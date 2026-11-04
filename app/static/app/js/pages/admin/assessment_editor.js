import { del, download, get, patch, post } from "../../core/api.js";
import { adminLayout } from "../../core/layout.js";
import { barChart, percentBars } from "../../core/charts.js";
import { TYPE_LABELS, fmtDateTime, fmtPct, plural } from "../../core/format.js";
import { navigate } from "../../core/router.js";
import {
  busy, categoryPill, checkbox, confirmDialog, emptyState, errorBox, field, h, input, loading, mount, openModal,
  pageHead, pill, select, stat, statusPill, table, tabs, textarea, toast, toastError,
} from "../../core/ui.js";
import { settingsForm } from "./assessments.js";

const TABS = [
  { id: "settings", label: "Settings" },
  { id: "questions", label: "Questions" },
  { id: "candidates", label: "Candidates" },
  { id: "results", label: "Results" },
  { id: "analytics", label: "Analytics" },
];

export async function assessmentPage(params, query) {
  const main = adminLayout("assessments");
  mount(main, loading());
  let a;
  try { a = await get(`/api/assessments/${params.id}`); } catch (err) { return mount(main, errorBox(err, () => assessmentPage(params, query))); }

  let active = TABS.some((t) => t.id === query.tab) ? query.tab : a.status === "draft" && !a.question_count ? "questions" : "settings";
  const body = h("div");
  const isDraft = a.status === "draft";

  async function reload(tab = active) {
    a = await get(`/api/assessments/${params.id}`);
    active = tab;
    renderHead();
    renderTab();
  }

  const headHost = h("div");
  function renderHead() {
    const actions = [
      h("button", { class: "btn", type: "button", onclick: () => previewModal(a.id) }, "Preview"),
      h("button", { class: "btn", type: "button", onclick: async (e) => busy(e.currentTarget, async () => {
        try { const copy = await post(`/api/assessments/${a.id}/duplicate`); toast("Duplicated as a new draft.", "good"); navigate(`/admin/assessments/${copy.id}`); } catch (err) { toastError(err); }
      }) }, "Duplicate"),
    ];
    if (a.status === "draft") actions.push(h("button", { class: "btn primary", type: "button", onclick: publish }, "Publish"));
    if (a.status === "published") actions.push(h("button", { class: "btn danger", type: "button", onclick: archive }, "Archive"));
    mount(headHost,
      pageHead({
        title: a.title,
        crumbs: [{ label: "Assessments", href: "#/admin/assessments" }, { label: a.title }],
        sub: [statusPill(a.display_status), "  ", a.access_start_at ? `${fmtDateTime(a.access_start_at)} → ${fmtDateTime(a.access_end_at)} IST · ${a.duration_minutes} min · ${plural(a.question_count, "question")}, ${a.total_points} pts` : "Not scheduled yet"],
        actions,
      }),
      tabs(TABS.map((t) => ({ ...t, count: t.id === "questions" ? a.question_count : undefined })), active, (id) => { active = id; history.replaceState(null, "", `#/admin/assessments/${a.id}?tab=${id}`); renderHead(); renderTab(); }));
  }

  async function publish() {
    if (!a.question_count) return toast("Add at least one question before publishing.", "bad");
    if (!a.access_start_at) return toast("Set the access window in Settings before publishing.", "bad");
    const ok = await confirmDialog({ title: "Publish assessment?", message: "Publishing locks the questions so results stay comparable. You can still change the schedule and student settings.", confirmLabel: "Publish" });
    if (!ok) return;
    try { await post(`/api/assessments/${a.id}/publish`); toast("Published. You can now invite candidates.", "good"); reload("candidates"); } catch (err) { toastError(err); }
  }
  async function archive() {
    const ok = await confirmDialog({ title: "Archive assessment?", message: "Candidates will no longer be able to start it. Existing results are kept.", confirmLabel: "Archive", danger: true });
    if (!ok) return;
    try { await post(`/api/assessments/${a.id}/archive`); toast("Archived."); reload(); } catch (err) { toastError(err); }
  }

  function renderTab() {
    const renderers = { settings: settingsTab, questions: questionsTab, candidates: candidatesTab, results: resultsTab, analytics: analyticsTab };
    mount(body, loading());
    Promise.resolve(renderers[active](a, body, reload)).catch((err) => mount(body, errorBox(err, renderTab)));
  }

  mount(main, headHost, body);
  renderHead();
  renderTab();
  void isDraft;
}

