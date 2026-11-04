// Taking an assessment: overview -> (camera setup) -> questions -> submitted.
// The server is the only clock; the countdown here is a display that re-syncs regularly.
import { get, post, put } from "../../core/api.js";
import { bareLayout, studentLayout } from "../../core/layout.js";
import { TYPE_LABELS, fmtDateTime, fmtDuration, plural } from "../../core/format.js";
import { navigate } from "../../core/router.js";
import { busy, checkbox, confirmDialog, errorBox, h, loading, mount, pageHead, pill, toast } from "../../core/ui.js";
import { Proctor } from "../../proctor.js";

export async function testPage(params) {
  const main = studentLayout("home");
  mount(main, loading());
  let info;
  try { info = await get(`/api/portal/assessments/${params.id}`); } catch (err) { return mount(main, errorBox(err, () => testPage(params))); }
  // Camera proctoring is mandatory for every assessment.
  const proctor = new Proctor();
  const resuming = Boolean(info.in_progress_attempt_id);

  const kv = (k, v) => [h("dt", null, k), h("dd", null, v)];
  const facts = h("dl", { class: "kv" },
    kv("Questions", `${info.question_count} (${info.total_points} points)`),
    kv("Time allowed", `${info.duration_minutes} minutes once you start`),
    kv("Access window", info.access_start_display ? `${info.access_start_display} – ${info.access_end_display}` : "Not scheduled"),
    kv("Attempts", `${info.attempts_used} of ${info.max_attempts} used`),
    kv("When the window closes", info.completion_policy === "hard_end_at_window" ? "Your attempt ends when the window closes" : "You keep your full time even if the window closes"),
    kv("Proctoring", "Webcam required"));

  const startBtn = h("button", { class: "btn primary", type: "button", disabled: true }, resuming ? "Resume assessment" : "Start assessment");
  const setup = cameraSetup(info, proctor, startBtn);
  startBtn.addEventListener("click", () => busy(startBtn, async () => {
    if (!(setup.cameraOk() && setup.consent())) {
      return toast("Turn on your camera and accept the proctoring notice to start.", "bad");
    }
    try {
      document.documentElement.requestFullscreen?.().catch(() => {});
      const attempt = await post(`/api/portal/assessments/${params.id}/attempts`, { proctoring_consent: true });
      proctor.begin(attempt.attempt_id, { cameraEnabled: true });
      await runAttempt(attempt.attempt_id, info, proctor);
    } catch (err) { toast(err.message, "bad"); }
  }));

  mount(main,
    pageHead({ title: info.title, crumbs: [{ label: "My assessments", href: "#/student" }, { label: info.title }], sub: info.description || null }),
    h("div", { class: "grid wide-left" },
      h("section", { class: "panel stack" },
        h("h2", null, "Before you start"),
        facts,
        info.instructions ? h("div", { class: "alert info", style: { whiteSpace: "pre-wrap" } }, info.instructions) : null,
        h("ul", { class: "muted", style: { margin: 0, paddingLeft: "18px", display: "grid", gap: "4px" } },
          h("li", null, "Your answers save automatically as you go."),
          h("li", null, "The timer keeps running if you close the page; you can come back and resume."),
          h("li", null, "When time runs out, your saved answers are submitted automatically.")),
        !info.can_start && !resuming ? h("div", { class: "alert warn" }, info.start_blocked_reason || "This assessment cannot be started right now.") : null,
        h("div", { class: "row" }, startBtn, h("a", { class: "btn ghost", href: "#/student" }, "Back"))),
      setup.el));
}

function cameraSetup(info, proctor, startBtn) {
  const video = h("video", { autoplay: true, muted: true, playsinline: true });
  const status = h("span", { class: "camera-status" }, "Camera off");
  let ok = false;
  proctor.onStatus = (text) => { status.textContent = text; };
  const agree = checkbox("I agree to camera proctoring for this attempt. Face checks run on my device; only flagged moments are saved as small snapshots for the administrator to review.", false);
  const enable = h("button", { class: "btn", type: "button" }, "Turn on camera");
  enable.addEventListener("click", () => busy(enable, async () => {
    ok = await proctor.startCamera(video);
    if (ok) enable.textContent = "Camera on";
    refresh();
  }));
  const refresh = () => {
    startBtn.disabled = !(ok && agree.input.checked && (info.can_start || info.in_progress_attempt_id));
  };
  agree.input.addEventListener("change", refresh);
  refresh();
  const el = h("section", { class: "panel stack" },
    h("div", { class: "row between" }, h("h2", null, "Camera check"), pill("Required", "warn")),
    h("div", { class: "camera-box" }, video, status),
    enable,
    agree.el,
    h("p", { class: "small muted" }, "Every assessment requires your webcam. We check that exactly one face is visible, and notice if you leave the test tab or fullscreen. No video is recorded or uploaded. Flags are reviewed by a person and never change your score automatically."),
    h("p", { class: "small muted" }, "No webcam, or the camera does not work? Contact your administrator before the window closes."));
  return { el, cameraOk: () => ok, consent: () => agree.input.checked };
}

