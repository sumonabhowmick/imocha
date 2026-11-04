// Sign-in page (Student / Admin) and invitation-link verification.
import { h, mount } from "../core/dom.js";
import { post } from "../core/api.js";
import { bareLayout } from "../core/layout.js";
import { navigate } from "../core/router.js";
import { session, loadAdmin, loadStudent } from "../core/session.js";
import { busy, field, input, toast } from "../core/ui.js";

export const invite = { token: null }; // invitation token, kept in memory only

function sidePanel() {
  return h("section", { class: "auth-side" },
    h("div", { class: "brand", style: { color: "#fff" } }, h("span", { class: "brand-logo" }, "A"), h("span", { class: "brand-name" }, "AssessFlow AI")),
    h("div", null,
      h("h1", null, "Assessments that grade themselves and coach every learner."),
      h("p", { class: "lead" }, "Create quizzes, invite batches of students, proctor attempts in the browser and get AI feedback grounded in verified scores.")),
    h("ul", { class: "auth-points" },
      h("li", null, h("span", null, "✓"), h("span", null, h("b", null, "Server-timed tests. "), "Deadlines are enforced by the server, not the browser.")),
      h("li", null, h("span", null, "✓"), h("span", null, h("b", null, "Private by design. "), "One-time codes are stored only as hashes; no video leaves the device.")),
      h("li", null, h("span", null, "✓"), h("span", null, h("b", null, "AI that explains, Python that decides. "), "Scores and categories are computed by code."))));
}

function errorLine() {
  return h("div", { class: "alert bad", hidden: true, role: "alert" });
}
function showError(box, message) {
  box.textContent = message;
  box.hidden = false;
}

function adminForm(next) {
  const email = input({ type: "email", autocomplete: "username", required: true, placeholder: "admin@college.edu" });
  const password = input({ type: "password", autocomplete: "current-password", required: true });
  const err = errorLine();
  const submit = h("button", { class: "btn primary", type: "submit", style: { width: "100%" } }, "Sign in as admin");
  const form = h("form", { class: "form", novalidate: true },
    field("Email", email), field("Password", password), err, submit);
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    err.hidden = true;
    busy(submit, async () => {
      try {
        const res = await post("/api/auth/session", { email: email.value.trim(), password: password.value });
        session.admin = res.admin;
        session.adminChecked = true;
        navigate(next && next.startsWith("/admin") ? next : "/admin");
      } catch (ex) {
        password.value = "";
        showError(err, ex.message);
      }
    });
  });
  return form;
}

function studentForm(next) {
  const email = input({ type: "email", autocomplete: "email", required: true, placeholder: "you@example.com" });
  const code = input({ inputmode: "numeric", autocomplete: "one-time-code", maxlength: 10, class: "input code-input", placeholder: "••••••" });
  const err = errorLine();
  const info = h("div", { class: "alert info", hidden: true });
  const codeStep = h("div", { class: "form", hidden: true },
    field("Sign-in code", code, "We emailed you a 6-digit code. It expires in 10 minutes."));
  const submit = h("button", { class: "btn primary", type: "submit", style: { width: "100%" } }, "Email me a sign-in code");
  const resend = h("button", { class: "btn ghost sm", type: "button", hidden: true }, "Send a new code");
  let stage = "email";

  async function requestCode() {
    const res = await post("/api/portal/login/request-code", { email: email.value.trim() });
    info.textContent = res.message;
    info.hidden = false;
    stage = "code";
    codeStep.hidden = false;
    resend.hidden = false;
    email.readOnly = true;
    submit.textContent = "Sign in";
    code.focus();
  }

  const form = h("form", { class: "form", novalidate: true }, field("Email", email), codeStep, info, err, submit, resend);
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    err.hidden = true;
    busy(submit, async () => {
      try {
        if (stage === "email") return await requestCode();
        await post("/api/portal/login/verify", { email: email.value.trim(), code: code.value.trim() });
        await loadStudent(true);
        navigate(next && next.startsWith("/student") ? next : "/student");
      } catch (ex) {
        showError(err, ex.message);
      }
    });
  });
  resend.addEventListener("click", () => busy(resend, async () => {
    try { await requestCode(); toast("If the email is registered, a new code is on its way."); } catch (ex) { showError(err, ex.message); }
  }));
  return form;
}