/* ------------------------------------------------------------------ settings */
function settingsTab(a, host, reload) {
  if (a.status === "archived") {
    return mount(host, h("div", { class: "alert info" }, "This assessment is archived. Duplicate it to reuse its questions."));
  }
  const form = settingsForm(a, { published: a.status === "published" });
  const save = h("button", { class: "btn primary", type: "button" }, "Save changes");
  save.addEventListener("click", () => busy(save, async () => {
    try { await patch(`/api/assessments/${a.id}`, form.values()); toast("Saved.", "good"); await reload("settings"); } catch (err) { toastError(err); }
  }));
  mount(host, h("section", { class: "panel" }, form.el, h("div", { class: "divider" }), save));
}

/* ------------------------------------------------------------------ questions */
function questionCard(q, n, { draft, total, onEdit, onMove, onDelete, onSave }) {
  const correct = Array.isArray(q.correct_answer) ? q.correct_answer : [q.correct_answer];
  return h("article", { class: "q-card" },
    h("div", { class: "q-num" }, n),
    h("div", { style: { minWidth: 0 } },
      h("div", { class: "q-text" }, q.question_text),
      h("div", { class: "q-meta" }, pill(TYPE_LABELS[q.question_type] || q.question_type, "accent"), q.topic ? pill(q.topic) : null, pill(`${q.points} pts`)),
      q.options ? h("ul", { class: "q-options" }, q.options.map((o) => h("li", { class: correct.includes(o.id) ? "correct" : null },
        h("span", { class: "oid" }, o.id), h("span", null, o.text), correct.includes(o.id) ? " ✓" : null))) : null,
      !q.options && q.grading_rubric ? h("p", { class: "small muted", style: { marginTop: "8px" } }, h("b", null, "Rubric: "), q.grading_rubric) : null),
    h("div", { class: "q-actions" },
      draft ? h("button", { class: "btn sm", type: "button", onclick: onEdit }, "Edit") : null,
      draft ? h("div", { class: "row", style: { gap: "2px" } },
        h("button", { class: "icon-btn", type: "button", "aria-label": "Move up", disabled: n === 1, onclick: () => onMove(-1) }, "↑"),
        h("button", { class: "icon-btn", type: "button", "aria-label": "Move down", disabled: n === total, onclick: () => onMove(1) }, "↓")) : null,
      h("button", { class: "btn sm ghost", type: "button", onclick: onSave }, "Save to bank"),
      draft ? h("button", { class: "btn sm ghost danger", type: "button", onclick: onDelete }, "Delete") : null));
}

