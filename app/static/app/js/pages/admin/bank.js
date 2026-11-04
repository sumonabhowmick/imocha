import { del, get, patch, post } from "../../core/api.js";
import { adminLayout } from "../../core/layout.js";
import { TYPE_LABELS, fmtDate, plural } from "../../core/format.js";
import { confirmDialog, emptyState, errorBox, h, input, loading, mount, openModal, pageHead, pill, select, table, toast, toastError } from "../../core/ui.js";
import { questionForm } from "./assessment_editor.js";

export async function bankPage() {
  const main = adminLayout("bank");
  const filters = { search: "", topic: "", question_type: "", difficulty: "" };
  const listHost = h("div");
  const search = input({ type: "search", class: "input grow", placeholder: "Search question text" });
  const topic = select([{ value: "", label: "All topics" }], "");
  const type = select([{ value: "", label: "All types" }, ...Object.entries(TYPE_LABELS).map(([value, label]) => ({ value, label }))], "");
  const level = select([{ value: "", label: "All levels" }, "beginner", "intermediate", "advanced"], "");
  let topicsLoaded = false;

  async function load() {
    mount(listHost, loading());
    try {
      const data = await get("/api/bank/questions", { ...filters, limit: 200 });
      if (!topicsLoaded) {
        data.topics.forEach((t) => topic.appendChild(h("option", { value: t.topic }, `${t.topic} (${t.count})`)));
        topicsLoaded = true;
      }
      mount(listHost,
        h("p", { class: "muted small", style: { marginBottom: "8px" } }, plural(data.total, "question")),
        table({
          rows: data.items,
          empty: emptyState("The bank is empty", "Add reusable questions here, or use “Save to bank” on any assessment question.", h("button", { class: "btn primary", onclick: () => edit(null) }, "+ Add question")),
          columns: [
            { label: "Question", render: (q) => h("div", { style: { maxWidth: "60ch", whiteSpace: "normal" } }, q.question_text) },
            { label: "Topic", render: (q) => q.topic || "—" },
            { label: "Type", render: (q) => pill(TYPE_LABELS[q.question_type], "accent") },
            { label: "Level", render: (q) => q.difficulty },
            { label: "Points", class: "num", render: (q) => q.points },
            { label: "Updated", render: (q) => fmtDate(q.updated_at) },
            { label: "", class: "actions", render: (q) => [
                h("button", { class: "btn sm", type: "button", onclick: () => edit(q) }, "Edit"),
                h("button", { class: "btn sm ghost danger", type: "button", onclick: () => remove(q) }, "Delete"),
              ] },
          ],
        }));
    } catch (err) { mount(listHost, errorBox(err, load)); }
  }

  function edit(q) {
    const form = questionForm(q || {}, { withDifficulty: true });
    openModal({ title: q ? "Edit bank question" : "Add bank question", wide: true, body: form.el, actions: [
      { label: "Cancel" },
      { label: q ? "Save" : "Add to bank", primary: true, onClick: async (close) => {
        const body = form.values();
        if (q) await patch(`/api/bank/questions/${q.id}`, body); else await post("/api/bank/questions", body);
        close(); toast(q ? "Saved. Assessments that already use it are unchanged." : "Added to the bank.", "good"); load();
      } },
    ] });
  }
  async function remove(q) {
    if (!(await confirmDialog({ title: "Delete from bank?", message: "Assessments that already copied this question keep their copy.", confirmLabel: "Delete", danger: true }))) return;
    try { await del(`/api/bank/questions/${q.id}`); toast("Deleted from the bank."); load(); } catch (err) { toastError(err); }
  }

  let timer;
  search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { filters.search = search.value; load(); }, 250); });
  topic.addEventListener("change", () => { filters.topic = topic.value; load(); });
  type.addEventListener("change", () => { filters.question_type = type.value; load(); });
  level.addEventListener("change", () => { filters.difficulty = level.value; load(); });

  mount(main,
    pageHead({ title: "Question bank", sub: "Reusable questions. Adding one to an assessment copies it, so editing the bank never changes published tests or past results.",
      actions: [h("button", { class: "btn primary", type: "button", onclick: () => edit(null) }, "+ Add question")] }),
    h("div", { class: "toolbar" }, search, topic, type, level),
    listHost);
  load();
}