export async function loginPage(_params, query) {
  const root = bareLayout();
  let role = query.role === "admin" ? "admin" : "student";
  const formHost = h("div");
  const switcher = h("div", { class: "role-switch", role: "group", "aria-label": "Sign in as" });
  function render() {
    mount(switcher,
      h("button", { type: "button", "aria-pressed": String(role === "student"), onclick: () => { role = "student"; render(); } }, "Student"),
      h("button", { type: "button", "aria-pressed": String(role === "admin"), onclick: () => { role = "admin"; render(); } }, "Admin"));
    mount(formHost, role === "admin" ? adminForm(query.next) : studentForm(query.next));
    formHost.querySelector("input")?.focus();
  }
  mount(root, h("div", { class: "auth-wrap" }, sidePanel(),
    h("section", { class: "auth-main" },
      h("div", { class: "auth-card" },
        h("h2", { style: { fontSize: "22px" } }, "Sign in"),
        h("p", { class: "muted", style: { marginTop: "4px" } }, "Students sign in with a one-time code sent by email. Admins use their password."),
        switcher, formHost))));
  render();
}

/** Opened from the invitation email ("Start Assessment"): verify the one-time code. */
export async function invitePage() {
  const root = bareLayout();
  if (!invite.token) {
    const already = await loadStudent();
    if (already) return navigate("/student", { replace: true });
    mount(root, h("div", { class: "auth-main", style: { minHeight: "100%" } }, h("div", { class: "auth-card stack" },
      h("h2", null, "Invitation link incomplete"),
      h("p", { class: "muted" }, "Open the Start Assessment button from your invitation email again, or sign in with your email address."),
      h("a", { class: "btn primary", href: "#/login?role=student" }, "Sign in with email"))));
    return;
  }
  const code = input({ inputmode: "numeric", autocomplete: "one-time-code", maxlength: 10, class: "input code-input", placeholder: "••••••" });
  const err = errorLine();
  const info = h("div", { class: "alert info", hidden: true });
  const submit = h("button", { class: "btn primary", type: "submit", style: { width: "100%" } }, "Verify and continue");
  const resend = h("button", { class: "btn ghost sm", type: "button" }, "Send me a new code");
  const form = h("form", { class: "form", novalidate: true },
    field("One-time code from your invitation email", code, "The code expires 10 minutes after it was sent and works once."), err, info, submit, resend);
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    err.hidden = true;
    busy(submit, async () => {
      try {
        const res = await post("/api/invitations/verify", { token: invite.token, passcode: code.value.trim() });
        invite.token = null;
        await loadStudent(true);
        navigate(res.assignment_id ? `/student/test/${res.assignment_id}` : "/student", { replace: true });
      } catch (ex) { showError(err, ex.message); }
    });
  });
  resend.addEventListener("click", () => busy(resend, async () => {
    try {
      const res = await post("/api/invitations/resend-code", { token: invite.token });
      info.textContent = res.message;
      info.hidden = false;
    } catch (ex) { showError(err, ex.message); }
  }));
  mount(root, h("div", { class: "auth-wrap" }, sidePanel(),
    h("section", { class: "auth-main" }, h("div", { class: "auth-card" },
      h("h2", { style: { fontSize: "22px" } }, "Verify your invitation"),
      h("p", { class: "muted", style: { margin: "4px 0 18px" } }, "Enter the code from the same email as your Start Assessment link."),
      form))));
  code.focus();
}

export async function requireAdmin(path) {
  const admin = await loadAdmin();
  if (!admin) { navigate(`/login?role=admin&next=${encodeURIComponent(path)}`, { replace: true }); return null; }
  return admin;
}

export async function requireStudent(path) {
  const student = await loadStudent();
  if (!student) { navigate(`/login?role=student&next=${encodeURIComponent(path)}`, { replace: true }); return null; }
  return student;
}