/** Question editor used by assessments and the question bank. Returns body values() or throws. */
export function questionForm(q = {}, { withDifficulty = false } = {}) {
  q = q || {};
  const type = select(Object.entries(TYPE_LABELS).map(([value, label]) => ({ value, label })), q.question_type || "mcq_single");
  const text = textarea({ value: q.question_text || "", rows: 4, maxlength: 5000, placeholder: "Write the question…" });
  const topic = input({ value: q.topic || "", maxlength: 100, placeholder: "e.g. Exceptions" });
  const points = input({ type: "number", min: 0, max: 1000, step: "0.5", value: q.points ?? 1 });
  const difficulty = select(["beginner", "intermediate", "advanced"].map((d) => ({ value: d, label: d[0].toUpperCase() + d.slice(1) })), q.difficulty || "intermediate");
  const rubric = textarea({ value: q.grading_rubric || "", rows: 3, placeholder: "How points are awarded, e.g. 1 pt: defines X; 1 pt: gives an example…" });
  const model = textarea({ value: typeof q.correct_answer === "string" && !q.options ? q.correct_answer : "", rows: 2, placeholder: "Optional reference answer" });
  const explanation = textarea({ value: q.explanation || "", rows: 2, placeholder: "Optional explanation (never shown to students during the test)" });
  const correctSet = new Set(Array.isArray(q.correct_answer) ? q.correct_answer : q.correct_answer && q.options ? [q.correct_answer] : []);
  let options = (q.options || [{ id: "A", text: "" }, { id: "B", text: "" }, { id: "C", text: "" }, { id: "D", text: "" }]).map((o) => ({ ...o }));
  const optionsHost = h("div", { class: "stack", style: { gap: "8px" } });

  function renderOptions() {
    const multi = type.value === "mcq_multi";
    mount(optionsHost,
      options.map((o, i) => h("div", { class: "opt-row" },
        h("input", { type: multi ? "checkbox" : "radio", name: "correct", checked: correctSet.has(o.id), "aria-label": `Option ${o.id} is correct`,
          onchange: (e) => { if (!multi) correctSet.clear(); if (e.target.checked) correctSet.add(o.id); else correctSet.delete(o.id); } }),
        h("span", { class: "mono" }, o.id),
        h("input", { class: "input", value: o.text, placeholder: `Option ${o.id}`, oninput: (e) => { o.text = e.target.value; } }),
        h("button", { class: "icon-btn", type: "button", "aria-label": "Remove option", disabled: options.length <= 2,
          onclick: () => { correctSet.delete(o.id); options.splice(i, 1); options = options.map((x, j) => ({ ...x, id: String.fromCharCode(65 + j) })); renderOptions(); } }, "✕"))),
      options.length < 8 ? h("button", { class: "btn sm", type: "button", onclick: () => { options.push({ id: String.fromCharCode(65 + options.length), text: "" }); renderOptions(); } }, "+ Add option") : null,
      h("p", { class: "small muted" }, multi ? "Tick every correct option. Students must select exactly these to score." : "Select the one correct option."));
  }
  const mcqBlock = h("div", { class: "field" }, h("span", { class: "field-label" }, "Options and correct answer"), optionsHost);
  const textBlock = h("div", { class: "form" }, field("Grading rubric", rubric, "Used for manual marking and optional AI-assisted marking."), field("Reference answer", model));
  function toggle() {
    const isMcq = type.value.startsWith("mcq");
    mcqBlock.hidden = !isMcq;
    textBlock.hidden = isMcq;
    if (isMcq) { if (type.value === "mcq_single" && correctSet.size > 1) correctSet.clear(); renderOptions(); }
  }
  type.addEventListener("change", toggle);

  const el = h("div", { class: "form" },
    h("div", { class: "cols-3" }, field("Type", type), field("Topic", topic, "Used for strengths and weak areas"), field("Points", points)),
    withDifficulty ? field("Difficulty", difficulty) : null,
    field("Question", text), mcqBlock, textBlock, field("Explanation", explanation));
  toggle();

  function values() {
    const isMcq = type.value.startsWith("mcq");
    const out = { question_text: text.value.trim(), question_type: type.value, topic: topic.value.trim(), points: Number(points.value), explanation: explanation.value };
    if (!out.question_text) throw new Error("Write the question text.");
    if (isMcq) {
      const filled = options.filter((o) => o.text.trim());
      if (filled.length < 2) throw new Error("Add at least two options.");
      const ids = filled.map((o) => o.id);
      const chosen = [...correctSet].filter((id) => ids.includes(id));
      if (!chosen.length) throw new Error("Mark the correct option.");
      out.options = filled.map((o) => ({ id: o.id, text: o.text.trim() }));
      out.correct_answer = type.value === "mcq_single" ? chosen[0] : chosen.sort();
      out.grading_rubric = "";
    } else {
      out.options = null;
      out.correct_answer = model.value.trim() || null;
      out.grading_rubric = rubric.value;
    }
    if (withDifficulty) out.difficulty = difficulty.value;
    return out;
  }
  return { el, values };
}

