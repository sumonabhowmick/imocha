import pandas as pd
import plotly.express as px
import streamlit as st

import api_client as api
from ui import badge, fmt_dt, fmt_pct

st.title("Results")

try:
    assessments = api.get("/api/assessments")
except api.APIError as exc:
    api.show_error(exc)
    st.stop()

c1, c2 = st.columns([3, 1])
choice = c1.selectbox(
    "Assessment", [None] + assessments, format_func=lambda a: "All assessments" if a is None else f"#{a['id']} {a['title']}"
)
status_filter = c2.selectbox("Result status", [None, "final", "provisional"], format_func=lambda s: s or "All")

try:
    data = api.get(
        "/api/admin/results", assessment_id=choice["id"] if choice else None, result_status=status_filter, limit=500
    )
except api.APIError as exc:
    api.show_error(exc)
    st.stop()

items = data["items"]
st.caption(f"{data['total']} result(s)")
if not items:
    st.info("No results yet. Results appear here after candidates submit.")
    st.stop()

df = pd.DataFrame(
    [
        {
            "Result": r["result_id"],
            "Candidate": r["candidate_name"],
            "Assessment": r["assessment_title"],
            "Score": f"{r['raw_score']:g} / {r['max_score']:g}",
            "Percentage": fmt_pct(r["percentage"]),
            "Category": r["category"] or "-",
            "Status": badge(r["result_status"]),
            "Grading": r["grading_method"],
            "Submitted": fmt_dt(r["submitted_at"]) + (" (auto)" if r["auto_submitted"] else ""),
            "Student feedback": badge(r["student_feedback_status"]),
            "Admin report": badge(r["admin_feedback_status"]),
            "Result email": badge(r["result_email_status"]),
        }
        for r in items
    ]
)
st.dataframe(df, hide_index=True, use_container_width=True)

pcts = [r["percentage"] for r in items if r["percentage"] is not None and r["result_status"] == "final"]
if len(pcts) > 1:
    st.plotly_chart(
        px.histogram(pd.DataFrame({"percentage": pcts}), x="percentage", nbins=10, range_x=[0, 100], title="Score distribution (final results)"),
        use_container_width=True,
    )

# ---------------------------------------------------------------- detail
st.divider()
selected = st.selectbox(
    "Open result", items, format_func=lambda r: f"#{r['result_id']} {r['candidate_name']} — {r['assessment_title']}"
)
try:
    detail = api.get(f"/api/admin/results/{selected['result_id']}")
except api.APIError as exc:
    api.show_error(exc)
    st.stop()

m1, m2, m3, m4 = st.columns(4)
m1.metric("Score", f"{detail['raw_score']:g} / {detail['max_score']:g}")
m2.metric("Percentage", fmt_pct(detail["percentage"]))
m3.metric("Category", detail["category"] or "-")
m4.metric("Status", detail["result_status"])
st.caption(
    f"Correct {detail['correct_count']} · Incorrect {detail['incorrect_count']} · Unanswered {detail['unanswered_count']} · "
    f"Awaiting review {detail['pending_review_count']} · Grading: {detail['grading_method']}"
)
if detail["grading_method"] in ("ai_assisted", "mixed"):
    st.info("Some answers were scored with AI assistance against a rubric. These are not objectively verified scores; review them below.")

st.markdown("#### Question-level performance")
st.dataframe(
    pd.DataFrame(
        [
            {
                "Q": q["number"],
                "Topic": q["topic"] or "-",
                "Outcome": q["outcome"],
                "Points": f"{q['awarded_points'] if q['awarded_points'] is not None else '-'} / {q['max_points']:g}",
                "Student answer": (q["student_answer"] or "—")[:120],
                "Correct answer": (q["correct_answer"] or "—")[:120],
                "Method": q["grading_method"] or "-",
            }
            for q in detail["questions"]
        ]
    ),
    hide_index=True,
    use_container_width=True,
)

reviewable = [q for q in detail["questions"] if q["grading_method"] in ("pending_review", "ai_assisted", "manual") and q["answer_id"]]
if reviewable:
    st.markdown("#### Review written answers")
    for q in reviewable:
        flag = "🔎 needs review" if q["needs_review"] else ""
        with st.expander(f"Q{q['number']} — {q['outcome']} {flag}"):
            st.markdown(f"**Question:** {q['question_text']}")
            st.markdown("**Student answer:**")
            st.code(q["student_answer"] or "(no answer)", language=None)
            if q["grading_rationale"]:
                label = f"AI-assisted rationale ({q['grading_model']})" if q["grading_method"] == "ai_assisted" else "Rationale"
                st.markdown(f"**{label}:** {q['grading_rationale']}")
            with st.form(f"grade_{q['answer_id']}"):
                pts = st.number_input(
                    "Points", 0.0, float(q["max_points"]), float(q["awarded_points"] or 0.0), 0.25, key=f"pts{q['answer_id']}"
                )
                note = st.text_input("Reason", key=f"note{q['answer_id']}")
                if st.form_submit_button("Save grade"):
                    try:
                        res = api.patch(f"/api/admin/answers/{q['answer_id']}/grade", {"awarded_points": pts, "rationale": note or "Manual review"})
                        st.success(
                            "Saved. The result is now final; feedback and the result email are being processed."
                            if res["became_final"]
                            else f"Saved. Result status: {res['result_status']}."
                        )
                    except api.APIError as exc:
                        api.show_error(exc)

st.markdown("#### Actions")
a1, a2 = st.columns(2)
if a1.button("Retry / generate feedback", disabled=detail["result_status"] != "final"):
    with st.spinner("Generating feedback..."):
        try:
            res = api.post(f"/api/admin/results/{selected['result_id']}/retry-feedback")
            st.success(f"Feedback: {res['feedback']}. Result email: {res['result_email']}.")
        except api.APIError as exc:
            api.show_error(exc)
if a2.button(
    "Send result email without AI feedback",
    disabled=detail["result_status"] != "final" or detail["result_email_status"] in ("sent", "outbox"),
    help="Use when AI feedback is unavailable. The student receives the verified score only. Sent at most once.",
):
    try:
        res = api.post(f"/api/admin/results/{selected['result_id']}/send-result-email")
        st.success(f"Email status: {res['status']}")
    except api.APIError as exc:
        api.show_error(exc)
st.caption("Open Feedback reports to read the student and admin reports for this result.")
