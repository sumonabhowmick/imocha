"""Student portal: email-code sign-in, own assessments only, taking a test, history."""
from __future__ import annotations

import re

from sqlalchemy import select

from app.models import LoginCode
from tests.conftest import ADMIN_JSON, STUDENT_JSON
from tests.helpers import assign, create_assessment, create_candidate, setup_admin, verify


def code_from(mailbox, address: str) -> str:
    msgs = [m for m in mailbox.to(address) if "sign-in code" in m["Subject"].lower()]
    assert msgs, f"no sign-in email for {address}"
    return re.search(r"portal is: (\d+)", mailbox.text_of(msgs[-1])).group(1)


def portal_login(make_client, mailbox, email):
    browser = make_client()
    assert browser.post("/api/portal/login/request-code", json={"email": email}).status_code == 200
    res = browser.post("/api/portal/login/verify", json={"email": email, "code": code_from(mailbox, email)})
    assert res.status_code == 200, res.text
    return browser


def csrf(browser) -> dict:
    return {"X-CSRF-Token": browser.cookies.get("af_csrf")}


def setup_student(client, headers, email="asha@example.com", name="Asha Rao", **assessment_kwargs):
    assessment = create_assessment(client, headers, **assessment_kwargs)
    candidate = create_candidate(client, headers, name=name, email=email)
    assignment_id = assign(client, headers, assessment["id"], candidate["id"], invite=False)
    return assessment, candidate, assignment_id


# ---------------------------------------------------------------------------
# Sign-in
# ---------------------------------------------------------------------------
def test_request_code_is_generic_and_only_emails_real_candidates(client, make_client, mailbox):
    headers = setup_admin(client)
    create_candidate(client, headers, email="asha@example.com")
    browser = make_client()
    known = browser.post("/api/portal/login/request-code", json={"email": "ASHA@example.com"}).json()
    unknown = browser.post("/api/portal/login/request-code", json={"email": "nobody@example.com"}).json()
    assert known == unknown  # same response: no account discovery
    assert len(mailbox.to("asha@example.com")) == 1 and mailbox.to("nobody@example.com") == []


def test_login_with_code_then_code_cannot_be_reused(client, make_client, mailbox):
    headers = setup_admin(client)
    create_candidate(client, headers, email="asha@example.com", name="Asha Rao")
    browser = make_client()
    browser.post("/api/portal/login/request-code", json={"email": "asha@example.com"})
    code = code_from(mailbox, "asha@example.com")
    res = browser.post("/api/portal/login/verify", json={"email": "asha@example.com", "code": code})
    assert res.status_code == 200 and res.json()["name"] == "Asha Rao"
    assert browser.get("/api/portal/me").json()["email"] == "asha@example.com"
    again = make_client().post("/api/portal/login/verify", json={"email": "asha@example.com", "code": code})
    assert again.status_code == 400


def test_wrong_codes_burn_the_code(client, make_client, mailbox, settings, db):
    headers = setup_admin(client)
    create_candidate(client, headers, email="asha@example.com")
    browser = make_client()
    browser.post("/api/portal/login/request-code", json={"email": "asha@example.com"})
    code = code_from(mailbox, "asha@example.com")
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(settings.passcode_max_attempts):
        assert browser.post("/api/portal/login/verify", json={"email": "asha@example.com", "code": wrong}).status_code == 400
    assert browser.post("/api/portal/login/verify", json={"email": "asha@example.com", "code": code}).status_code == 400
    assert db.scalar(select(LoginCode.code_hash)) != code  # only a hash is stored


def test_inactive_candidate_cannot_sign_in(client, make_client, mailbox):
    headers = setup_admin(client)
    c = create_candidate(client, headers, email="asha@example.com")
    client.patch(f"/api/candidates/{c['id']}", json={"is_active": False}, headers=headers)
    make_client().post("/api/portal/login/request-code", json={"email": "asha@example.com"})
    assert mailbox.to("asha@example.com") == []


def test_invitation_link_also_opens_the_portal(client, make_client, mailbox):
    headers = setup_admin(client)
    assessment = create_assessment(client, headers)
    c = create_candidate(client, headers, email="asha@example.com")
    assignment_id = assign(client, headers, assessment["id"], c["id"])
    student = make_client()
    res = verify(student, *mailbox.credentials_for("asha@example.com"))
    assert res.json()["assignment_id"] == assignment_id
    items = student.get("/api/portal/assessments").json()
    assert [i["assignment_id"] for i in items] == [assignment_id]


