// Student quiz page. All rules (window, deadline, attempts) are enforced by the server;
// the countdown here is only a display aid. All text is inserted with textContent so
// question or answer content can never inject HTML or script.
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    attemptId: null,
    questions: [],
    index: 0,
    deadlineMs: null, // local clock time of the server deadline
    timerHandle: null,
    syncHandle: null,
    saveTimers: {},
    submitting: false,
    warned: { five: false, one: false },
  };

  // ---------------------------------------------------------------- helpers
  function getCookie(name) {
    const match = document.cookie.split("; ").find((c) => c.startsWith(name + "="));
    return match ? decodeURIComponent(match.split("=")[1]) : "";
  }

  async function api(method, url, body) {
    const headers = { "X-CSRF-Token": getCookie("af_csrf") };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(url, {
      method: method,
      credentials: "same-origin",
      headers: headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = {};
    try { data = await response.json(); } catch (e) { /* no body */ }
    return { ok: response.ok, status: response.status, data: data };
  }

  function message(text, kind) {
    const el = $("global-message");
    if (!text) { el.className = "alert hidden"; return; }
    el.textContent = text;
    el.className = "alert " + (kind || "error");
  }

  function showSection(id) {
    ["overview", "quiz", "done"].forEach((s) => $(s).classList.toggle("hidden", s !== id));
  }

  function formatSeconds(total) {
    total = Math.max(0, Math.floor(total));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    const mm = String(m).padStart(2, "0");
    const ss = String(s).padStart(2, "0");
    return h > 0 ? h + ":" + mm + ":" + ss : mm + ":" + ss;
  }

  function handleAuthError(res) {
    if (res.status === 401 || res.status === 403) {
      showSection(null);
      message(res.data.detail || "Your session has ended. Open the link in your invitation email again.");
      return true;
    }
    return false;
  }

  // ---------------------------------------------------------------- overview
  async function loadOverview() {
    const res = await api("GET", "/api/student/assessment");
    if (handleAuthError(res)) return;
    if (!res.ok) { message(res.data.detail || "Could not load the assessment."); return; }
    const d = res.data;
    $("ov-greeting").textContent = "Welcome, " + d.candidate_name;
    $("ov-title").textContent = d.title;
    $("ov-description").textContent = d.description || "";
    $("ov-questions").textContent = d.question_count + " (" + d.total_points + " points)";
    $("ov-duration").textContent = d.duration_minutes + " minutes";
    $("ov-window").textContent = (d.access_start_display || "-") + "  to  " + (d.access_end_display || "-");
    $("ov-timezone").textContent = d.timezone;
    $("ov-attempts").textContent = d.attempts_used + " of " + d.max_attempts + " used";
    $("ov-instructions").textContent = d.instructions || "Answer every question, then submit before the timer ends.";
    $("ov-policy").textContent =
      d.completion_policy === "hard_end_at_window"
        ? "Your attempt ends when the access window closes, even if your full duration has not passed."
        : "Once started, you get the full duration, even if the access window closes meanwhile.";
    const blocked = $("ov-blocked");
    blocked.classList.toggle("hidden", !d.start_blocked_reason);
    blocked.textContent = d.start_blocked_reason || "";
    $("start-btn").classList.toggle("hidden", !d.can_start);
    $("resume-btn").classList.toggle("hidden", !d.in_progress_attempt_id);
    state.attemptId = d.in_progress_attempt_id;
    showSection("overview");
  }

  async function startAttempt() {
    $("start-btn").disabled = true;
    const res = await api("POST", "/api/student/attempts");
    $("start-btn").disabled = false;
    if (handleAuthError(res)) return;
    if (!res.ok) { message(res.data.detail || "Could not start the assessment."); await loadOverview(); return; }
    state.attemptId = res.data.attempt_id;
    await loadQuestions();
  }

  // ---------------------------------------------------------------- quiz
  async function loadQuestions() {
    const res = await api("GET", "/api/student/attempts/" + state.attemptId + "/questions");
    if (handleAuthError(res)) return;
    if (res.status === 409) { await finalizeSubmit(); return; }
    if (!res.ok) { message(res.data.detail || "Could not load questions."); return; }
    message("");
    state.questions = res.data.questions.map((q) => Object.assign({}, q, { answer: q.saved_answer }));
    $("quiz-title").textContent = res.data.title;
    setDeadline(res.data.remaining_seconds);
    buildNav();
    renderQuestion(0);
    showSection("quiz");
    startTimer();
  }

  function setDeadline(remainingSeconds) {
    state.deadlineMs = Date.now() + remainingSeconds * 1000;
  }

  function isAnswered(q) {
    const a = q.answer;
    if (!a) return false;
    if (typeof a.text === "string") return a.text.trim().length > 0;
    if (Array.isArray(a.selected)) return a.selected.length > 0;
    return !!a.selected;
  }

  function buildNav() {
    const nav = $("qnav");
    nav.textContent = "";
    state.questions.forEach((q, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = String(q.number);
      b.setAttribute("aria-label", "Question " + q.number);
      b.addEventListener("click", () => renderQuestion(i));
      nav.appendChild(b);
    });
    refreshNav();
  }

  function refreshNav() {
    const buttons = $("qnav").children;
    state.questions.forEach((q, i) => {
      buttons[i].classList.toggle("answered", isAnswered(q));
      buttons[i].classList.toggle("current", i === state.index);
    });
    const answered = state.questions.filter(isAnswered).length;
    $("quiz-progress").textContent = answered + " of " + state.questions.length + " answered";
  }

  function renderQuestion(i) {
    state.index = i;
    const q = state.questions[i];
    $("q-meta").textContent =
      "Question " + q.number + " of " + state.questions.length + "  -  " + q.points + " point" + (q.points === 1 ? "" : "s") +
      (q.question_type === "mcq_multi" ? "  -  select all that apply" : "");
    $("q-text").textContent = q.question_text;
    const container = $("q-answer");
    container.textContent = "";
    $("save-status").textContent = "";

    if (q.question_type === "mcq_single" || q.question_type === "mcq_multi") {
      const multi = q.question_type === "mcq_multi";
      const current = q.answer ? q.answer.selected : null;
      q.options.forEach((opt) => {
        const label = document.createElement("label");
        label.className = "option";
        const input = document.createElement("input");
        input.type = multi ? "checkbox" : "radio";
        input.name = "q" + q.id;
        input.value = opt.id;
        input.checked = multi ? Array.isArray(current) && current.includes(opt.id) : current === opt.id;
        input.addEventListener("change", () => {
          if (multi) {
            const selected = Array.from(container.querySelectorAll("input:checked")).map((x) => x.value);
            q.answer = selected.length ? { selected: selected } : null;
            saveAnswer(q, { selected: selected.length ? selected : null });
          } else {
            q.answer = { selected: opt.id };
            saveAnswer(q, { selected: opt.id });
          }
        });
        const span = document.createElement("span");
        span.textContent = opt.id + ". " + opt.text;
        label.appendChild(input);
        label.appendChild(span);
        container.appendChild(label);
      });
    } else {
      const area = document.createElement("textarea");
      area.className = q.question_type === "coding" ? "code" : "";
      area.maxLength = 20000;
      area.value = q.answer && q.answer.text ? q.answer.text : "";
      area.setAttribute("aria-label", "Your answer");
      area.addEventListener("input", () => {
        q.answer = area.value.trim() ? { text: area.value } : null;
        $("save-status").textContent = "Unsaved changes...";
        clearTimeout(state.saveTimers[q.id]);
        state.saveTimers[q.id] = setTimeout(() => saveAnswer(q, { text: area.value }), 1200);
      });
      area.addEventListener("blur", () => {
        clearTimeout(state.saveTimers[q.id]);
        saveAnswer(q, { text: area.value });
      });
      container.appendChild(area);
    }
    $("prev-btn").disabled = i === 0;
    $("next-btn").disabled = i === state.questions.length - 1;
    refreshNav();
  }

  async function saveAnswer(q, body) {
    if (state.submitting) return;
    $("save-status").textContent = "Saving...";
    try {
      const res = await api("PUT", "/api/student/attempts/" + state.attemptId + "/answers/" + q.id, body);
      if (handleAuthError(res)) return;
      if (res.status === 409) { await finalizeSubmit(); return; }
      if (!res.ok) { $("save-status").textContent = res.data.detail || "Could not save this answer."; return; }
      setDeadline(res.data.remaining_seconds);
      if (state.questions[state.index] === q) $("save-status").textContent = "Saved";
      refreshNav();
    } catch (e) {
      $("save-status").textContent = "Network problem - your answer was not saved. It will retry when you change it.";
    }
  }

  async function flushPendingSaves() {
    const pending = Object.keys(state.saveTimers);
    for (const id of pending) {
      clearTimeout(state.saveTimers[id]);
      const q = state.questions.find((x) => String(x.id) === id);
      if (q && q.answer && typeof q.answer.text === "string") await saveAnswer(q, { text: q.answer.text });
    }
    state.saveTimers = {};
  }

  // ---------------------------------------------------------------- timer
  function startTimer() {
    const timer = $("timer");
    timer.classList.remove("hidden");
    clearInterval(state.timerHandle);
    clearInterval(state.syncHandle);
    const tick = () => {
      const remaining = (state.deadlineMs - Date.now()) / 1000;
      timer.textContent = "Time left " + formatSeconds(remaining);
      timer.classList.toggle("warning", remaining <= 300 && remaining > 60);
      timer.classList.toggle("critical", remaining <= 60);
      const warning = $("time-warning");
      if (remaining <= 60 && !state.warned.one) {
        state.warned.one = true;
        warning.textContent = "Less than one minute left. Your saved answers will be submitted automatically.";
        warning.classList.remove("hidden");
      } else if (remaining <= 300 && !state.warned.five) {
        state.warned.five = true;
        warning.textContent = "Five minutes left. Make sure your answers are saved.";
        warning.classList.remove("hidden");
      }
      if (remaining <= 0) {
        clearInterval(state.timerHandle);
        finalizeSubmit();
      }
    };
    tick();
    state.timerHandle = setInterval(tick, 1000);
    // Re-sync with the server clock every 30 seconds.
    state.syncHandle = setInterval(async () => {
      const res = await api("GET", "/api/student/attempts/" + state.attemptId + "/status");
      if (res.ok) {
        if (res.data.status !== "in_progress") { finalizeSubmit(); return; }
        setDeadline(res.data.remaining_seconds);
      }
    }, 30000);
  }

  // ---------------------------------------------------------------- submit
  function openConfirm() {
    const unanswered = state.questions.filter((q) => !isAnswered(q)).length;
    $("confirm-text").textContent =
      unanswered > 0
        ? "You have " + unanswered + " unanswered question" + (unanswered === 1 ? "" : "s") + ". Unanswered questions score zero."
        : "You have answered every question.";
    $("confirm-panel").classList.remove("hidden");
    $("confirm-panel").scrollIntoView({ behavior: "smooth" });
  }

  async function finalizeSubmit() {
    if (state.submitting) return;
    state.submitting = true;
    clearInterval(state.timerHandle);
    clearInterval(state.syncHandle);
    $("confirm-submit").disabled = true;
    const res = await api("POST", "/api/student/attempts/" + state.attemptId + "/submit");
    if (handleAuthError(res)) return;
    if (!res.ok) {
      state.submitting = false;
      $("confirm-submit").disabled = false;
      message(res.data.detail || "Submission failed. Please try again.");
      return;
    }
    $("timer").classList.add("hidden");
    $("done-message").textContent = res.data.message;
    $("done-time").textContent = "Submitted at " + new Date(res.data.submitted_at).toLocaleString();
    message("");
    showSection("done");
  }

  // ---------------------------------------------------------------- wiring
  $("start-btn").addEventListener("click", startAttempt);
  $("resume-btn").addEventListener("click", loadQuestions);
  $("prev-btn").addEventListener("click", () => renderQuestion(Math.max(0, state.index - 1)));
  $("next-btn").addEventListener("click", () => renderQuestion(Math.min(state.questions.length - 1, state.index + 1)));
  $("review-btn").addEventListener("click", openConfirm);
  $("cancel-submit").addEventListener("click", () => $("confirm-panel").classList.add("hidden"));
  $("confirm-submit").addEventListener("click", async () => { await flushPendingSaves(); finalizeSubmit(); });

  loadOverview().catch(() => message("Could not reach the server. Check your connection and reload."));
})();