async function questionsTab(a, host, reload) {
  const draft = a.status === "draft";
  const qs = a.questions;
  const openEditor = (q) => {
    const form = questionForm(q);
    openModal({ title: q ? "Edit question" : "Add question", wide: true, body: form.el, actions: [
      { label: "Cancel" },
      { label: q ? "Save question" : "Add question", primary: true, onClick: async (close) => {
        const body = form.values();
        if (q) await patch(`/api/questions/${q.id}`, body); else await post(`/api/assessments/${a.id}/questions`, body);
        close(); toast(q ? "Question saved." : "Question added.", "good"); await reload("questions");
      } },
    ] });
  };
  const move = async (index, dir) => {
    const ids = qs.map((q) => q.id);
    const [moved] = ids.splice(index, 1);
    ids.splice(index + dir, 0, moved);
    try { await post(`/api/assessments/${a.id}/questions/reorder`, { question_ids: ids }); await reload("questions"); } catch (err) { toastError(err); }
  };
  const remove = async (q) => {
    if (!(await confirmDialog({ title: "Delete question?", message: "This removes it from the draft. It cannot be undone.", confirmLabel: "Delete", danger: true }))) return;
    try { await del(`/api/questions/${q.id}`); toast("Question deleted."); await reload("questions"); } catch (err) { toastError(err); }
  };
  const saveToBank = (q) => {
    const diff = select(["beginner", "intermediate", "advanced"], "intermediate");
    openModal({ title: "Save to question bank", body: field("Difficulty", diff, "A copy is saved; editing it later does not change this assessment."), actions: [
      { label: "Cancel" },
      { label: "Save copy", primary: true, onClick: async (close) => { await post(`/api/questions/${q.id}/save-to-bank`, { difficulty: diff.value }); close(); toast("Saved to the question bank.", "good"); } },
    ] });
  };

  const toolbar = draft ? h("div", { class: "toolbar" },
    h("button", { class: "btn primary", type: "button", onclick: () => openEditor(null) }, "+ Add question"),
    h("button", { class: "btn", type: "button", onclick: () => generateModal(a, reload) }, "✦ Generate with AI"),
    h("button", { class: "btn", type: "button", onclick: () => bankPickerModal(a, reload) }, "Add from bank"),
    h("button", { class: "btn", type: "button", onclick: () => randomModal(a, reload) }, "Random from bank"),
    h("span", { class: "spacer" }),
    h("span", { class: "muted small" }, `${plural(qs.length, "question")} · ${a.total_points} points`))
    : h("div", { class: "alert info", style: { marginBottom: "14px" } }, "Questions are locked because this assessment is published. Duplicate it to make an editable copy.");

  mount(host, toolbar,
    qs.length
      ? h("div", { class: "q-list" }, qs.map((q, i) => questionCard(q, i + 1, { draft, total: qs.length, onEdit: () => openEditor(q), onMove: (d) => move(i, d), onDelete: () => remove(q), onSave: () => saveToBank(q) })))
      : emptyState("No questions yet", "Add questions by hand, generate them with AI, or pick them from your question bank."));
}

function generateModal(a, reload) {
  const mcq = input({ type: "number", min: 0, max: 50, value: 5 });
  const maq = input({ type: "number", min: 0, max: 50, value: 0 });
  openModal({ title: "Generate questions with AI", body: h("div", { class: "form" },
    h("p", { class: "muted" }, `Questions are generated from the title, category, description and difficulty of "${a.title}". Review them before publishing.`),
    h("div", { class: "cols-2" }, field("Single-answer questions", mcq), field("Multiple-answer questions", maq))),
  actions: [{ label: "Cancel" }, { label: "Generate", primary: true, onClick: async (close) => {
    await post(`/api/assessments/${a.id}/generate-questions`, { mcq_count: Number(mcq.value), maq_count: Number(maq.value) });
    close(); toast("Questions added. Review each one before publishing.", "good"); await reload("questions");
  } }] });
}

