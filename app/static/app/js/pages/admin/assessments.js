import { get, post } from "../../core/api.js";
import { adminLayout } from "../../core/layout.js";
import { fmtDateTime, isoToLocalInput, localInputToISO } from "../../core/format.js";
import { navigate } from "../../core/router.js";
import { busy, checkbox, emptyState, errorBox, field, h, input, loading, mount, pageHead, pill, select, statusPill, table, tabs, textarea, toast } from "../../core/ui.js";

const STATUS_TABS = ["all", "draft", "scheduled", "active", "completed", "archived"];

export async function assessmentsPage(_p, query) {
  const main = adminLayout("assessments");
  mount(main, loading());
  let all;
  try { all = await get("/api/assessments"); } catch (err) { return mount(main, errorBox(err, () => assessmentsPage(_p, query))); }

  let status = STATUS_TABS.includes(query.status) ? query.status : "all";
  let search = "";
  const host = h("div");
  const searchBox = h("input", { class: "input grow", type: "search", placeholder: "Search by title or category", oninput: (e) => { search = e.target.value.toLowerCase(); render(); } });

  function render() {
    const rows = all.filter((a) => (status === "all" || a.display_status === status)
      && (!search || a.title.toLowerCase().includes(search) || (a.category || "").toLowerCase().includes(search)));
    mount(host,
      tabs(STATUS_TABS.map((s) => ({ id: s, label: s[0].toUpperCase() + s.slice(1), count: s === "all" ? all.length : all.filter((a) => a.display_status === s).length })), status, (s) => { status = s; render(); }),
      h("div", { class: "toolbar" }, searchBox),
      table({
        rows,
        empty: all.length ? emptyState("No matching assessments") : emptyState("No assessments yet", "Create your first assessment to start inviting candidates.", h("a", { class: "btn primary", href: "#/admin/assessments/new" }, "+ New assessment")),
        onRowClick: (a) => navigate(`/admin/assessments/${a.id}`),
        columns: [
          { label: "Assessment", render: (a) => [h("div", { style: { fontWeight: 600 } }, a.title), h("div", { class: "sub" }, [a.category, a.difficulty].filter(Boolean).join(" · "))] },
          { label: "Status", render: (a) => statusPill(a.display_status) },
          { label: "Access window (IST)", render: (a) => (a.access_start_at ? h("span", { class: "small" }, `${fmtDateTime(a.access_start_at)} → ${fmtDateTime(a.access_end_at)}`) : h("span", { class: "muted small" }, "Not scheduled")) },
          { label: "Questions", class: "num", render: (a) => `${a.question_count} · ${a.total_points} pts` },
          { label: "Duration", class: "num", render: (a) => `${a.duration_minutes} min` },
          { label: "Settings", render: (a) => h("div", { class: "row", style: { gap: "4px" } },
              a.shuffle_questions || a.shuffle_options ? pill("Shuffled", "neutral") : null,
              a.reminder_minutes_before_end ? pill("Reminder", "neutral") : null) },
        ],
      }));
  }
  mount(main,
    pageHead({ title: "Assessments", sub: "Build quizzes, schedule access windows and publish them to candidates.", actions: [h("a", { class: "btn primary", href: "#/admin/assessments/new" }, "+ New assessment")] }),
    host);
  render();
}

/**
 * Assessment settings form. `published` limits editing to fields the server allows
 * after publication. Returns {el, values()}.
 */
