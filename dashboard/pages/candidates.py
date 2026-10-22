import pandas as pd
import streamlit as st

import api_client as api
from ui import badge, fmt_dt, fmt_pct

st.title("Candidates & invitations")
st.caption("Verification codes are never shown here: only their status. Codes exist only in the candidate's email.")

tab_assign, tab_candidates = st.tabs(["Assignments & invitations", "Candidates"])

# ---------------------------------------------------------------- candidates
with tab_candidates:
    with st.form("new_candidate", clear_on_submit=True):
        c1, c2 = st.columns(2)
        name = c1.text_input("Full name")
        email = c2.text_input("Email")
        if st.form_submit_button("Add candidate", type="primary"):
            try:
                api.post("/api/candidates", {"name": name, "email": email})
                st.success(f"Added {name}.")
            except api.APIError as exc:
                api.show_error(exc)

    search = st.text_input("Search by name or email")
    try:
        candidates = api.get("/api/candidates", search=search or None)
    except api.APIError as exc:
        api.show_error(exc)
        st.stop()
    if candidates:
        st.dataframe(
            pd.DataFrame(
                [
                    {"ID": c["id"], "Name": c["name"], "Email": c["email"], "Active": c["is_active"], "Added": fmt_dt(c["created_at"])}
                    for c in candidates
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        st.markdown("#### Edit or delete a candidate")
        target = st.selectbox("Candidate", candidates, format_func=lambda c: f"#{c['id']} {c['name']} <{c['email']}>")

        with st.form(f"edit_candidate_{target['id']}"):
            c1, c2 = st.columns(2)
            new_name = c1.text_input("Full name", value=target["name"])
            new_email = c2.text_input("Email", value=target["email"])
            active = st.checkbox(
                "Active", value=target["is_active"],
                help="Inactive candidates cannot sign in or be assigned, but their results are kept.",
            )
            st.caption("If you change the email, any unused invitation sent to the old address stops working; resend it from the Assignments tab.")
            if st.form_submit_button("Save changes", type="primary"):
                changes = {}
                if new_name.strip() != target["name"]:
                    changes["name"] = new_name.strip()
                if new_email.strip().lower() != target["email"]:
                    changes["email"] = new_email.strip()
                if active != target["is_active"]:
                    changes["is_active"] = active
                if not changes:
                    st.info("Nothing changed.")
                else:
                    try:
                        api.patch(f"/api/candidates/{target['id']}", changes)
                        st.session_state["candidate_notice"] = f"Saved changes to {new_name.strip()}."
                        st.rerun()
                    except api.APIError as exc:
                        api.show_error(exc)

        with st.expander(f"Delete {target['name']}"):
            st.caption(
                "Only candidates who have never started an assessment can be deleted; their assignments and "
                "unused invitations are removed too. Anyone with results can only be deactivated, so results are kept."
            )
            confirm = st.checkbox(f"Yes, permanently delete {target['name']} <{target['email']}>", key=f"confirm_delete_{target['id']}")
            if st.button("Delete candidate", disabled=not confirm, key=f"delete_{target['id']}"):
                try:
                    api.delete(f"/api/candidates/{target['id']}")
                    st.session_state["candidate_notice"] = f"Deleted {target['name']}."
                    st.rerun()
                except api.APIError as exc:
                    api.show_error(exc)

        if notice := st.session_state.pop("candidate_notice", None):
            st.success(notice)
    else:
        st.info("No candidates found.")

# ---------------------------------------------------------------- assignments
with tab_assign:
    try:
        assessments = [a for a in api.get("/api/assessments") if a["status"] != "archived"]
        active_candidates = api.get("/api/candidates", active_only=True)
    except api.APIError as exc:
        api.show_error(exc)
        st.stop()

    if not assessments:
        st.info("Create an assessment first.")
        st.stop()

    assessment = st.selectbox(
        "Assessment", assessments, format_func=lambda a: f"#{a['id']} {a['title']} ({a['display_status']})"
    )
    if assessment["status"] != "published":
        st.warning("This assessment is a draft. You can assign candidates now, but invitations can only be sent after publishing.")

    with st.form("assign"):
        chosen = st.multiselect(
            "Assign candidates", active_candidates, format_func=lambda c: f"{c['name']} <{c['email']}>"
        )
        c1, c2 = st.columns(2)
        max_attempts = c1.number_input("Max attempts for these candidates", 1, 10, assessment["max_attempts"])
        send_now = c2.checkbox("Send invitations now", value=assessment["status"] == "published")
        if st.form_submit_button("Assign", type="primary"):
            if not chosen:
                st.error("Choose at least one candidate.")
            else:
                try:
                    res = api.post(
                        f"/api/assessments/{assessment['id']}/assign",
                        {"candidate_ids": [c["id"] for c in chosen], "max_attempts": int(max_attempts), "send_invitations": send_now},
                    )
                    st.success(f"{len(res['assignment_ids'])} assignment(s) saved.")
                    for inv in res["invitations"]:
                        if inv.get("error"):
                            st.error(f"Assignment {inv['assignment_id']}: {inv['error']}")
                        elif inv["email_status"] == "failed":
                            st.error(f"Assignment {inv['assignment_id']}: invitation email failed. Retry from Email status.")
                except api.APIError as exc:
                    api.show_error(exc)

    try:
        rows = api.get(f"/api/assessments/{assessment['id']}/assignments")
    except api.APIError as exc:
        api.show_error(exc)
        st.stop()
    if not rows:
        st.info("No candidates assigned yet.")
        st.stop()

    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Assignment": r["id"],
                    "Candidate": r["candidate_name"],
                    "Email": r["candidate_email"],
                    "Status": r["status"],
                    "Invitation": badge(r["invitation_status"]) if r["invitation_status"] else "not sent",
                    "Passcode": r["passcode_status"] or "-",
                    "Last email": badge(r["last_email_status"]),
                    "Attempts": f"{r['attempts_used']}/{r['max_attempts']}",
                    "Score": fmt_pct(r["percentage"]),
                    "Invite valid until": fmt_dt(r["invitation_expires_at"]),
                }
                for r in rows
            ]
        ),
        hide_index=True,
        width="stretch",
    )

    st.markdown("#### Send or resend an invitation")
    st.caption("Resending issues a new link and a new code; the previous link and code stop working immediately.")
    row = st.selectbox(
        "Assignment", rows, format_func=lambda r: f"#{r['id']} {r['candidate_name']} — {r['status']}", key="inv_row"
    )
    c1, c2 = st.columns(2)
    if c1.button("Send invitation", type="primary"):
        try:
            res = api.post(f"/api/assignments/{row['id']}/invite")
            (st.success if res["email_status"] in ("sent", "outbox") else st.error)(res["message"])
        except api.APIError as exc:
            api.show_error(exc)
    if row["status"] not in ("completed", "revoked") and c2.button("Revoke assignment"):
        try:
            api.post(f"/api/assignments/{row['id']}/revoke")
            st.rerun()
        except api.APIError as exc:
            api.show_error(exc)