async function bankPickerModal(a, reload) {
  const chosen = new Set();
  const list = h("div");
  const search = input({ type: "search", placeholder: "Search the bank" });
  const topic = select([{ value: "", label: "All topics" }], "");
  async function load() {
    const data = await get("/api/bank/questions", { search: search.value, topic: topic.value, limit: 100 });
    if (topic.options.length === 1) data.topics.forEach((t) => topic.appendChild(h("option", { value: t.topic }, `${t.topic} (${t.count})`)));
    mount(list, table({
      rows: data.items,
      empty: emptyState("No bank questions", "Save questions to the bank from any assessment, or add them on the Question bank page."),
      columns: [
        { label: "", render: (q) => h("input", { type: "checkbox", checked: chosen.has(q.id), "aria-label": "Select", onchange: (e) => (e.target.checked ? chosen.add(q.id) : chosen.delete(q.id)) }) },
        { label: "Question", render: (q) => h("div", { style: { maxWidth: "48ch", whiteSpace: "normal" } }, q.question_text) },
        { label: "Topic", render: (q) => q.topic || "—" },
        { label: "Type", render: (q) => TYPE_LABELS[q.question_type] },
        { label: "Level", render: (q) => q.difficulty },
      ],
    }));
  }
  search.addEventListener("input", () => load());
  topic.addEventListener("change", () => load());
  openModal({ title: "Add questions from the bank", wide: true, body: h("div", { class: "stack" }, h("div", { class: "toolbar", style: { margin: 0 } }, search, topic), list),
    actions: [{ label: "Cancel" }, { label: "Add selected", primary: true, onClick: async (close) => {
      if (!chosen.size) throw new Error("Select at least one question.");
      await post(`/api/assessments/${a.id}/questions/from-bank`, { bank_question_ids: [...chosen] });
      close(); toast(`${plural(chosen.size, "question")} copied into the assessment.`, "good"); await reload("questions");
    } }] });
  load().catch(toastError);
}

async function randomModal(a, reload) {
  const data = await get("/api/bank/questions", { limit: 1 }).catch(() => ({ topics: [] }));
  const count = input({ type: "number", min: 1, max: 100, value: 5 });
  const topic = select([{ value: "", label: "Any topic" }, ...data.topics.map((t) => ({ value: t.topic, label: `${t.topic} (${t.count})` }))], "");
  const level = select([{ value: "", label: "Any difficulty" }, "beginner", "intermediate", "advanced"], "");
  openModal({ title: "Add random questions from the bank", body: h("div", { class: "form" },
    h("p", { class: "muted" }, "Picks questions at random that are not already in this assessment."),
    h("div", { class: "cols-3" }, field("How many", count), field("Topic", topic), field("Difficulty", level))),
  actions: [{ label: "Cancel" }, { label: "Add questions", primary: true, onClick: async (close) => {
    await post(`/api/assessments/${a.id}/questions/random-from-bank`, { count: Number(count.value), topic: topic.value || null, difficulty: level.value || null });
    close(); toast("Random questions added.", "good"); await reload("questions");
  } }] });
}

export async function previewModal(id) {
  const p = await post(`/api/assessments/${id}/preview`).catch(toastError);
  if (!p) return;
  openModal({ title: `Student preview: ${p.title}`, wide: true, body: h("div", { class: "stack" },
    h("div", { class: "alert info" }, "This is exactly what a student receives: no correct answers or rubrics."),
    p.instructions ? h("p", { class: "muted", style: { whiteSpace: "pre-wrap" } }, p.instructions) : null,
    p.questions.map((q) => h("div", { class: "question-card" },
      h("div", { class: "qhead" }, h("span", null, `Question ${q.number}`), h("span", null, `${q.points} pts · ${TYPE_LABELS[q.question_type]}`)),
      h("div", { class: "qtext" }, q.question_text),
      q.options ? h("div", { class: "choices" }, q.options.map((o) => h("div", { class: "choice" }, h("span", { class: "key" }, o.id), h("span", null, o.text))))
        : h("div", { class: "textarea answer-text", style: { color: "var(--faint)" } }, "Written answer area")))) });
}

