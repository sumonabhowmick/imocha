import pandas as pd
import plotly.express as px
import streamlit as st

import api_client as api
from ui import bullet_list, fmt_dt, fmt_pct

st.title("Feedback reports")
st.caption("AI-generated reports are based only on verified scores computed by the application. Admin reports are never shown to students.")

tab_student, tab_admin, tab_group = st.tabs(["Student feedback", "Admin coaching report", "Group analytics"])

try:
    results = api.get("/api/admin/results", result_status="final", limit=500)["items"]
    assessments = api.get("/api/assessments")
except api.APIError as exc:
    api.show_error(exc)
    st.stop()


def pick_result(key: str):
    if not results:
        st.info("No final results yet.")
        return None, None
    r = st.selectbox(
        "Result", results, key=key,
        format_func=lambda r: f"#{r['result_id']} {r['candidate_name']} — {r['assessment_title']} ({fmt_pct(r['percentage'])})",
    )
    try:
        return r, api.get(f"/api/admin/results/{r['result_id']}/feedback")
    except api.APIError as exc:
        api.show_error(exc)
        return r, None


def status_box(fb: dict | None, result_id: int, key: str) -> bool:
    """Show generation status; returns True when content is available."""
    if fb is None:
        st.info("Feedback has not been generated yet.")
    elif fb["generation_status"] == "failed":
        st.error(f"Generation failed: {fb['error_message']}. The verified score is unaffected.")
    elif fb["generation_status"] == "pending":
        st.info("Generation in progress.")
    if st.button("Retry failed feedback" if fb and fb["generation_status"] == "failed" else "Generate / regenerate", key=key):
        with st.spinner("Generating..."):
            try:
                api.post(f"/api/admin/results/{result_id}/retry-feedback", force=str(bool(fb and fb["generation_status"] == "completed")).lower())
                st.rerun()
            except api.APIError as exc:
                api.show_error(exc)
    return bool(fb and fb["generation_status"] == "completed" and fb["content"])


# ---------------------------------------------------------------- student
with tab_student:
    r, fb = pick_result("student_pick")
    if r and fb is not None:
        st.markdown(f"**{r['candidate_name']}** · {r['assessment_title']} · {r['raw_score']:g}/{r['max_score']:g} · "
                    f"{fmt_pct(r['percentage'])} · {r['category'] or '-'}")
        student = fb["student"]
        if status_box(student, r["result_id"], "regen_student"):
            c = student["content"]
            st.caption(f"Generated {fmt_dt(student['generated_at'])} with {student['model_name']}")
            st.write(c["summary"])
            bullet_list("Strengths", c["strengths"])
            bullet_list("Areas to improve", c["improvement_areas"])
            bullet_list("Recommendations", c["recommendations"])
            bullet_list("Suggested practice areas", c["practice_areas"])
            bullet_list("Next steps", c["next_steps"])
            if c.get("evidence_limitations"):
                st.caption(f"Limitations: {c['evidence_limitations']}")
            st.success(c["closing_message"])

# ---------------------------------------------------------------- admin
with tab_admin:
    r, fb = pick_result("admin_pick")
    if r and fb is not None:
        st.markdown(f"**{r['candidate_name']}** · {r['assessment_title']} · {fmt_pct(r['percentage'])} · {r['category'] or '-'}")
        admin_fb = fb["admin"]
        if status_box(admin_fb, r["result_id"], "regen_admin"):
            c = admin_fb["content"]
            sufficiency = c["evidence_sufficiency"]
            icon = {"sufficient": "✅", "limited": "⚠️", "insufficient": "❌"}[sufficiency]
            st.markdown(f"**Evidence:** {icon} {sufficiency} — {c['evidence_explanation']}")
            st.write(c["overview"])
            left, right = st.columns(2)
            with left:
                bullet_list("Strong areas", c["strong_areas"])
                bullet_list("Weak areas", c["weak_areas"])
                bullet_list("Likely learning gaps", c["likely_learning_gaps"])
            with right:
                bullet_list("Coaching actions", c["coaching_actions"])
                bullet_list("Practice topics", c["practice_topics"])
            if c["follow_up_items"]:
                st.markdown("**Follow-up items**")
                st.dataframe(pd.DataFrame(c["follow_up_items"]), hide_index=True, width="stretch")
            if c.get("limitations"):
                st.caption(f"Limitations: {c['limitations']}")
        try:
            detail = api.get(f"/api/admin/results/{r['result_id']}")
            st.markdown("**Question-level performance (verified)**")
            st.dataframe(
                pd.DataFrame(
                    [{"Q": q["number"], "Topic": q["topic"] or "-", "Outcome": q["outcome"],
                      "Points": f"{q['awarded_points'] if q['awarded_points'] is not None else '-'} / {q['max_points']:g}"}
                     for q in detail["questions"]]
                ),
                hide_index=True, width="stretch",
            )
        except api.APIError as exc:
            api.show_error(exc)