export function settingsForm(a = {}, { published = false } = {}) {
  const title = input({ value: a.title || "", maxlength: 200, required: true, placeholder: "Python Developer Assessment" });
  const category = input({ value: a.category || "", maxlength: 100, placeholder: "Python" });
  const difficulty = select([{ value: "beginner", label: "Beginner" }, { value: "intermediate", label: "Intermediate" }, { value: "advanced", label: "Advanced" }], a.difficulty || "intermediate", { disabled: published });
  const description = textarea({ value: a.description || "", maxlength: 5000, rows: 3, placeholder: "Shown on the student's assessment page." });
  const instructions = textarea({ value: a.instructions || "", maxlength: 5000, rows: 3, placeholder: "Rules, allowed resources, what to do if something goes wrong…" });
  const start = input({ type: "datetime-local", value: isoToLocalInput(a.access_start_at) });
  const end = input({ type: "datetime-local", value: isoToLocalInput(a.access_end_at) });
  const duration = input({ type: "number", min: 1, max: 600, value: a.duration_minutes || 45 });
  const attempts = input({ type: "number", min: 1, max: 10, value: a.max_attempts || 1 });
  const policy = select([
    { value: "finish_after_window", label: "Let started attempts finish after the window closes" },
    { value: "hard_end_at_window", label: "End every attempt when the window closes" },
  ], a.completion_policy || "finish_after_window");
  const shuffleQ = checkbox("Shuffle question order for each student", a.shuffle_questions);
  const shuffleO = checkbox("Shuffle answer options for each student", a.shuffle_options);
  const reminderOn = checkbox("Email students who have not started", Boolean(a.reminder_minutes_before_end));
  const reminder = input({ type: "number", min: 5, max: 10080, value: a.reminder_minutes_before_end || 60, style: { width: "110px" } });

  const el = h("div", { class: "form" },
    published ? h("div", { class: "alert info" }, "This assessment is published, so its questions and difficulty are locked. Schedule, duration and student settings can still change; they apply to attempts started afterwards.") : null,
    h("div", { class: "cols-2" }, field("Title", title), field("Category", category, "e.g. Python, SQL, Aptitude")),
    h("div", { class: "cols-2" }, field("Difficulty", difficulty), field("Maximum attempts per candidate", attempts)),
    field("Description", description),
    field("Instructions for candidates", instructions),
    h("div", { class: "divider" }),
    h("h3", null, "Schedule"),
    h("div", { class: "cols-3" },
      field("Access window opens (IST)", start),
      field("Access window closes (IST)", end),
      field("Duration (minutes)", duration, "Time allowed once a candidate starts.")),
    field("When the window closes", policy),
    h("div", { class: "divider" }),
    h("h3", null, "Integrity"),
    h("div", { class: "cols-2" },
      h("div", { class: "field" }, h("span", { class: "field-label" }, "Camera proctoring"),
        h("div", null, pill("Always on", "good")),
        h("div", { class: "hint" }, "Every assessment requires the student's webcam. Face checks, tab switches and fullscreen exits are flagged for your review; flags never change scores.")),
      h("div", { class: "field" }, h("span", { class: "field-label" }, "Randomization"), shuffleQ.el, shuffleO.el)),
    h("div", { class: "field" }, h("span", { class: "field-label" }, "Reminder"),
      h("div", { class: "row" }, reminderOn.el, reminder, h("span", { class: "muted small" }, "minutes before the window closes"))));

  function values() {
    const out = {
      title: title.value.trim(),
      category: category.value.trim(),
      description: description.value,
      instructions: instructions.value,
      duration_minutes: Number(duration.value),
      max_attempts: Number(attempts.value),
      completion_policy: policy.value,
      shuffle_questions: shuffleQ.input.checked,
      shuffle_options: shuffleO.input.checked,
      reminder_minutes_before_end: reminderOn.input.checked ? Number(reminder.value) : null,
      access_start_at: localInputToISO(start.value),
      access_end_at: localInputToISO(end.value),
    };
    if (!published) out.difficulty = difficulty.value;
    if (!out.title) throw new Error("Give the assessment a title.");
    if ((out.access_start_at && !out.access_end_at) || (!out.access_start_at && out.access_end_at)) throw new Error("Set both the opening and closing time, or neither.");
    return out;
  }
  return { el, values };
}

export async function newAssessmentPage() {
  const main = adminLayout("assessments");
  const form = settingsForm();
  const save = h("button", { class: "btn primary", type: "button" }, "Create draft");
  save.addEventListener("click", () => busy(save, async () => {
    try {
      const body = form.values();
      const created = await post("/api/assessments", body);
      toast("Draft created. Add questions next.", "good");
      navigate(`/admin/assessments/${created.id}?tab=questions`);
    } catch (err) { toast(err.message, "bad"); }
  }));
  mount(main,
    pageHead({ title: "New assessment", crumbs: [{ label: "Assessments", href: "#/admin/assessments" }, { label: "New" }], sub: "Start with the basics. You can add questions, candidates and publish afterwards." }),
    h("section", { class: "panel" }, form.el, h("div", { class: "divider" }), h("div", { class: "row" }, save, h("a", { class: "btn ghost", href: "#/admin/assessments" }, "Cancel"))));
}