# ---------------------------------------------------------------------------
# Taking a test through the portal
# ---------------------------------------------------------------------------
def test_full_portal_flow_and_history(client, make_client, mailbox, fake_ai):
    headers = setup_admin(client)
    assessment, _c, assignment_id = setup_student(client, headers)
    browser = portal_login(make_client, mailbox, "asha@example.com")

    items = browser.get("/api/portal/assessments").json()
    assert items[0]["state"] == "open" and items[0]["can_start"] is True
    attempt = browser.post(f"/api/portal/assessments/{assignment_id}/attempts", json={"proctoring_consent": True}, headers=csrf(browser)).json()
    qs = browser.get(f"/api/portal/attempts/{attempt['attempt_id']}/questions").json()["questions"]
    assert all("correct_answer" not in q for q in qs)
    by_text = {q["question_text"]: q["id"] for q in qs}
    first = by_text[assessment["questions"][0]["question_text"]]
    assert browser.put(f"/api/portal/attempts/{attempt['attempt_id']}/answers/{first}", json={"selected": "B"}, headers=csrf(browser)).status_code == 200
    sub = browser.post(f"/api/portal/attempts/{attempt['attempt_id']}/submit", headers=csrf(browser))
    assert sub.status_code == 200 and sub.json()["already_submitted"] is False

    history = browser.get("/api/portal/history").json()
    assert len(history) == 1 and history[0]["result"]["percentage"] == 40.0
    result = browser.get(f"/api/portal/results/{history[0]['result']['result_id']}").json()
    assert result["category"] == "Adaptive"
    assert result["feedback"]["summary"] == STUDENT_JSON["summary"]
    text = str(result)
    assert ADMIN_JSON["overview"] not in text and "coaching" not in text.lower()  # never the admin report
    assert "correct_answer" not in text
    items = browser.get("/api/portal/assessments").json()
    assert items[0]["state"] == "completed" and items[0]["latest_result"]["percentage"] == 40.0


def test_students_only_see_their_own_data(client, make_client, mailbox):
    headers = setup_admin(client)
    _a, _c, asha_assignment = setup_student(client, headers, email="asha@example.com")
    ravi = create_candidate(client, headers, name="Ravi", email="ravi@example.com")
    ravi_assignment = assign(client, headers, _a["id"], ravi["id"], invite=False)

    asha = portal_login(make_client, mailbox, "asha@example.com")
    ravi_browser = portal_login(make_client, mailbox, "ravi@example.com")
    attempt = ravi_browser.post(f"/api/portal/assessments/{ravi_assignment}/attempts", json={"proctoring_consent": True}, headers=csrf(ravi_browser)).json()

    assert asha.get(f"/api/portal/assessments/{ravi_assignment}").status_code == 404
    assert asha.post(f"/api/portal/assessments/{ravi_assignment}/attempts", json={"proctoring_consent": True}, headers=csrf(asha)).status_code == 404
    assert asha.get(f"/api/portal/attempts/{attempt['attempt_id']}/questions").status_code == 404
    assert asha.post(f"/api/portal/attempts/{attempt['attempt_id']}/submit", headers=csrf(asha)).status_code == 404
    assert [i["assignment_id"] for i in asha.get("/api/portal/assessments").json()] == [asha_assignment]


def test_portal_requires_login_and_csrf(client, make_client, mailbox):
    headers = setup_admin(client)
    _a, _c, assignment_id = setup_student(client, headers)
    assert make_client().get("/api/portal/assessments").status_code == 401
    browser = portal_login(make_client, mailbox, "asha@example.com")
    assert browser.post(f"/api/portal/assessments/{assignment_id}/attempts", json={}).status_code == 403
    assert browser.get("/api/admin/results").status_code == 401  # never admin access


def test_portal_lists_most_recent_past_assessments_first(client, make_client, mailbox, db):
    from datetime import timedelta

    from sqlalchemy import update

    from app.models import Assessment
    from tests.helpers import now

    headers = setup_admin(client)
    c = create_candidate(client, headers, email="asha@example.com")
    ids = {}
    for title in ("Older closed", "Newer closed", "Open later", "Open soon"):
        a = create_assessment(client, headers, title=title)
        assign(client, headers, a["id"], c["id"], invite=False)
        ids[title] = a["id"]
    windows = {
        "Older closed": (now() - timedelta(days=10), now() - timedelta(days=9)),
        "Newer closed": (now() - timedelta(days=3), now() - timedelta(days=2)),
        "Open later": (now() - timedelta(hours=1), now() + timedelta(hours=5)),
        "Open soon": (now() - timedelta(hours=1), now() + timedelta(hours=1)),
    }
    for title, (start, end) in windows.items():
        db.execute(update(Assessment).where(Assessment.id == ids[title]).values(access_start_at=start, access_end_at=end))
    db.commit()

    browser = portal_login(make_client, mailbox, "asha@example.com")
    titles = [i["title"] for i in browser.get("/api/portal/assessments").json()]
    assert titles == ["Open soon", "Open later", "Newer closed", "Older closed"]


def test_drafts_are_hidden_and_logout_works(client, make_client, mailbox):
    headers = setup_admin(client)
    draft = create_assessment(client, headers, publish=False)
    c = create_candidate(client, headers, email="asha@example.com")
    assign(client, headers, draft["id"], c["id"], invite=False)
    browser = portal_login(make_client, mailbox, "asha@example.com")
    assert browser.get("/api/portal/assessments").json() == []
    browser.post("/api/portal/logout")
    assert browser.get("/api/portal/me").status_code == 401
