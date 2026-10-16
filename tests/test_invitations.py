from datetime import timedelta

from app.database import SessionLocal
from app.models import AuthSession, EmailLog, Invitation
from tests.helpers import (
    assign,
    create_assessment,
    create_candidate,
    csrf,
    now,
    setup_admin,
    update_invitation,
    verify,
)


def invited(client, mailbox, email="asha@example.com", **assessment_kwargs):
    headers = setup_admin(client)
    assessment = create_assessment(client, headers, **assessment_kwargs)
    cand = create_candidate(client, headers, email=email)
    assignment_id = assign(client, headers, assessment["id"], cand["id"])
    token, passcode = mailbox.credentials_for(email)
    return headers, assessment, cand, assignment_id, token, passcode


def wrong_code(passcode: str) -> str:
    return str((int(passcode) + 1) % 10**len(passcode)).zfill(len(passcode))


def test_invitation_email_contents(client, mailbox, settings):
    headers, assessment, cand, assignment_id, token, passcode = invited(client, mailbox)
    msg = mailbox.to(cand["email"])[-1]
    text = mailbox.text_of(msg)
    html = mailbox.html_of(msg)
    assert "Asha Rao" in text and assessment["title"] in text
    assert "Asia/Kolkata" in text and "45 minutes" in text
    assert f"{settings.public_base_url}/start#token=" in text
    assert "Start Assessment" in html
    assert len(passcode) == settings.passcode_length and passcode.isdigit()
    # The passcode is never part of the link.
    link = text.split("Start Assessment: ")[1].split()[0]
    assert passcode not in link
    assert token != passcode


def test_only_hashes_are_stored(client, mailbox, db):
    _, _, _, _, token, passcode = invited(client, mailbox)
    inv = db.query(Invitation).one()
    assert inv.invitation_token_hash != token and token not in inv.invitation_token_hash
    assert inv.passcode_hash != passcode and len(inv.passcode_hash) == 64
    log = db.query(EmailLog).one()
    assert token not in str(vars(log)) and passcode not in str(vars(log))


def test_valid_invitation_and_passcode(client, make_client, mailbox, db):
    _, assessment, cand, _, token, passcode = invited(client, mailbox)
    student = make_client()
    res = verify(student, token, passcode)
    assert res.status_code == 200
    assert student.cookies.get("af_student") and student.cookies.get("af_csrf")
    overview = student.get("/api/student/assessment").json()
    # The session is bound to the right student and assessment.
    assert overview["title"] == assessment["title"] and overview["candidate_name"] == cand["name"]
    inv = db.query(Invitation).one()
    assert inv.status == "verified" and inv.passcode_consumed_at is not None


def test_invalid_passcode(client, make_client, mailbox, db):
    *_, token, passcode = invited(client, mailbox)
    res = verify(make_client(), token, wrong_code(passcode))
    assert res.status_code == 400 and res.json()["code"] == "invalid_code"
    assert db.query(Invitation).one().failed_attempts == 1


def test_invalid_token(client, make_client, mailbox):
    *_, token, passcode = invited(client, mailbox)
    res = verify(make_client(), "x" * 43, passcode)
    assert res.status_code == 400 and res.json()["code"] == "invalid_invitation"


def test_expired_passcode(client, make_client, mailbox):
    _, _, _, assignment_id, token, passcode = invited(client, mailbox)
    update_invitation(assignment_id, passcode_expires_at=now() - timedelta(seconds=1))
    res = verify(make_client(), token, passcode)
    assert res.status_code == 400 and res.json()["code"] == "code_expired"


def test_expired_invitation(client, make_client, mailbox, db):
    _, _, _, assignment_id, token, passcode = invited(client, mailbox)
    update_invitation(assignment_id, invitation_expires_at=now() - timedelta(seconds=1))
    res = verify(make_client(), token, passcode)
    assert res.status_code == 400 and res.json()["code"] == "invalid_invitation"
    assert db.query(Invitation).one().status == "expired"


def test_reused_passcode_is_rejected(client, make_client, mailbox):
    *_, token, passcode = invited(client, mailbox)
    assert verify(make_client(), token, passcode).status_code == 200
    replay = verify(make_client(), token, passcode)
    assert replay.status_code == 400 and replay.json()["code"] == "code_used"


