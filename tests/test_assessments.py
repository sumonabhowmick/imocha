from datetime import timedelta

from tests.helpers import (
    MCQ_BASICS,
    MCQ_EXCEPTIONS,
    TEXT_Q,
    create_assessment,
    full_flow,
    iso,
    now,
    setup_admin,
)


def test_create_and_publish_assessment(client):
    headers = setup_admin(client)
    a = create_assessment(client, headers, publish=False)
    assert a["status"] == "draft" and a["question_count"] == 2 and a["total_points"] == 5
    res = client.post(f"/api/assessments/{a['id']}/publish", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "published" and body["display_status"] == "active"


def test_display_status_scheduled(client):
    headers = setup_admin(client)
    a = create_assessment(client, headers, start=now() + timedelta(days=1), end=now() + timedelta(days=1, hours=2))
    assert a["display_status"] == "scheduled"


def test_publish_requires_questions_and_window(client):
    headers = setup_admin(client)
    empty = create_assessment(client, headers, questions=[], publish=False)
    assert client.post(f"/api/assessments/{empty['id']}/publish", headers=headers).status_code == 400
    no_window = client.post("/api/assessments", json={"title": "No window"}, headers=headers).json()
    client.post(f"/api/assessments/{no_window['id']}/questions", json=MCQ_BASICS, headers=headers)
    res = client.post(f"/api/assessments/{no_window['id']}/publish", headers=headers)
    assert res.status_code == 400 and "access window" in res.json()["detail"]


def test_invalid_window_rejected(client):
    headers = setup_admin(client)
    t = now()
    res = client.post(
        "/api/assessments",
        json={"title": "Bad", "access_start_at": iso(t + timedelta(hours=2)), "access_end_at": iso(t)},
        headers=headers,
    )
    assert res.status_code == 400
    naive = client.post(
        "/api/assessments",
        json={"title": "Naive", "access_start_at": "2030-01-01T10:00:00", "access_end_at": "2030-01-01T12:00:00"},
        headers=headers,
    )
    assert naive.status_code == 422


def test_question_validation(client):
    headers = setup_admin(client)
    a = create_assessment(client, headers, questions=[], publish=False)
    bad_correct = {**MCQ_BASICS, "correct_answer": "Z"}
    assert client.post(f"/api/assessments/{a['id']}/questions", json=bad_correct, headers=headers).status_code == 422
    one_option = {**MCQ_BASICS, "options": [{"id": "A", "text": "x"}], "correct_answer": "A"}
    assert client.post(f"/api/assessments/{a['id']}/questions", json=one_option, headers=headers).status_code == 422
    negative = {**MCQ_BASICS, "points": -1}
    assert client.post(f"/api/assessments/{a['id']}/questions", json=negative, headers=headers).status_code == 422


def test_edit_delete_reorder_questions_in_draft(client):
    headers = setup_admin(client)
    a = create_assessment(client, headers, questions=[MCQ_BASICS, MCQ_EXCEPTIONS, TEXT_Q], publish=False)
    q1, q2, q3 = (q["id"] for q in a["questions"])
    res = client.patch(f"/api/questions/{q1}", json={"points": 5, "question_text": "Updated?"}, headers=headers)
    assert res.status_code == 200 and res.json()["points"] == 5
    assert client.delete(f"/api/questions/{q2}", headers=headers).status_code == 204
    res = client.post(f"/api/assessments/{a['id']}/questions/reorder", json={"question_ids": [q3, q1]}, headers=headers)
    assert [q["id"] for q in res.json()["questions"]] == [q3, q1]


def test_published_questions_are_locked(client):
    headers = setup_admin(client)
    a = create_assessment(client, headers)
    q = a["questions"][0]["id"]
    assert client.patch(f"/api/questions/{q}", json={"points": 10}, headers=headers).status_code == 409
    assert client.delete(f"/api/questions/{q}", headers=headers).status_code == 409
    assert client.post(f"/api/assessments/{a['id']}/questions", json=MCQ_BASICS, headers=headers).status_code == 409
    # Settings that do not change grading can still be edited.
    res = client.patch(f"/api/assessments/{a['id']}", json={"title": "Renamed"}, headers=headers)
    assert res.status_code == 200 and res.json()["title"] == "Renamed"
    # Grading-related settings are locked.
    scale = [{"label": "All", "min": 0, "max": 100}]
    assert client.patch(f"/api/assessments/{a['id']}", json={"category_scale": scale}, headers=headers).status_code == 409


def test_duplicate_creates_editable_draft(client):
    headers = setup_admin(client)
    a = create_assessment(client, headers)
    copy = client.post(f"/api/assessments/{a['id']}/duplicate", headers=headers).json()
    assert copy["status"] == "draft" and copy["question_count"] == 2 and copy["id"] != a["id"]
    assert copy["access_start_at"] is None
    q = copy["questions"][0]["id"]
    assert client.patch(f"/api/questions/{q}", json={"points": 9}, headers=headers).status_code == 200


def test_preview_hides_answers(client):
    headers = setup_admin(client)
    a = create_assessment(client, headers, questions=[MCQ_BASICS, TEXT_Q], publish=False)
    res = client.post(f"/api/assessments/{a['id']}/preview", headers=headers)
    assert res.status_code == 200
    assert "correct_answer" not in res.text and "grading_rubric" not in res.text


def test_historical_results_stable_after_assessment_edit(client, make_client, mailbox):
    headers = setup_admin(client)
    flow = full_flow(client, headers, make_client, mailbox, answers={0: {"selected": "B"}, 1: {"selected": "A"}})
    before = client.get("/api/admin/results", headers=headers).json()["items"][0]
    aid = flow["assessment"]["id"]
    # Allowed post-publication edits do not alter the stored result...
    client.patch(f"/api/assessments/{aid}", json={"title": "Renamed", "duration_minutes": 90}, headers=headers)
    # ...and question edits are refused outright.
    q = flow["assessment"]["questions"][0]["id"]
    assert client.patch(f"/api/questions/{q}", json={"correct_answer": "A"}, headers=headers).status_code == 409
    after = client.get(f"/api/admin/results/{before['result_id']}", headers=headers).json()
    assert after["raw_score"] == before["raw_score"] == 2
    assert after["max_score"] == before["max_score"] == 5
    assert after["questions"][0]["outcome"] == "correct"


def test_candidates_and_duplicate_assignment(client):
    headers = setup_admin(client)
    a = create_assessment(client, headers)
    c = client.post("/api/candidates", json={"name": "Asha", "email": "Asha@Example.com"}, headers=headers).json()
    assert c["email"] == "asha@example.com"
    dup = client.post("/api/candidates", json={"name": "Asha 2", "email": "asha@example.com"}, headers=headers)
    assert dup.status_code == 409
    body = {"candidate_ids": [c["id"], c["id"]]}
    first = client.post(f"/api/assessments/{a['id']}/assign", json=body, headers=headers).json()
    second = client.post(f"/api/assessments/{a['id']}/assign", json=body, headers=headers).json()
    assert first["assignment_ids"] == second["assignment_ids"] and len(first["assignment_ids"]) == 1
    rows = client.get(f"/api/assessments/{a['id']}/assignments", headers=headers).json()
    assert len(rows) == 1 and rows[0]["passcode_status"] is None


def test_assignment_rows_never_include_codes(client, mailbox):
    headers = setup_admin(client)
    a = create_assessment(client, headers)
    c = client.post("/api/candidates", json={"name": "Asha", "email": "asha@example.com"}, headers=headers).json()
    client.post(f"/api/assessments/{a['id']}/assign", json={"candidate_ids": [c["id"]], "send_invitations": True}, headers=headers)
    token, passcode = mailbox.credentials_for("asha@example.com")
    res = client.get(f"/api/assessments/{a['id']}/assignments", headers=headers)
    assert res.json()[0]["passcode_status"] == "pending"
    assert passcode not in res.text and token not in res.text
