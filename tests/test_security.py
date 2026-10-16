"""Privacy and access-control tests: students only ever reach their own data."""
import json

from tests.helpers import (
    answer,
    assign,
    create_assessment,
    create_candidate,
    csrf,
    setup_admin,
    start,
    student_session,
    submit,
)


def two_students(client, make_client, mailbox):
    headers = setup_admin(client)
    assessment = create_assessment(client, headers)
    a = create_candidate(client, headers, "Asha Rao", "asha@example.com")
    b = create_candidate(client, headers, "Ben Lee", "ben@example.com")
    assign(client, headers, assessment["id"], a["id"])
    assign(client, headers, assessment["id"], b["id"])
    sa = student_session(make_client, mailbox, a["email"])
    sb = student_session(make_client, mailbox, b["email"])
    return headers, assessment, sa, sb


def test_unauthenticated_users_cannot_get_questions(client):
    assert client.get("/api/student/assessment").status_code == 401
    assert client.get("/api/student/attempts/1/questions").status_code == 401
    assert client.post("/api/student/attempts").status_code in (401, 403)


def test_question_delivery_never_exposes_answers(client, make_client, mailbox):
    headers, assessment, sa, _ = two_students(client, make_client, mailbox)
    attempt_id = start(sa).json()["attempt_id"]
    res = sa.get(f"/api/student/attempts/{attempt_id}/questions")
    assert res.status_code == 200
    raw = res.text
    for q in res.json()["questions"]:
        assert set(q) <= {"id", "number", "question_text", "question_type", "points", "options", "saved_answer"}
        for option in q["options"] or []:
            assert set(option) == {"id", "text"}
    assert "correct_answer" not in raw and "grading_rubric" not in raw and "explanation" not in raw


def test_student_cannot_view_or_change_another_students_attempt(client, make_client, mailbox):
    headers, assessment, sa, sb = two_students(client, make_client, mailbox)
    attempt_a = start(sa).json()["attempt_id"]
    start(sb)
    q = assessment["questions"][0]["id"]
    # Student B uses student A's attempt id: indistinguishable from a missing attempt.
    assert sb.get(f"/api/student/attempts/{attempt_a}/questions").status_code == 404
    assert sb.get(f"/api/student/attempts/{attempt_a}/status").status_code == 404
    assert answer(sb, attempt_a, q, selected="A").status_code == 404
    assert submit(sb, attempt_a).status_code == 404
    # A's attempt is untouched.
    questions = sa.get(f"/api/student/attempts/{attempt_a}/questions").json()["questions"]
    assert all(item["saved_answer"] is None for item in questions)


def test_student_cannot_answer_question_from_another_assessment(client, make_client, mailbox):
    headers, assessment, sa, _ = two_students(client, make_client, mailbox)
    other = create_assessment(client, headers, title="Other quiz")
    attempt_id = start(sa).json()["attempt_id"]
    assert answer(sa, attempt_id, other["questions"][0]["id"], selected="B").status_code == 404


def test_student_cannot_access_admin_feedback_or_results(client, make_client, mailbox):
    headers, assessment, sa, _ = two_students(client, make_client, mailbox)
    attempt_id = start(sa).json()["attempt_id"]
    submit(sa, attempt_id)
    for path in ("/api/admin/results", "/api/admin/results/1", "/api/admin/results/1/feedback"):
        assert sa.get(path).status_code == 401
    status = sa.get(f"/api/student/attempts/{attempt_id}/status").json()
    assert "admin" not in json.dumps(status).lower()


def test_csrf_token_required_for_student_writes(client, make_client, mailbox):
    headers, assessment, sa, _ = two_students(client, make_client, mailbox)
    assert sa.post("/api/student/attempts").status_code == 403
    assert sa.post("/api/student/attempts", headers={"X-CSRF-Token": "forged"}).status_code == 403
    assert sa.post("/api/student/attempts", headers=csrf(sa)).status_code == 200


def test_student_session_cookie_flags(client, make_client, mailbox):
    from tests.helpers import verify

    headers = setup_admin(client)
    assessment = create_assessment(client, headers)
    cand = create_candidate(client, headers)
    assign(client, headers, assessment["id"], cand["id"])
    token, passcode = mailbox.credentials_for(cand["email"])
    res = verify(make_client(), token, passcode)
    cookies = res.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith("af_student="))
    assert "HttpOnly" in session_cookie and "SameSite=strict" in session_cookie.replace("Strict", "strict")
    assert passcode not in res.text and token not in res.text


def test_validation_errors_do_not_echo_input(client):
    res = client.post("/api/invitations/verify", json={"token": "x" * 20, "passcode": "12ab"})
    assert res.status_code == 422
    assert "12ab" not in res.text


def test_security_headers_present(client):
    res = client.get("/start")
    assert res.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in res.headers["Content-Security-Policy"]
    assert res.headers["X-Content-Type-Options"] == "nosniff"