/* ------------------------------------------------------------------ candidates */
async function candidatesTab(a, host, reload) {
  const rows = await get(`/api/assessments/${a.id}/assignments`);
  const canInvite = a.status === "published";
  const sendInvite = async (r, btn) => busy(btn, async () => {
    try {
      const res = await post(`/api/assignments/${r.id}/invite`);
      toast(res.message, ["sent", "outbox"].includes(res.email_status) ? "good" : "bad");
      await reload("candidates");
    } catch (err) { toastError(err); }
  });
  const revoke = async (r) => {
    if (!(await confirmDialog({ title: `Revoke ${r.candidate_name}'s access?`, message: "Their invitation stops working immediately. Results already submitted are kept.", confirmLabel: "Revoke", danger: true }))) return;
    try { await post(`/api/assignments/${r.id}/revoke`); toast("Access revoked."); await reload("candidates"); } catch (err) { toastError(err); }
  };
  mount(host,
    h("div", { class: "toolbar" },
      h("button", { class: "btn primary", type: "button", disabled: a.status === "archived", onclick: () => assignModal(a, reload) }, "+ Assign candidates"),
      h("button", { class: "btn", type: "button", disabled: a.status === "archived", onclick: () => assignBatchModal(a, reload) }, "Assign a batch"),
      h("span", { class: "spacer" }),
      !canInvite ? h("span", { class: "muted small" }, "Invitations can be sent after publishing.") : h("span", { class: "muted small" }, `${plural(rows.length, "candidate")} assigned`)),
    table({
      rows,
      empty: emptyState("Nobody assigned yet", "Assign individual candidates or a whole batch."),
      columns: [
        { label: "Candidate", render: (r) => [h("div", null, r.candidate_name), h("div", { class: "sub" }, r.candidate_email)] },
        { label: "Status", render: (r) => statusPill(r.status) },
        { label: "Invitation", render: (r) => (r.invitation_status ? statusPill(r.invitation_status) : h("span", { class: "muted small" }, "Not sent")) },
        { label: "Code", render: (r) => (r.passcode_status ? statusPill(r.passcode_status) : "—") },
        { label: "Last email", render: (r) => (r.last_email_status ? statusPill(r.last_email_status) : "—") },
        { label: "Attempts", class: "num", render: (r) => `${r.attempts_used} / ${r.max_attempts}` },
        { label: "Score", class: "num", render: (r) => (r.result_id ? h("a", { href: `#/admin/results/${r.result_id}` }, fmtPct(r.percentage)) : "—") },
        { label: "", class: "actions", render: (r) => [
            canInvite && !["completed", "revoked"].includes(r.status)
              ? h("button", { class: "btn sm", type: "button", onclick: (e) => sendInvite(r, e.currentTarget) }, r.invitation_status ? "Resend" : "Send invite") : null,
            !["completed", "revoked"].includes(r.status) ? h("button", { class: "btn sm ghost danger", type: "button", onclick: () => revoke(r) }, "Revoke") : null,
          ] },
      ],
    }),
    canInvite ? h("p", { class: "small muted", style: { marginTop: "10px" } }, "Resending issues a new link and code; the previous ones stop working immediately. Codes are never shown here.") : null);
}

async function assignModal(a, reload) {
  const people = await get("/api/candidates", { active_only: true });
  const chosen = new Set();
  const list = h("div", { style: { maxHeight: "50vh", overflowY: "auto" } });
  const search = input({ type: "search", placeholder: "Search candidates" });
  const invite = checkbox("Send invitation emails now", a.status === "published", { disabled: a.status !== "published" });
  function render() {
    const q = search.value.toLowerCase();
    const rows = people.filter((p) => !q || p.name.toLowerCase().includes(q) || p.email.toLowerCase().includes(q));
    mount(list, table({ rows, empty: emptyState("No active candidates", "Add candidates on the Candidates page first."), columns: [
      { label: "", render: (p) => h("input", { type: "checkbox", checked: chosen.has(p.id), "aria-label": `Select ${p.name}`, onchange: (e) => (e.target.checked ? chosen.add(p.id) : chosen.delete(p.id)) }) },
      { label: "Name", render: (p) => p.name },
      { label: "Email", render: (p) => p.email },
      { label: "Batches", render: (p) => (p.batches || []).map((b) => pill(b.name)) },
    ] }));
  }
  search.addEventListener("input", render);
  openModal({ title: "Assign candidates", wide: true, body: h("div", { class: "stack" }, search, list, invite.el),
    actions: [{ label: "Cancel" }, { label: "Assign", primary: true, onClick: async (close) => {
      if (!chosen.size) throw new Error("Select at least one candidate.");
      const res = await post(`/api/assessments/${a.id}/assign`, { candidate_ids: [...chosen], send_invitations: invite.input.checked });
      const failed = res.invitations.filter((i) => i.error || i.email_status === "failed").length;
      close(); toast(`${plural(res.assignment_ids.length, "candidate")} assigned${invite.input.checked ? `, ${res.invitations.length - failed} invited` : ""}.`, failed ? "bad" : "good");
      await reload("candidates");
    } }] });
  render();
}

