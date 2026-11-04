// Student portal: my assessments, history and result pages.
import { get } from "../../core/api.js";
import { studentLayout } from "../../core/layout.js";
import { percentBars } from "../../core/charts.js";
import { fmtDateTime, fmtPct, fmtRelative, plural } from "../../core/format.js";
import { navigate } from "../../core/router.js";
import { session } from "../../core/session.js";
import { categoryPill, emptyState, errorBox, h, loading, mount, pageHead, pill, statusPill, table } from "../../core/ui.js";

const STATE_LABEL = { in_progress: "In progress", open: "Open now", upcoming: "Upcoming", completed: "Completed", closed: "Closed", missed: "Missed" };
const STATE_TONE = { in_progress: "warn", open: "good", upcoming: "info", completed: "accent", closed: "neutral", missed: "bad" };

function card(a) {
  const action = a.state === "in_progress" ? h("a", { class: "btn primary", href: `#/student/test/${a.assignment_id}` }, "Resume")
    : a.can_start ? h("a", { class: "btn primary", href: `#/student/test/${a.assignment_id}` }, "Start")
    : a.latest_result ? h("a", { class: "btn", href: `#/student/results/${a.latest_result.result_id}` }, "View result")
    : h("a", { class: "btn", href: `#/student/test/${a.assignment_id}` }, "Details");
  const when = a.state === "upcoming" ? `Opens ${fmtRelative(a.access_start_at)} · ${fmtDateTime(a.access_start_at)}`
    : a.access_end_at ? `${a.state === "open" || a.state === "in_progress" ? "Closes" : "Closed"} ${fmtDateTime(a.access_end_at)}` : "";
  return h("article", { class: "a-card" },
    h("div", { class: "row between" }, pill(STATE_LABEL[a.state], STATE_TONE[a.state])),
    h("div", null, h("h3", null, a.title), h("p", { class: "muted small", style: { marginTop: "4px" } }, when)),
    h("div", { class: "meta" },
      h("span", null, `${a.duration_minutes} min`),
      h("span", null, plural(a.question_count, "question")),
      h("span", null, `Attempts ${a.attempts_used}/${a.max_attempts}`),
      a.difficulty ? h("span", null, a.difficulty) : null),
    a.latest_result?.percentage !== null && a.latest_result?.percentage !== undefined
      ? h("div", { class: "row" }, h("b", null, fmtPct(a.latest_result.percentage)), categoryPill(a.latest_result.category))
      : a.latest_result ? h("span", { class: "small muted" }, "Result being reviewed") : null,
    h("div", { class: "foot" }, a.start_blocked_reason && !a.can_start && a.state !== "completed" ? h("span", { class: "small muted" }, a.start_blocked_reason) : h("span"), action));
}

export async function homePage() {
  const main = studentLayout("home");
  mount(main, loading());
  let items;
  try { items = await get("/api/portal/assessments"); } catch (err) { return mount(main, errorBox(err, homePage)); }
  const now = items.filter((i) => ["in_progress", "open"].includes(i.state));
  const later = items.filter((i) => i.state === "upcoming");
  const past = items.filter((i) => ["completed", "closed", "missed"].includes(i.state));
  const section = (title, list, empty) => h("section", { style: { marginBottom: "26px" } },
    h("h2", { style: { marginBottom: "12px" } }, title, h("span", { class: "muted", style: { fontWeight: 400, marginLeft: "8px" } }, list.length)),
    list.length ? h("div", { class: "cards" }, list.map(card)) : h("p", { class: "muted" }, empty));
  mount(main,
    pageHead({ title: `Hello, ${(session.student?.name || "").split(" ")[0] || "there"}`, sub: "Your assessments, all in one place. Times are shown in IST. Every assessment needs your webcam, so check it works before you start." }),
    items.length ? [
      section("Available now", now, "Nothing to take right now."),
      section("Upcoming", later, "No upcoming assessments."),
      section("Past", past, "No past assessments yet."),
    ] : emptyState("No assessments yet", "When an administrator assigns you an assessment, it appears here and you also get an email."));
}

