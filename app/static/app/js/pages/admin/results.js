import { get, imageUrl, patch, post } from "../../core/api.js";
import { adminLayout } from "../../core/layout.js";
import { TYPE_LABELS, fmtClock, fmtDateTime, fmtDuration, fmtPct, plural } from "../../core/format.js";
import { navigate } from "../../core/router.js";
import {
  busy, categoryPill, confirmDialog, emptyState, errorBox, field, h, input, loading, mount, openModal, pageHead, pill, select,
  stat, statusPill, table, tabs, textarea, toast, toastError,
} from "../../core/ui.js";

export async function resultsPage(_p, query) {
  const main = adminLayout("results");
  const listHost = h("div");
  const assessmentFilter = select([{ value: "", label: "All assessments" }], query.assessment || "");
  const statusFilter = select([{ value: "", label: "Any status" }, { value: "final", label: "Final" }, { value: "provisional", label: "Awaiting review" }], query.status || "");
  const search = input({ type: "search", class: "input grow", placeholder: "Filter by candidate name" });
  let items = [];

  function render() {
    const q = search.value.toLowerCase();
    const rows = items.filter((r) => !q || r.candidate_name.toLowerCase().includes(q) || r.candidate_email.toLowerCase().includes(q));
    mount(listHost, table({
      rows,
      empty: emptyState("No results", "Results appear as soon as candidates submit."),
      onRowClick: (r) => navigate(`/admin/results/${r.result_id}`),
      columns: [
        { label: "Candidate", render: (r) => [h("div", null, r.candidate_name), h("div", { class: "sub" }, r.candidate_email)] },
        { label: "Assessment", render: (r) => r.assessment_title },
        { label: "Score", class: "num", render: (r) => `${r.raw_score} / ${r.max_score}` },
        { label: "%", class: "num", render: (r) => fmtPct(r.percentage) },
        { label: "Category", render: (r) => (r.result_status === "final" ? categoryPill(r.category) : statusPill("provisional", "Needs review")) },
        { label: "Flags", class: "num", render: (r) => (r.proctoring_mode === "off" ? h("span", { class: "faint" }, "off") : r.proctoring_flags ? pill(String(r.proctoring_flags), "bad") : pill("0", "good")) },
        { label: "Feedback", render: (r) => statusPill(r.student_feedback_status || "pending") },
        { label: "Email", render: (r) => (r.result_email_status ? statusPill(r.result_email_status) : h("span", { class: "faint" }, "—")) },
        { label: "Submitted", render: (r) => h("span", { class: "nowrap small" }, fmtDateTime(r.submitted_at)) },
      ],
    }));
  }
  async function load() {
    mount(listHost, loading());
    try {
      const data = await get("/api/admin/results", { assessment_id: assessmentFilter.value || null, result_status: statusFilter.value || null, limit: 500 });
      items = data.items;
      render();
    } catch (err) { mount(listHost, errorBox(err, load)); }
  }
  try {
    (await get("/api/assessments")).forEach((a) => assessmentFilter.appendChild(h("option", { value: a.id, selected: String(a.id) === query.assessment }, a.title)));
  } catch { /* optional */ }
  assessmentFilter.addEventListener("change", load);
  statusFilter.addEventListener("change", load);
  search.addEventListener("input", render);
  mount(main, pageHead({ title: "Results", sub: "Verified scores computed by the application. Open a result for per-question marks, AI feedback and proctoring flags." }),
    h("div", { class: "toolbar" }, search, assessmentFilter, statusFilter), listHost);
  load();
}

const OUTCOME_TONES = { correct: "good", partial: "warn", incorrect: "bad", unanswered: "neutral", pending_review: "warn" };
const EVENT_LABELS = {
  consent_given: "Consent given", camera_started: "Camera on", camera_denied: "Camera denied", camera_stopped: "Camera stopped",
  face_missing: "No face visible", multiple_faces: "More than one face", face_returned: "Face visible again",
  tab_hidden: "Left the test tab", tab_visible: "Returned to the tab", fullscreen_exit: "Left fullscreen", fullscreen_enter: "Entered fullscreen",
};