/* ------------------------------------------------------------------ attempt */
async function runAttempt(attemptId, info, proctor) {
  const data = await get(`/api/portal/attempts/${attemptId}/questions`);
  const questions = data.questions;
  const answers = new Map(questions.map((q) => [q.id, q.saved_answer]));
  const saveState = new Map(); // id -> "saving" | "saved" | "error"
  let index = 0;
  let deadline = Date.now() + data.remaining_seconds * 1000;
  let finished = false;
  const warned = new Set();

  const root = bareLayout();
  const timer = h("span", { class: "timer", role: "timer", "aria-live": "off" }, "--:--");
  const progress = h("span", { class: "muted small" });
  const submitBtn = h("button", { class: "btn primary", type: "button" }, "Submit");
  const qHost = h("div");
  const navHost = h("div");
  const preview = proctor?.stream ? h("video", { autoplay: true, muted: true, playsinline: true }) : null;
  const camStatus = h("span", { class: "camera-status" }, "Camera on");
  if (proctor && preview) { proctor.attachPreview(preview); proctor.onStatus = (t) => { camStatus.textContent = t; }; }

  const isAnswered = (q) => {
    const a = answers.get(q.id);
    if (!a) return false;
    if ("text" in a) return Boolean(a.text && a.text.trim());
    return Array.isArray(a.selected) ? a.selected.length > 0 : Boolean(a.selected);
  };

  const pendingText = new Map();
  async function save(q, body) {
    saveState.set(q.id, "saving");
    renderSaveState();
    try {
      await put(`/api/portal/attempts/${attemptId}/answers/${q.id}`, body);
      saveState.set(q.id, "saved");
    } catch (err) {
      if (err.code === "attempt_closed") return finish(true, err.message);
      saveState.set(q.id, "error");
      toast(`Answer not saved: ${err.message}`, "bad");
    }
    renderSaveState();
    renderNav();
  }
  function saveText(q, text) {
    answers.set(q.id, { text });
    clearTimeout(pendingText.get(q.id));
    pendingText.set(q.id, setTimeout(() => { pendingText.delete(q.id); save(q, { text: text.trim() ? text : null }); }, 800));
    renderNav();
  }
  async function flushText() {
    for (const [id, t] of pendingText) {
      clearTimeout(t);
      pendingText.delete(id);
      const q = questions.find((x) => x.id === id);
      const a = answers.get(id);
      await save(q, { text: a?.text?.trim() ? a.text : null });
    }
  }

  const saveLine = h("span", { class: "save-state" });
  function renderSaveState() {
    const s = saveState.get(questions[index].id);
    saveLine.textContent = s === "saving" ? "Saving…" : s === "saved" ? "Saved" : s === "error" ? "Not saved, change your answer to retry" : isAnswered(questions[index]) ? "Saved" : "";
  }

  function renderQuestion() {
    const q = questions[index];
    const a = answers.get(q.id) || {};
    let control;
    if (q.question_type === "mcq_single" || q.question_type === "mcq_multi") {
      const multi = q.question_type === "mcq_multi";
      const selected = new Set(multi ? a.selected || [] : a.selected ? [a.selected] : []);
      control = h("div", { class: "choices", role: multi ? "group" : "radiogroup", "aria-label": "Answer options" },
        q.options.map((o) => {
          const inputEl = h("input", { type: multi ? "checkbox" : "radio", name: `q${q.id}`, value: o.id, checked: selected.has(o.id) });
          const label = h("label", { class: `choice ${selected.has(o.id) ? "selected" : ""}` }, inputEl, h("span", { class: "key" }, o.id), h("span", null, o.text));
          inputEl.addEventListener("change", () => {
            if (multi) { inputEl.checked ? selected.add(o.id) : selected.delete(o.id); }
            else { selected.clear(); selected.add(o.id); }
            const value = multi ? [...selected].sort() : o.id;
            answers.set(q.id, { selected: multi && !selected.size ? null : value });
            save(q, { selected: multi ? (selected.size ? value : null) : value });
            renderQuestion();
          });
          return label;
        }));
    } else {
      const ta = h("textarea", { class: `textarea answer-text ${q.question_type === "coding" ? "code" : ""}`, placeholder: q.question_type === "coding" ? "Write your code or pseudo-code here…" : "Write your answer here…", spellcheck: q.question_type !== "coding", "aria-label": "Your answer" });
      ta.value = a.text || "";
      ta.addEventListener("input", () => { saveText(q, ta.value); renderSaveState(); });
      ta.addEventListener("blur", () => flushText());
      control = ta;
    }
    mount(qHost, h("article", { class: "question-card" },
      h("div", { class: "qhead" }, h("span", null, `Question ${index + 1} of ${questions.length}`), h("span", null, `${q.points} ${q.points === 1 ? "point" : "points"} · ${TYPE_LABELS[q.question_type]}${q.question_type === "mcq_multi" ? " (select all that apply)" : ""}`)),
      h("div", { class: "qtext" }, q.question_text),
      control,
      h("div", { class: "row between", style: { marginTop: "18px" } },
        h("button", { class: "btn", type: "button", disabled: index === 0, onclick: async () => { await flushText(); go(index - 1); } }, "← Previous"),
        saveLine,
        index < questions.length - 1
          ? h("button", { class: "btn primary", type: "button", onclick: async () => { await flushText(); go(index + 1); } }, "Next →")
          : h("button", { class: "btn primary", type: "button", onclick: () => confirmSubmit() }, "Review and submit"))));
    renderSaveState();
  }

  function renderNav() {
    const answered = questions.filter(isAnswered).length;
    progress.textContent = `${answered} of ${questions.length} answered`;
    mount(navHost, h("div", { class: "navigator" },
      h("h3", null, "Questions"),
      h("div", { class: "nav-grid" }, questions.map((q, i) => h("button", { type: "button", class: `${isAnswered(q) ? "answered" : ""} ${i === index ? "current" : ""}`, "aria-label": `Question ${i + 1}${isAnswered(q) ? ", answered" : ""}`, onclick: async () => { await flushText(); go(i); } }, i + 1))),
      h("div", { class: "legend" }, h("span", null, h("i", { class: "answered" }), "Answered"), h("span", null, h("i"), "Not answered")),
      preview ? h("div", { style: { marginTop: "14px" } }, h("div", { class: "camera-box proctor-mini" }, preview, camStatus)) : null));
  }

  function go(i) {
    index = Math.max(0, Math.min(questions.length - 1, i));
    renderQuestion();
    renderNav();
    window.scrollTo({ top: 0 });
  }

  function tick() {
    if (finished) return;
    const left = Math.max(0, Math.round((deadline - Date.now()) / 1000));
    timer.textContent = fmtDuration(left);
    timer.className = `timer ${left <= 60 ? "bad" : left <= 300 ? "warn" : ""}`;
    for (const mark of [300, 60]) {
      if (left <= mark && left > 0 && !warned.has(mark)) { warned.add(mark); toast(mark === 300 ? "5 minutes left." : "1 minute left. Your saved answers will be submitted automatically.", "bad"); }
    }
    if (left === 0) finish(true);
  }
  async function resync() {
    if (finished) return;
    try {
      const s = await get(`/api/portal/attempts/${attemptId}/status`);
      if (s.status !== "in_progress") return finish(true);
      deadline = Date.now() + s.remaining_seconds * 1000;
    } catch { /* keep local estimate */ }
  }

  async function confirmSubmit() {
    await flushText();
    const unanswered = questions.length - questions.filter(isAnswered).length;
    const ok = await confirmDialog({
      title: "Submit your answers?",
      message: unanswered ? `You have ${plural(unanswered, "unanswered question")}. Unanswered questions score zero. You cannot change answers after submitting.` : "You have answered every question. You cannot change answers after submitting.",
      confirmLabel: "Submit now",
    });
    if (ok) finish(false);
  }

  async function finish(timeUp, note) {
    if (finished) return;
    finished = true;
    clearInterval(tickTimer);
    clearInterval(syncTimer);
    window.removeEventListener("beforeunload", beforeUnload);
    let message = note;
    try {
      if (!timeUp) await flushText();
      const res = await post(`/api/portal/attempts/${attemptId}/submit`);
      message = res.message;
    } catch (err) { message = message || err.message; }
    proctor?.stop();
    if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
    mount(bareLayout(), h("div", { class: "auth-main", style: { minHeight: "100%" } }, h("div", { class: "auth-card stack", style: { width: "min(520px, 100%)", textAlign: "center" } },
      h("div", { class: "score-ring", style: { margin: "0 auto", border: "8px solid var(--accent)", fontSize: "34px" } }, "✓"),
      h("h1", null, timeUp ? "Time is up" : "Submitted"),
      h("p", { class: "muted" }, message || "Your answers have been submitted."),
      h("p", { class: "muted small" }, `Submitted ${fmtDateTime(new Date().toISOString())}. Your result will appear in your history and be emailed to you once it is ready.`),
      h("div", { class: "row", style: { justifyContent: "center" } }, h("a", { class: "btn primary", href: "#/student/history" }, "View history"), h("a", { class: "btn", href: "#/student" }, "My assessments")))));
  }
  const beforeUnload = (e) => { e.preventDefault(); e.returnValue = ""; };
  window.addEventListener("beforeunload", beforeUnload);
  window.addEventListener("hashchange", function leave() { window.removeEventListener("hashchange", leave); if (!finished) { finished = true; clearInterval(tickTimer); clearInterval(syncTimer); window.removeEventListener("beforeunload", beforeUnload); proctor?.stop(); } }, { once: true });

  mount(root,
    h("header", { class: "test-head" }, h("div", { class: "test-head-inner" },
      h("b", null, info.title), progress, h("span", { class: "spacer" }), timer, submitBtn)),
    h("div", { class: "test-body" }, h("div", { class: "test-shell" }, qHost, navHost)));
  submitBtn.addEventListener("click", confirmSubmit);
  const tickTimer = setInterval(tick, 1000);
  const syncTimer = setInterval(resync, 30000);
  go(0);
  tick();
  void navigate;
}
