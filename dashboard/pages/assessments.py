from datetime import date, datetime, time, timedelta

import pandas as pd
import streamlit as st

import api_client as api
from ui import TZ, TZ_NAME, badge, combine_local, fmt_dt, to_local

st.title("Assessments")

DIFFICULTIES = ["beginner", "intermediate", "advanced"]
POLICIES = {
    "finish_after_window": "Finish after window (full duration even if the window closes)",
    "hard_end_at_window": "Hard end at window close",
}
QTYPES = {
    "mcq_single": "Multiple choice (single answer)",
    "mcq_multi": "Multiple choice (select all that apply)",
    "text": "Text answer (manual or AI-assisted grading)",
    "coding": "Coding answer as text (manual or AI-assisted grading; never executed)",
}


def load_assessments():
    try:
        return api.get("/api/assessments")
    except api.APIError as exc:
        api.show_error(exc)
        st.stop()


def window_inputs(prefix: str, start_iso: str | None = None, end_iso: str | None = None):
    now = datetime.now(TZ)
    start = to_local(start_iso) or (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    end = to_local(end_iso) or start + timedelta(hours=3)
    c1, c2, c3, c4 = st.columns(4)
    sd = c1.date_input("Window start date", start.date(), key=f"{prefix}_sd")
    stime = c2.time_input("Start time", start.time(), key=f"{prefix}_st", step=300)
    ed = c3.date_input("Window end date", end.date(), key=f"{prefix}_ed")
    etime = c4.time_input("End time", end.time(), key=f"{prefix}_et", step=300)
    st.caption(f"Times are in {TZ_NAME}.")
    return combine_local(sd, stime), combine_local(ed, etime)


# ---------------------------------------------------------------- list + create
assessments = load_assessments()
tab_list, tab_create = st.tabs(["All assessments", "Create assessment"])

with tab_list:
    if not assessments:
        st.info("No assessments yet. Create one in the next tab.")
    else:
        df = pd.DataFrame(
            [
                {
                    "ID": a["id"],
                    "Title": a["title"],
                    "Status": badge(a["display_status"]),
                    "Questions": a["question_count"],
                    "Points": a["total_points"],
                    "Duration (min)": a["duration_minutes"],
                    "Window start": fmt_dt(a["access_start_at"]),
                    "Window end": fmt_dt(a["access_end_at"]),
                }
                for a in assessments
            ]
        )
        st.dataframe(df, hide_index=True, width="stretch")

with tab_create:
    with st.form("create_assessment"):
        title = st.text_input("Title *")
        c1, c2 = st.columns(2)
        category = c1.text_input("Category / subject", placeholder="e.g. Python")
        difficulty = c2.selectbox("Difficulty", DIFFICULTIES, index=1)
        description = st.text_area("Description")
        instructions = st.text_area("Instructions for students")
        c3, c4, c5 = st.columns(3)
        duration = c3.number_input("Duration (minutes)", 1, 600, 45)
        max_attempts = c4.number_input("Maximum attempts", 1, 10, 1)
        policy = c5.selectbox("Completion policy", list(POLICIES), format_func=POLICIES.get)
        set_window = st.checkbox("Set the access window now", value=True)
        start_iso, end_iso = window_inputs("new")
        if st.form_submit_button("Create draft", type="primary"):
            body = {
                "title": title,
                "category": category,
                "difficulty": difficulty,
                "description": description,
                "instructions": instructions,
                "duration_minutes": int(duration),
                "max_attempts": int(max_attempts),
                "completion_policy": policy,
            }
            if set_window:
                body.update(access_start_at=start_iso, access_end_at=end_iso)
            try:
                created = api.post("/api/assessments", body)
                st.session_state["selected_assessment"] = created["id"]
                st.success(f"Draft '{created['title']}' created. Add questions below.")
                st.rerun()
            except api.APIError as exc:
                api.show_error(exc)

# ---------------------------------------------------------------- manage one
st.divider()
if not assessments:
    st.stop()
ids = [a["id"] for a in assessments]
labels = {a["id"]: f"#{a['id']} {a['title']} ({a['display_status']})" for a in assessments}
default = st.session_state.get("selected_assessment")
selected = st.selectbox(
    "Manage assessment", ids, format_func=labels.get, index=ids.index(default) if default in ids else 0
)
st.session_state["selected_assessment"] = selected

try:
    a = api.get(f"/api/assessments/{selected}")
except api.APIError as exc:
    api.show_error(exc)
    st.stop()

is_draft = a["status"] == "draft"
st.subheader(a["title"])
st.markdown(
    f"Status: **{badge(a['display_status'])}** · {a['question_count']} questions · {a['total_points']} points · "
    f"{a['duration_minutes']} min · max attempts {a['max_attempts']} · {POLICIES[a['completion_policy']]}"
)
st.caption(f"Window: {fmt_dt(a['access_start_at'])} → {fmt_dt(a['access_end_at'])} ({TZ_NAME})")
if not is_draft and a["status"] == "published":
    st.info("Published: the question set is locked so existing attempts keep their meaning. Duplicate to make a new version.")

b1, b2, b3, b4 = st.columns(4)
if is_draft and b1.button("Publish", type="primary"):
    try:
        api.post(f"/api/assessments/{selected}/publish")
        st.success("Published.")
        st.rerun()
    except api.APIError as exc:
        api.show_error(exc)
if a["status"] != "archived" and b2.button("Archive"):
    try:
        api.post(f"/api/assessments/{selected}/archive")
        st.rerun()
    except api.APIError as exc:
        api.show_error(exc)
if b3.button("Duplicate as new draft"):
    try:
        copy = api.post(f"/api/assessments/{selected}/duplicate")
        st.session_state["selected_assessment"] = copy["id"]
        st.rerun()
    except api.APIError as exc:
        api.show_error(exc)
show_preview = b4.toggle("Student preview")

if show_preview:
    try:
        preview = api.post(f"/api/assessments/{selected}/preview")
        with st.container(border=True):
            st.markdown(f"### {preview['title']}")
            st.caption(f"{preview['duration_minutes']} minutes")
            st.write(preview["instructions"] or "")
            for q in preview["questions"]:
                st.markdown(f"**Q{q['number']}.** {q['question_text']}  _({q['points']} pts)_")
                for o in q["options"] or []:
                    st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;{o['id']}. {o['text']}")
                if not q["options"]:
                    st.caption("Written answer")
            st.caption("This is exactly what students receive — no correct answers or rubrics.")
    except api.APIError as exc:
        api.show_error(exc)

# ---------------------------------------------------------------- edit settings
if a["status"] != "archived":
    with st.expander("Edit settings and schedule"):
        with st.form("edit_assessment"):
            title = st.text_input("Title", a["title"])
            description = st.text_area("Description", a["description"])
            instructions = st.text_area("Instructions", a["instructions"])
            c1, c2, c3 = st.columns(3)
            duration = c1.number_input("Duration (minutes)", 1, 600, a["duration_minutes"])
            max_attempts = c2.number_input("Maximum attempts", 1, 10, a["max_attempts"])
            policy = c3.selectbox(
                "Completion policy", list(POLICIES), index=list(POLICIES).index(a["completion_policy"]), format_func=POLICIES.get
            )
            start_iso, end_iso = window_inputs(f"edit{selected}", a["access_start_at"], a["access_end_at"])
            if st.form_submit_button("Save changes"):
                body = {
                    "title": title,
                    "description": description,
                    "instructions": instructions,
                    "duration_minutes": int(duration),
                    "max_attempts": int(max_attempts),
                    "completion_policy": policy,
                    "access_start_at": start_iso,
                    "access_end_at": end_iso,
                }
                try:
                    api.patch(f"/api/assessments/{selected}", body)
                    st.success("Saved.")
                    st.rerun()
                except api.APIError as exc:
                    api.show_error(exc)

    if is_draft:
        with st.expander("Performance categories (optional, assessment-specific)"):
            st.caption("Bands must cover 0–100% without gaps or overlaps. Min is inclusive; max is exclusive except for the last band.")
            scale_df = st.data_editor(
                pd.DataFrame(a["category_scale"]), num_rows="dynamic", key=f"scale{selected}", width="stretch"
            )
            if st.button("Save category scale"):
                bands = [
                    {"label": str(r["label"]), "min": float(r["min"]), "max": float(r["max"])}
                    for r in scale_df.to_dict("records")
                    if str(r.get("label") or "").strip()
                ]
                try:
                    api.patch(f"/api/assessments/{selected}", {"category_scale": bands})
                    st.success("Category scale saved.")
                except api.APIError as exc:
                    api.show_error(exc)

# ---------------------------------------------------------------- questions
st.subheader("Questions")

if is_draft:
    with st.expander("Generate questions with OpenAI"):
        st.caption("Questions use the assessment title, subject, description and difficulty. Review the generated questions and answer keys before publishing.")
        with st.form(f"generate_questions_{selected}"):
            gc1, gc2 = st.columns(2)
            mcq_count = gc1.number_input("MCQ (one correct answer)", min_value=0, max_value=50, value=0, step=1)
            maq_count = gc2.number_input("MAQ (multiple correct answers)", min_value=0, max_value=50, value=0, step=1)
            generate = st.form_submit_button("Generate questions", type="primary")
            if generate:
                if mcq_count + maq_count == 0:
                    st.error("Choose at least one question.")
                elif mcq_count + maq_count > 50:
                    st.error("Generate at most 50 questions at a time.")
                else:
                    try:
                        api.post(
                            f"/api/assessments/{selected}/generate-questions",
                            {"mcq_count": int(mcq_count), "maq_count": int(maq_count)},
                        )
                        st.success(f"Generated {mcq_count} MCQ and {maq_count} MAQ questions. Review them below before publishing.")
                        st.rerun()
                    except api.APIError as exc:
                        api.show_error(exc)


def options_editor(key: str, existing: list | None):
    default = existing or [{"id": "A", "text": ""}, {"id": "B", "text": ""}, {"id": "C", "text": ""}, {"id": "D", "text": ""}]
    edited = st.data_editor(pd.DataFrame(default), num_rows="dynamic", key=key, width="stretch")
    return [
        {"id": str(r["id"]).strip(), "text": str(r["text"]).strip()}
        for r in edited.to_dict("records")
        if str(r.get("id") or "").strip() and str(r.get("text") or "").strip()
    ]


def question_form(key: str, q: dict | None = None):
    """Returns a request body when submitted, else None."""
    qtype = st.selectbox(
        "Question type", list(QTYPES), format_func=QTYPES.get, key=f"{key}_type",
        index=list(QTYPES).index(q["question_type"]) if q else 0,
    )
    text = st.text_area("Question text", q["question_text"] if q else "", key=f"{key}_text")
    c1, c2 = st.columns(2)
    topic = c1.text_input("Topic / skill", q["topic"] if q else "", key=f"{key}_topic", help="Used for skill-gap analysis")
    points = c2.number_input("Points", 0.0, 1000.0, float(q["points"]) if q else 1.0, 0.5, key=f"{key}_pts")
    body = {"question_type": qtype, "question_text": text, "topic": topic, "points": points}
    if qtype in ("mcq_single", "mcq_multi"):
        st.caption("Options")
        options = options_editor(f"{key}_opts_{qtype}", q["options"] if q and q["options"] else None)
        ids_ = [o["id"] for o in options]
        if qtype == "mcq_single":
            current = q["correct_answer"] if q and isinstance(q["correct_answer"], str) else None
            correct = st.selectbox("Correct option", ids_, index=ids_.index(current) if current in ids_ else 0, key=f"{key}_correct") if ids_ else None
        else:
            current = q["correct_answer"] if q and isinstance(q["correct_answer"], list) else []
            correct = st.multiselect("Correct options", ids_, default=[c for c in current if c in ids_], key=f"{key}_correctm")
        body.update(options=options, correct_answer=correct, grading_rubric="")
    else:
        rubric = st.text_area(
            "Grading rubric", q["grading_rubric"] if q else "", key=f"{key}_rubric",
            help="Required for AI-assisted grading. Without it, answers wait for manual review.",
        )
        reference = st.text_area(
            "Reference answer (optional, never shown to students)",
            q["correct_answer"] if q and isinstance(q["correct_answer"], str) else "", key=f"{key}_ref",
        )
        body.update(options=None, correct_answer=reference or None, grading_rubric=rubric)
    body["explanation"] = st.text_area("Explanation (admin only)", q["explanation"] if q else "", key=f"{key}_expl")
    return body


questions = a["questions"]
for q in questions:
    header = f"Q{q['position'] + 1}. {q['question_text'][:80]} — {q['points']} pts · {QTYPES[q['question_type']].split(' (')[0]}"
    with st.expander(header):
        if q["options"]:
            for o in q["options"]:
                correct = o["id"] == q["correct_answer"] or (isinstance(q["correct_answer"], list) and o["id"] in q["correct_answer"])
                st.markdown(f"{'✅' if correct else '▫️'} {o['id']}. {o['text']}")
        if q["grading_rubric"]:
            st.markdown(f"**Rubric:** {q['grading_rubric']}")
        if q["topic"]:
            st.caption(f"Topic: {q['topic']}")
        if is_draft:
            if st.toggle("Edit", key=f"edit_toggle_{q['id']}"):
                body = question_form(f"q{q['id']}", q)
                if st.button("Save question", key=f"save_{q['id']}"):
                    try:
                        api.patch(f"/api/questions/{q['id']}", body)
                        st.success("Saved.")
                        st.rerun()
                    except api.APIError as exc:
                        api.show_error(exc)
            if st.button("Delete question", key=f"del_{q['id']}"):
                try:
                    api.delete(f"/api/questions/{q['id']}")
                    st.rerun()
                except api.APIError as exc:
                    api.show_error(exc)

if is_draft and len(questions) > 1:
    with st.expander("Reorder questions"):
        order_df = st.data_editor(
            pd.DataFrame([{"id": q["id"], "question": q["question_text"][:80], "order": i + 1} for i, q in enumerate(questions)]),
            disabled=["id", "question"], hide_index=True, key=f"order{selected}",
        )
        if st.button("Apply order"):
            new_order = [int(r["id"]) for r in sorted(order_df.to_dict("records"), key=lambda r: r["order"])]
            try:
                api.post(f"/api/assessments/{selected}/questions/reorder", {"question_ids": new_order})
                st.rerun()
            except api.APIError as exc:
                api.show_error(exc)

if is_draft:
    st.markdown("#### Add a question")
    with st.container(border=True):
        n = st.session_state.get("new_q_counter", 0)
        body = question_form(f"new{selected}_{n}")
        if st.button("Add question", type="primary"):
            try:
                api.post(f"/api/assessments/{selected}/questions", body)
                st.session_state["new_q_counter"] = n + 1  # fresh, empty form
                st.success("Question added.")
                st.rerun()
            except api.APIError as exc:
                api.show_error(exc)