export async function resultPage(params) {
  const main = adminLayout("results");
  mount(main, loading());
  let r, fb;
  try { [r, fb] = await Promise.all([get(`/api/admin/results/${params.id}`), get(`/api/admin/results/${params.id}/feedback`)]); }
  catch (err) { return mount(main, errorBox(err, () => resultPage(params))); }
  const reload = () => resultPage(params);
  let tab = "answers";
  const body = h("div");

  const pending = r.questions.filter((q) => q.needs_review || q.outcome === "pending_review");
  const head = pageHead({
    title: r.candidate_name,
    crumbs: [{ label: "Results", href: "#/admin/results" }, { label: r.assessment_title, href: `#/admin/assessments/${r.assessment_id}?tab=results` }, { label: r.candidate_name }],
    sub: `${r.candidate_email} · attempt ${r.attempt_number} · submitted ${fmtDateTime(r.submitted_at)}${r.auto_submitted ? " (auto-submitted at the deadline)" : ""}`,
    actions: [
      fb.student?.generation_status !== "completed" && r.result_status === "final" && r.result_email_status !== "sent"
        ? h("button", { class: "btn", type: "button", onclick: sendScoreOnly }, "Email score without feedback") : null,
    ],
  });

  async function sendScoreOnly(e) {
    if (!(await confirmDialog({ title: "Send the result email now?", message: "The student receives their score without AI feedback. Only one result email is ever sent.", confirmLabel: "Send email" }))) return;
    busy(e.target, async () => { try { await post(`/api/admin/results/${r.result_id}/send-result-email`); toast("Result email sent.", "good"); reload(); } catch (err) { toastError(err); } });
  }

  function markModal(q) {
    const pts = input({ type: "number", min: 0, max: q.max_points, step: "0.5", value: q.awarded_points ?? "" });
    const why = textarea({ rows: 3, placeholder: "Why these points? (kept with the result)" });
    openModal({ title: `Mark question ${q.number}`, wide: true, body: h("div", { class: "form" },
      h("div", { class: "panel" }, h("p", { class: "q-text" }, q.question_text)),
      h("div", null, h("div", { class: "field-label" }, "Student answer"), h("div", { class: "panel", style: { whiteSpace: "pre-wrap" } }, q.student_answer || h("span", { class: "muted" }, "No answer"))),
      q.grading_rationale ? h("div", { class: "alert info" }, h("b", null, q.grading_method === "ai_assisted" ? "AI suggestion: " : "Note: "), q.grading_rationale) : null,
      h("div", { class: "cols-2" }, field(`Points (0–${q.max_points})`, pts), field("Reason", why))),
    actions: [{ label: "Cancel" }, { label: "Save mark", primary: true, onClick: async (close) => {
      if (pts.value === "") throw new Error("Enter the points awarded.");
      if (!why.value.trim()) throw new Error("Add a short reason.");
      const res = await patch(`/api/admin/answers/${q.answer_id}/grade`, { awarded_points: Number(pts.value), rationale: why.value.trim() });
      close(); toast(res.became_final ? "Result is now final. Feedback and the result email are being prepared." : "Mark saved.", "good");
      setTimeout(reload, res.became_final ? 600 : 0);
    } }] });
  }

  function answersTab() {
    return h("div", { class: "stack" },
      pending.length ? h("div", { class: "alert warn" }, h("b", null, `${plural(pending.length, "answer")} need${pending.length === 1 ? "s" : ""} marking. `), "The result stays provisional (no category, no feedback, no result email) until every answer is marked.") : null,
      table({ rows: r.questions, columns: [
        { label: "#", class: "num", render: (q) => q.number },
        { label: "Question", render: (q) => [h("div", { style: { maxWidth: "46ch", whiteSpace: "normal" } }, q.question_text), h("div", { class: "sub" }, [TYPE_LABELS[q.question_type], q.topic].filter(Boolean).join(" · "))] },
        { label: "Answer", render: (q) => h("div", { style: { maxWidth: "30ch", whiteSpace: "pre-wrap" } }, q.student_answer || h("span", { class: "faint" }, "—")) },
        { label: "Expected", render: (q) => h("div", { style: { maxWidth: "26ch", whiteSpace: "normal" }, class: "small muted" }, q.correct_answer || "—") },
        { label: "Points", class: "num", render: (q) => `${q.awarded_points ?? "—"} / ${q.max_points}` },
        { label: "Outcome", render: (q) => [pill(q.outcome.replace("_", " "), OUTCOME_TONES[q.outcome]), q.grading_method === "ai_assisted" ? h("div", { class: "sub" }, "AI-assisted") : q.grading_method === "manual" ? h("div", { class: "sub" }, "Marked by admin") : null] },
        { label: "", class: "actions", render: (q) => (["text", "coding"].includes(q.question_type) && q.answer_id
            ? h("button", { class: `btn sm ${q.needs_review || q.outcome === "pending_review" ? "primary" : ""}`, type: "button", onclick: () => markModal(q) }, q.needs_review || q.outcome === "pending_review" ? "Mark" : "Re-mark") : null) },
      ] }));
  }

  function report(title, f, kind) {
    const retry = (force) => h("button", { class: "btn sm", type: "button", onclick: (e) => busy(e.currentTarget, async () => {
      try { await post(`/api/admin/results/${r.result_id}/retry-feedback`, {}, { force }); toast("Feedback updated.", "good"); reload(); } catch (err) { toastError(err); }
    }) }, force ? "Regenerate" : "Retry");
    if (r.result_status !== "final") return h("section", { class: "panel" }, h("h2", null, title), h("p", { class: "muted", style: { marginTop: "8px" } }, "Generated after every answer is marked."));
    if (!f) return h("section", { class: "panel" }, h("div", { class: "panel-head" }, h("h2", null, title), retry(false)), h("p", { class: "muted" }, "Not generated yet."));
    if (f.generation_status !== "completed") return h("section", { class: "panel" }, h("div", { class: "panel-head" }, h("h2", null, title), retry(false)),
      h("div", { class: "alert bad" }, `Generation ${f.generation_status}: ${f.error_message || ""} The score is saved.`));
    const c = f.content;
    const list = (label, items) => (items?.length ? h("div", null, h("h3", { style: { margin: "12px 0 4px" } }, label), h("ul", { style: { margin: 0, paddingLeft: "18px" } }, items.map((i) => h("li", null, typeof i === "string" ? i : `Q${i.question_number ?? "?"} ${i.topic ? `(${i.topic})` : ""}: ${i.reason}`)))) : null);
    return h("section", { class: "panel" },
      h("div", { class: "panel-head" }, h("h2", null, title), h("div", { class: "row" }, h("span", { class: "hint" }, `${f.model_name} · ${fmtDateTime(f.generated_at)}`), retry(true))),
      kind === "student"
        ? [h("p", null, c.summary), list("Strengths", c.strengths), list("Areas to improve", c.improvement_areas), list("Recommendations", c.recommendations), list("Practice areas", c.practice_areas), list("Next steps", c.next_steps), h("p", { style: { marginTop: "12px" } }, c.closing_message), c.evidence_limitations ? h("p", { class: "small muted" }, c.evidence_limitations) : null]
        : [h("p", null, c.overview), h("div", { class: "row", style: { marginTop: "8px" } }, pill(`Evidence: ${c.evidence_sufficiency}`, c.evidence_sufficiency === "sufficient" ? "good" : "warn")), h("p", { class: "small muted", style: { marginTop: "6px" } }, c.evidence_explanation),
           list("Strong areas", c.strong_areas), list("Weak areas", c.weak_areas), list("Likely learning gaps", c.likely_learning_gaps), list("Follow up", c.follow_up_items), list("Coaching actions", c.coaching_actions), list("Practice topics", c.practice_topics), c.limitations ? h("p", { class: "small muted", style: { marginTop: "10px" } }, c.limitations) : null]);
  }

  async function proctoringTab() {
    const host = h("div", null, loading());
    try {
      const t = await get(`/api/admin/attempts/${r.attempt_id}/proctoring`);
      mount(host, h("div", { class: "stack" },
        h("div", { class: "alert info" }, t.note),
        h("div", { class: "grid cols-4" },
          stat("Flags", t.flag_count, "events worth reviewing"),
          stat("No face visible", fmtDuration(t.face_missing_seconds), "total time"),
          stat("Tab switches", t.counts.tab_hidden || 0),
          stat("Consent", t.consent_at ? "Given" : "Not given", t.consent_at ? fmtDateTime(t.consent_at) : "")),
        h("section", { class: "panel" }, h("h2", { style: { marginBottom: "10px" } }, "Timeline"),
          t.events.length ? h("ul", { class: "timeline" }, t.events.map((e) => {
            const li = h("li", null, h("span", { class: "t" }, fmtClock(e.occurred_at)), h("span", { class: `mark ${e.flag ? "flag" : ""}` }),
              h("div", null, h("b", null, EVENT_LABELS[e.event_type] || e.event_type), e.duration_seconds ? h("span", { class: "muted" }, ` · ${fmtDuration(e.duration_seconds)}`) : null));
            if (e.has_snapshot) {
              const img = h("img", { class: "snap", alt: `Snapshot: ${EVENT_LABELS[e.event_type]}` });
              li.lastChild.appendChild(img);
              imageUrl(`/api/admin/proctoring/snapshots/${e.id}`).then((u) => { img.src = u; }).catch(() => img.replaceWith(h("span", { class: "small muted" }, " (snapshot unavailable)")));
            }
            return li;
          })) : emptyState("No events recorded"))));
    } catch (err) { mount(host, errorBox(err)); }
    return host;
  }

  function renderTab() {
    const items = [{ id: "answers", label: "Answers" }, { id: "feedback", label: "AI feedback" }];
    if (r.proctoring_mode !== "off") items.push({ id: "proctoring", label: "Proctoring", count: r.proctoring_flags });
    const view = tab === "answers" ? answersTab()
      : tab === "feedback" ? h("div", { class: "grid cols-2" }, report("Student report", fb.student, "student"), report("Admin coaching report", fb.admin, "admin"))
      : proctoringTab();
    Promise.resolve(view).then((v) => mount(body, tabs(items, tab, (id) => { tab = id; renderTab(); }), v));
  }

  mount(main, head,
    h("div", { class: "grid cols-4", style: { marginBottom: "16px" } },
      stat("Score", `${r.raw_score} / ${r.max_score}`, `${r.correct_count} correct · ${r.incorrect_count} incorrect · ${r.unanswered_count} unanswered`),
      stat("Percentage", fmtPct(r.percentage), r.grading_method === "objective" ? "graded automatically" : `grading: ${r.grading_method.replace("_", " ")}`),
      stat("Category", r.result_status === "final" ? categoryPill(r.category) : statusPill("provisional", "Needs review")),
      stat("Result email", r.result_email_status ? statusPill(r.result_email_status) : "Not sent", r.proctoring_mode !== "off" ? `${plural(r.proctoring_flags, "proctoring flag")}` : "proctoring off")),
    body);
  renderTab();
}
