import pandas as pd
import streamlit as st

import api_client as api
from ui import badge, fmt_dt

st.title("Email status")
st.caption(
    "Delivery log for every notification. Message bodies, verification codes and links are never stored. "
    "Retrying an invitation issues a fresh link and code (old ones stop working)."
)

status = st.selectbox("Filter", [None, "failed", "sent", "outbox", "queued"], format_func=lambda s: s or "All")
try:
    logs = api.get("/api/admin/email-logs", status=status)
except api.APIError as exc:
    api.show_error(exc)
    st.stop()

if not logs:
    st.info("No emails yet.")
    st.stop()

st.dataframe(
    pd.DataFrame(
        [
            {
                "ID": log["id"],
                "Type": log["notification_type"],
                "Recipient": log["recipient_email"],
                "Subject": log["subject"],
                "Status": badge(log["status"]),
                "Tries": log["attempts"],
                "Last error": log["last_error"] or "",
                "Sent": fmt_dt(log["sent_at"]),
                "Created": fmt_dt(log["created_at"]),
            }
            for log in logs
        ]
    ),
    hide_index=True,
    use_container_width=True,
)

failed = [log for log in logs if log["status"] in ("failed", "queued")]
if failed:
    st.markdown("#### Retry a failed email")
    target = st.selectbox(
        "Email", failed, format_func=lambda l: f"#{l['id']} {l['notification_type']} → {l['recipient_email']} ({l['last_error'] or l['status']})"
    )
    if st.button("Retry", type="primary"):
        try:
            res = api.post(f"/api/admin/email-logs/{target['id']}/retry")
            (st.success if res["status"] in ("sent", "outbox") else st.error)(
                f"Status: {res['status']}" + (f" — {res['error']}" if res.get("error") else "")
            )
        except api.APIError as exc:
            api.show_error(exc)