export async function historyPage() {
  const main = studentLayout("history");
  mount(main, loading());
  let rows;
  try { rows = await get("/api/portal/history"); } catch (err) { return mount(main, errorBox(err, historyPage)); }
  mount(main,
    pageHead({ title: "Test history", sub: "Every attempt you have submitted, with your verified score." }),
    table({
      rows,
      empty: emptyState("No attempts yet", "Submitted tests appear here."),
      onRowClick: (r) => r.result && navigate(`/student/results/${r.result.result_id}`),
      columns: [
        { label: "Assessment", render: (r) => [h("div", { style: { fontWeight: 600 } }, r.assessment_title), h("div", { class: "sub" }, `Attempt ${r.attempt_number}`)] },
        { label: "Submitted", render: (r) => [fmtDateTime(r.submitted_at), r.auto_submitted ? h("div", { class: "sub" }, "Submitted automatically when time ran out") : null] },
        { label: "Score", class: "num", render: (r) => (r.result?.raw_score !== null && r.result?.raw_score !== undefined ? `${r.result.raw_score} / ${r.result.max_score}` : "—") },
        { label: "%", class: "num", render: (r) => fmtPct(r.result?.percentage) },
        { label: "Category", render: (r) => (!r.result ? statusPill("pending", "Processing") : r.result.result_status === "final" ? categoryPill(r.result.category) : statusPill("provisional", "Being reviewed")) },
      ],
    }));
}

/** Two cards side by side, or one full-width card when the other is empty. */
function pair(a, b) {
  const cards = [a, b].filter(Boolean);
  return cards.length === 2 ? h("div", { class: "grid cols-2" }, cards) : cards[0] || null;
}

export async function resultPage(params) {
  const main = studentLayout("history");
  mount(main, loading());
  let r;
  try { r = await get(`/api/portal/results/${params.id}`); } catch (err) { return mount(main, errorBox(err, () => resultPage(params))); }
  const final = r.result_status === "final";
  const f = r.feedback;
  const list = (title, items) => (items?.length ? h("section", { class: "panel" }, h("h3", { style: { marginBottom: "8px" } }, title), h("ul", { style: { margin: 0, paddingLeft: "18px", display: "grid", gap: "6px" } }, items.map((i) => h("li", null, i)))) : null);
  const ringColor = !final ? "var(--muted)" : r.percentage >= 75 ? "var(--good)" : r.percentage >= 50 ? "var(--warn)" : "var(--bad)";
  mount(main,
    pageHead({ title: r.assessment_title, crumbs: [{ label: "History", href: "#/student/history" }, { label: "Result" }], sub: `Submitted ${fmtDateTime(r.submitted_at)}${r.auto_submitted ? " (automatically, when time ran out)" : ""}` }),
    h("section", { class: "panel", style: { marginBottom: "14px" } },
      h("div", { class: "row", style: { gap: "24px" } },
        h("div", { class: "score-ring", style: { border: `8px solid ${ringColor}` } }, final ? fmtPct(r.percentage) : "…"),
        h("div", { class: "stack", style: { gap: "6px" } },
          final ? h("h2", null, `${r.raw_score} out of ${r.max_score} points`) : h("h2", null, "Your result is being reviewed"),
          final ? h("div", { class: "row" }, categoryPill(r.category), h("span", { class: "muted small" }, "Category describes this assessment only, not your overall ability.")) : h("p", { class: "muted" }, "Some written answers are being marked. You will get an email when your result is ready."),
          final ? h("p", { class: "muted small" }, `${r.correct_count} correct · ${r.incorrect_count} incorrect · ${r.unanswered_count} unanswered`) : null))),
    final && r.topics.length ? h("section", { class: "panel", style: { marginBottom: "14px" } }, h("h3", { style: { marginBottom: "10px" } }, "By topic"),
      percentBars(r.topics.map((t) => ({ label: t.topic, percent: t.percent, hint: `${t.earned} / ${t.possible} points` })))) : null,
    f ? h("div", { class: "stack" },
      h("section", { class: "panel" }, h("h3", { style: { marginBottom: "6px" } }, "Your feedback"), h("p", null, f.summary)),
      pair(list("Strengths", f.strengths), list("Areas to improve", f.improvement_areas)),
      pair(list("Recommendations", f.recommendations), list("Practice areas", f.practice_areas)),
      list("Your next steps", f.next_steps),
      h("section", { class: "panel" }, h("p", null, f.closing_message), f.evidence_limitations ? h("p", { class: "small muted", style: { marginTop: "6px" } }, f.evidence_limitations) : null,
        h("p", { class: "small faint", style: { marginTop: "8px" } }, "Feedback was written with AI from your verified score.")))
      : final ? h("div", { class: "alert info" }, r.feedback_status === "failed" ? "Personalised feedback is not available for this result. Your score is final." : "Your personalised feedback is being prepared. Check back in a minute.") : null);
}
