"""Editing and deleting candidates."""
from __future__ import annotations

from sqlalchemy import func, select

from app.models import AssessmentAssignment, Candidate, EmailLog, Invitation, Result
from tests.helpers import assign, create_assessment, create_candidate, full_flow, setup_admin, verify


def test_edit_name_email_and_status(client):
    headers = setup_admin(client)
    c = create_candidate(client, headers, name="Asha Rao", email="asha@example.com")
    res = client.patch(f"/api/candidates/{c['id']}", json={"name": "Asha R. Rao", "email": "Asha.New@Example.com", "is_active": False}, headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["name"] == "Asha R. Rao" and body["email"] == "asha.new@example.com" and body["is_active"] is False


def test_email_must_stay_unique_and_valid(client):
    headers = setup_admin(client)
    a = create_candidate(client, headers, name="A", email="a@example.com")
    create_candidate(client, headers, name="B", email="b@example.com")
    assert client.patch(f"/api/candidates/{a['id']}", json={"email": "B@example.com"}, headers=headers).status_code == 409
    assert client.patch(f"/api/candidates/{a['id']}", json={"email": "not-an-email"}, headers=headers).status_code == 422


def test_email_change_revokes_unused_invitation(client, make_client, mailbox, db):
    headers = setup_admin(client)
    assessment = create_assessment(client, headers)
    c = create_candidate(client, headers, email="typo@example.com")
    assign(client, headers, assessment["id"], c["id"])
    token, passcode = mailbox.credentials_for("typo@example.com")

    client.patch(f"/api/candidates/{c['id']}", json={"email": "right@example.com"}, headers=headers)
    # The link sent to the wrong address no longer works.
    assert verify(make_client(), token, passcode).status_code == 400
    assert db.scalar(select(Invitation.status)) == "revoked"
    # Resending reaches the new address.
    assignment_id = db.scalar(select(AssessmentAssignment.id))
    assert client.post(f"/api/assignments/{assignment_id}/invite", headers=headers).status_code == 200
    assert len(mailbox.to("right@example.com")) == 1


def test_email_change_does_not_interrupt_a_verified_student(client, make_client, mailbox, db):
    headers = setup_admin(client)
    assessment = create_assessment(client, headers)
    c = create_candidate(client, headers, email="asha@example.com")
    assign(client, headers, assessment["id"], c["id"])
    student = make_client()
    assert verify(student, *mailbox.credentials_for("asha@example.com")).status_code == 200

    client.patch(f"/api/candidates/{c['id']}", json={"email": "asha2@example.com"}, headers=headers)
    assert student.get("/api/student/assessment").status_code == 200


def test_delete_candidate_never_assigned(client, db):
    headers = setup_admin(client)
    c = create_candidate(client, headers)
    assert client.delete(f"/api/candidates/{c['id']}", headers=headers).status_code == 204
    assert db.get(Candidate, c["id"]) is None
    assert client.delete(f"/api/candidates/{c['id']}", headers=headers).status_code == 404


def test_delete_assigned_but_not_started_removes_assignment_and_invitation(client, make_client, mailbox, db):
    headers = setup_admin(client)
    assessment = create_assessment(client, headers)
    c = create_candidate(client, headers)
    assign(client, headers, assessment["id"], c["id"])
    token, passcode = mailbox.credentials_for(c["email"])

    assert client.delete(f"/api/candidates/{c['id']}", headers=headers).status_code == 204
    assert db.scalar(select(func.count(AssessmentAssignment.id))) == 0
    assert db.scalar(select(func.count(Invitation.id))) == 0
    log = db.scalar(select(EmailLog))  # delivery record kept, unlinked
    assert log is not None and log.related_assignment_id is None
    assert verify(make_client(), token, passcode).status_code == 400


def test_candidate_with_results_cannot_be_deleted(client, make_client, mailbox, db):
    headers = setup_admin(client)
    flow = full_flow(client, headers, make_client, mailbox, answers={0: {"selected": "B"}})
    res = client.delete(f"/api/candidates/{flow['candidate']['id']}", headers=headers)
    assert res.status_code == 409 and "Deactivate" in res.json()["detail"]
    assert db.get(Candidate, flow["candidate"]["id"]) is not None
    assert db.scalar(select(func.count(Result.id))) == 1


def test_edit_and_delete_require_admin(client):
    headers = setup_admin(client)
    c = create_candidate(client, headers)
    assert client.patch(f"/api/candidates/{c['id']}", json={"name": "X"}).status_code == 401
    assert client.delete(f"/api/candidates/{c['id']}").status_code == 401