def test_excessive_failed_attempts_lock_the_invitation(client, make_client, mailbox, settings, db):
    *_, token, passcode = invited(client, mailbox)
    student = make_client()
    for _ in range(settings.passcode_max_attempts - 1):
        assert verify(student, token, wrong_code(passcode)).status_code == 400
    locked = verify(student, token, wrong_code(passcode))
    assert locked.status_code == 429 and locked.json()["code"] == "locked"
    # Even the correct code no longer works: it was burned when the lock engaged.
    assert verify(student, token, passcode).status_code == 429
    inv = db.query(Invitation).one()
    assert inv.locked_until is not None and inv.passcode_hash is None


def test_resend_code_invalidates_previous_code(client, make_client, mailbox):
    _, _, cand, _, token, old_code = invited(client, mailbox)
    student = make_client()
    res = student.post("/api/invitations/resend-code", json={"token": token})
    assert res.status_code == 200
    new_code = mailbox.latest_code_for(cand["email"])
    if new_code == old_code:  # 1-in-a-million collision; request again
        student.post("/api/invitations/resend-code", json={"token": token})
        new_code = mailbox.latest_code_for(cand["email"])
    assert verify(student, token, old_code).status_code == 400
    assert verify(student, token, new_code).status_code == 200


def test_resend_code_is_generic_for_unknown_tokens(client, make_client, mailbox):
    invited(client, mailbox)
    sent_before = len(mailbox.messages)
    res = make_client().post("/api/invitations/resend-code", json={"token": "y" * 43})
    assert res.status_code == 200
    assert "If this invitation is still valid" in res.json()["message"]
    assert len(mailbox.messages) == sent_before


def test_admin_resend_revokes_old_invitation_and_sessions(client, make_client, mailbox, db):
    headers, _, cand, assignment_id, token, passcode = invited(client, mailbox)
    student = make_client()
    assert verify(student, token, passcode).status_code == 200
    assert student.get("/api/student/assessment").status_code == 200

    res = client.post(f"/api/assignments/{assignment_id}/invite", headers=headers)
    assert res.status_code == 200 and res.json()["email_status"] == "sent"
    # Old session and old link stop working; the new credentials work.
    assert student.get("/api/student/assessment").status_code == 401
    new_token, new_code = mailbox.credentials_for(cand["email"])
    assert new_token != token
    assert verify(make_client(), token, passcode).status_code == 400
    assert verify(make_client(), new_token, new_code).status_code == 200
    statuses = sorted(i.status for i in db.query(Invitation).all())
    assert statuses == ["revoked", "verified"]


def test_invitation_belongs_to_correct_student_and_assessment(client, make_client, mailbox):
    headers = setup_admin(client)
    first = create_assessment(client, headers, title="Quiz One")
    second = create_assessment(client, headers, title="Quiz Two")
    a = create_candidate(client, headers, "Asha Rao", "asha@example.com")
    b = create_candidate(client, headers, "Ben Lee", "ben@example.com")
    assign(client, headers, first["id"], a["id"])
    assign(client, headers, second["id"], b["id"])
    token_a, code_a = mailbox.credentials_for(a["email"])
    token_b, code_b = mailbox.credentials_for(b["email"])
    # A's code does not unlock B's invitation.
    assert verify(make_client(), token_b, code_a).status_code == 400
    sa = make_client()
    assert verify(sa, token_a, code_a).status_code == 200
    overview = sa.get("/api/student/assessment").json()
    assert overview["title"] == "Quiz One" and overview["candidate_name"] == "Asha Rao"


def test_cannot_invite_for_draft_assessment(client, mailbox):
    headers = setup_admin(client)
    draft = create_assessment(client, headers, publish=False)
    cand = create_candidate(client, headers)
    assignment_id = assign(client, headers, draft["id"], cand["id"], invite=False)
    res = client.post(f"/api/assignments/{assignment_id}/invite", headers=headers)
    assert res.status_code == 409
    assert mailbox.messages == []


def test_inactive_candidate_cannot_verify(client, make_client, mailbox):
    headers, _, cand, _, token, passcode = invited(client, mailbox)
    client.patch(f"/api/candidates/{cand['id']}", json={"is_active": False}, headers=headers)
    assert verify(make_client(), token, passcode).status_code == 400


def test_student_logout_revokes_session(client, make_client, mailbox, db):
    *_, token, passcode = invited(client, mailbox)
    student = make_client()
    verify(student, token, passcode)
    cookie = student.cookies.get("af_student")
    student.post("/api/student/logout", headers=csrf(student))
    student.cookies.set("af_student", cookie)
    assert student.get("/api/student/assessment").status_code == 401
    assert db.query(AuthSession).filter(AuthSession.revoked_at.is_(None), AuthSession.kind == "student").count() == 0
