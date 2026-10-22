import pandas as pd
import plotly.express as px
import streamlit as st

import api_client as api
from ui import badge, fmt_pct

st.title("Overview")

try:
    data = api.get("/api/admin/overview")
    health = api.get("/api/health")
except api.APIError as exc:
    api.show_error(exc)
    st.stop()

if health.get("demo_mode"):
    st.warning("DEMO MODE is on. Records labelled [DEMO] are demonstration data, not real assessment results.")
if not health.get("ai_configured"):
    st.info("OpenAI is not configured, so AI feedback cannot be generated yet. Set OPENAI_API_KEY and OPENAI_MODEL in .env.")
if health.get("email_backend") == "outbox":
    st.info("EMAIL_BACKEND=outbox: emails are saved to the outbox folder instead of being sent (development only).")
elif not health.get("smtp_configured"):
    st.warning("SMTP is not configured; emails will fail until SMTP settings are added to .env.")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Assessments", data["total_assessments"], help=f"{data['published_assessments']} published")
c2.metric("Candidates", data["total_candidates"])
c3.metric("Invitations issued", data["total_invitations"])
c4.metric("Attempts started", data["started_attempts"])
c5, c6, c7, c8 = st.columns(4)
c5.metric("Completed assessments", data["completed_assessments"])
c6.metric("Pending assessments", data["pending_assessments"])
c7.metric("Average score (final results)", fmt_pct(data["average_score_percent"]))
c8.metric("Results awaiting review", data["results_needing_review"])

if data["failed_emails"] or data["failed_feedback"]:
    st.error(
        f"Needs attention: {data['failed_emails']} failed email(s), "
        f"{data['failed_feedback']} failed feedback report(s). See Email status and Feedback reports."
    )

left, right = st.columns(2)
with left:
    st.subheader("Assignment status")
    counts = data["assignment_status_counts"]
    if counts:
        df = pd.DataFrame({"status": [badge(k) for k in counts], "count": list(counts.values())})
        st.plotly_chart(px.bar(df, x="status", y="count", text="count"), width="stretch")
    else:
        st.caption("No assignments yet.")
with right:
    st.subheader("Performance categories")
    cats = data["category_counts"]
    if cats:
        df = pd.DataFrame({"category": list(cats), "count": list(cats.values())})
        st.plotly_chart(px.pie(df, names="category", values="count", hole=0.45), width="stretch")
        st.caption("Categories describe results on a specific assessment, not overall ability.")
    else:
        st.caption("No final results yet.")