async function assignBatchModal(a, reload) {
  const batches = await get("/api/batches");
  if (!batches.length) return toast("Create a batch on the Batches page first.", "bad");
  const pick = select(batches.map((b) => ({ value: b.id, label: `${b.name} (${b.member_count})` })), batches[0].id);
  const invite = checkbox("Send invitation emails now", a.status === "published", { disabled: a.status !== "published" });
  openModal({ title: "Assign a batch", body: h("div", { class: "form" }, field("Batch", pick, "Every active member is assigned. People already assigned are left as they are."), invite.el),
    actions: [{ label: "Cancel" }, { label: "Assign batch", primary: true, onClick: async (close) => {
      const res = await post(`/api/assessments/${a.id}/assign-batch`, { batch_id: Number(pick.value), send_invitations: invite.input.checked });
      close(); toast(`${plural(res.assignment_ids.length, "candidate")} assigned.`, "good"); await reload("candidates");
    } }] });
}

/* ------------------------------------------------------------------ results */
async function resultsTab(a, host) {
  const data = await get("/api/admin/results", { assessment_id: a.id, limit: 500 });
  const exportBtn = (fmt, label) => h("button", { class: "btn", type: "button", onclick: (e) => busy(e.currentTarget, () => download(`/api/admin/assessments/${a.id}/export`, { format: fmt }, `results.${fmt}`).catch(toastError)) }, label);
  mount(host,
    h("div", { class: "toolbar" }, h("span", { class: "muted" }, plural(data.total, "result")), h("span", { class: "spacer" }), exportBtn("csv", "Export CSV"), exportBtn("xlsx", "Export Excel")),
    table({
      rows: data.items,
      empty: emptyState("No results yet", "Results appear when candidates submit."),
      onRowClick: (r) => navigate(`/admin/results/${r.result_id}`),
      columns: [
        { label: "Candidate", render: (r) => [h("div", null, r.candidate_name), h("div", { class: "sub" }, r.candidate_email)] },
        { label: "Score", class: "num", render: (r) => `${r.raw_score} / ${r.max_score}` },
        { label: "%", class: "num", render: (r) => fmtPct(r.percentage) },
        { label: "Category", render: (r) => (r.result_status === "final" ? categoryPill(r.category) : statusPill("provisional")) },
        { label: "Flags", class: "num", render: (r) => (r.proctoring_mode === "off" ? "—" : r.proctoring_flags ? pill(String(r.proctoring_flags), "bad") : pill("0", "good")) },
        { label: "Feedback", render: (r) => statusPill(r.student_feedback_status || "pending") },
        { label: "Submitted", render: (r) => [fmtDateTime(r.submitted_at), r.auto_submitted ? h("div", { class: "sub" }, "auto-submitted") : null] },
      ],
    }));
}