# ---------------------------------------------------------------- group
with tab_group:
    if not assessments:
        st.info("No assessments yet.")
        st.stop()
    a = st.selectbox("Assessment", assessments, format_func=lambda a: f"#{a['id']} {a['title']}", key="group_pick")
    try:
        stats = api.get(f"/api/admin/assessments/{a['id']}/analytics")
        group = api.get(f"/api/admin/assessments/{a['id']}/group-feedback")["group"]
    except api.APIError as exc:
        api.show_error(exc)
        st.stop()

    st.markdown("##### Verified statistics (calculated from the database)")
    c = st.columns(4)
    c[0].metric("Assigned", stats["total_assigned"])
    c[1].metric("Invitations sent", stats["invitations_sent"])
    c[2].metric("Started", stats["started"])
    c[3].metric("Completed", stats["completed"])
    c = st.columns(4)
    c[0].metric("Pending", stats["pending"])
    c[1].metric("Expired / failed invites", stats["expired_or_failed_invitations"])
    c[2].metric("Average (valid results)", fmt_pct(stats["average_percent"]))
    c[3].metric("Highest / lowest", f"{fmt_pct(stats['highest_percent'])} / {fmt_pct(stats['lowest_percent'])}")

    left, right = st.columns(2)
    if stats["score_distribution"]:
        dist = pd.DataFrame({"range": list(stats["score_distribution"]), "candidates": list(stats["score_distribution"].values())})
        left.plotly_chart(px.bar(dist, x="range", y="candidates", title="Score distribution"), width="stretch")
    if stats["category_counts"]:
        cats = pd.DataFrame({"category": list(stats["category_counts"]), "count": list(stats["category_counts"].values())})
        right.plotly_chart(px.pie(cats, names="category", values="count", title="Performance categories"), width="stretch")
    if stats["topics"]:
        st.markdown("**Topic performance**")
        topics = pd.DataFrame(stats["topics"])
        st.plotly_chart(px.bar(topics, x="topic", y="average_percent", range_y=[0, 100]), width="stretch")
    if stats["candidates"]:
        st.markdown("**Candidates (lowest score first)**")
        st.dataframe(pd.DataFrame(stats["candidates"]), hide_index=True, width="stretch")

    st.markdown("##### AI group summary")
    if group and group["generation_status"] == "failed":
        st.error(f"Last generation failed: {group['error_message']}")
    if st.button("Generate group report", disabled=stats["valid_results"] == 0):
        with st.spinner("Summarising verified statistics..."):
            try:
                group = api.post(f"/api/admin/assessments/{a['id']}/group-feedback")["group"]
            except api.APIError as exc:
                api.show_error(exc)
    if group and group["generation_status"] == "completed":
        g = group["content"]
        st.caption(f"Generated {fmt_dt(group['generated_at'])} with {group['model_name']}")
        st.write(g["overview"])
        bullet_list("Common strengths", g["common_strengths"])
        bullet_list("Common skill gaps", g["common_gaps"])
        bullet_list("Recommended training priorities", g["training_priorities"])
        if g["candidates_needing_support"]:
            st.markdown("**Candidates who may benefit from additional support**")
            st.dataframe(pd.DataFrame(g["candidates_needing_support"]), hide_index=True, width="stretch")
        if g.get("limitations"):
            st.caption(f"Limitations: {g['limitations']}")