/* ------------------------------------------------------------------ analytics */
async function analyticsTab(a, host) {
  const [an, group] = await Promise.all([get(`/api/admin/assessments/${a.id}/analytics`), get(`/api/admin/assessments/${a.id}/group-feedback`)]);
  const dist = Array.from({ length: 10 }, (_, i) => ({ label: String(i * 10), value: an.score_distribution[`${i * 10}-${i * 10 + 10}%`] || 0 }));
  const groupHost = h("div");
  function renderGroup(g) {
    if (!g) return mount(groupHost, emptyState("No group report yet", an.valid_results ? "Generate an AI summary of common strengths, gaps and training priorities." : "A group report needs at least one final result."));
    if (g.generation_status === "failed") return mount(groupHost, h("div", { class: "alert bad" }, `Generation failed: ${g.error_message || "unknown error"}`));
    const c = g.content || {};
    const list = (title, items) => (items?.length ? h("div", null, h("h3", { style: { marginBottom: "6px" } }, title), h("ul", { style: { margin: 0, paddingLeft: "18px" } }, items.map((i) => h("li", null, i)))) : null);
    mount(groupHost, h("div", { class: "stack" },
      h("p", null, c.overview),
      h("div", { class: "grid cols-3" }, list("Common strengths", c.common_strengths), list("Common gaps", c.common_gaps), list("Training priorities", c.training_priorities)),
      c.candidates_needing_support?.length ? h("div", null, h("h3", { style: { marginBottom: "6px" } }, "Candidates who may need support"),
        h("ul", { style: { margin: 0, paddingLeft: "18px" } }, c.candidates_needing_support.map((s) => h("li", null, h("b", null, s.candidate_name || `${s.candidate_ref} (unverified)`), ": ", s.evidence)))) : null,
      c.limitations ? h("p", { class: "small muted" }, c.limitations) : null,
      h("p", { class: "small faint" }, `Generated ${fmtDateTime(g.generated_at)} by ${g.model_name}. Statistics are computed by the application; AI only summarises them.`)));
  }
  const genBtn = h("button", { class: "btn", type: "button", disabled: !an.valid_results }, group.group ? "Regenerate" : "✦ Generate group report");
  genBtn.addEventListener("click", () => busy(genBtn, async () => {
    try { const res = await post(`/api/admin/assessments/${a.id}/group-feedback`); renderGroup(res.group); } catch (err) { toastError(err); }
  }));
  mount(host,
    h("div", { class: "grid cols-4", style: { marginBottom: "14px" } },
      stat("Assigned", an.total_assigned, `${an.invitations_sent} invited`),
      stat("Completed", an.completed, `${an.pending} pending`),
      stat("Average", fmtPct(an.average_percent), `${an.valid_results} final results`),
      stat("Range", an.valid_results ? `${fmtPct(an.lowest_percent)} – ${fmtPct(an.highest_percent)}` : "—", "lowest – highest")),
    h("div", { class: "grid cols-2", style: { marginBottom: "14px" } },
      h("section", { class: "panel" }, h("div", { class: "panel-head" }, h("h2", null, "Score distribution"), h("span", { class: "hint" }, "candidates per 10% band")),
        an.valid_results ? barChart(dist, { xTitle: "score %" }) : emptyState("No final results yet")),
      h("section", { class: "panel" }, h("div", { class: "panel-head" }, h("h2", null, "Topic performance"), h("span", { class: "hint" }, "average across final results")),
        an.topics.length ? percentBars(an.topics.map((t) => ({ label: t.topic, percent: t.average_percent, hint: `${t.points_earned} / ${t.points_possible} points` }))) : emptyState("No data yet"))),
    h("section", { class: "panel", style: { marginBottom: "14px" } },
      h("div", { class: "panel-head" }, h("h2", null, "Questions"), h("span", { class: "hint" }, "Low full-credit rates point to hard or unclear questions")),
      table({ rows: an.questions, empty: emptyState("No answers yet"), columns: [
        { label: "#", class: "num", render: (q) => q.number },
        { label: "Question", render: (q) => h("div", { style: { maxWidth: "52ch", whiteSpace: "normal" } }, q.question_text) },
        { label: "Topic", render: (q) => q.topic },
        { label: "Full credit", class: "num", render: (q) => fmtPct(q.full_credit_rate_percent) },
        { label: "Unanswered", class: "num", render: (q) => q.unanswered },
      ] })),
    h("section", { class: "panel" },
      h("div", { class: "panel-head" }, h("h2", null, "AI group report"), genBtn), groupHost));
  renderGroup(group.group);
}
