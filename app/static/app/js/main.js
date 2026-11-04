// AssessFlow AI web app entry point: one portal for admins and students.
import { get } from "./core/api.js";
import { config } from "./core/format.js";
import { fallback, navigate, route, startRouter } from "./core/router.js";
import { session, loadAdmin, loadStudent } from "./core/session.js";
import { toast } from "./core/ui.js";
import { invite, invitePage, loginPage, requireAdmin, requireStudent } from "./pages/auth.js";

// Invitation links look like /start#token=...; take the token out of the address bar
// immediately (it stays only in memory) and show the verification screen.
const tokenMatch = /token=([A-Za-z0-9_\-]+)/.exec(location.hash);
if (tokenMatch) {
  invite.token = tokenMatch[1];
  history.replaceState(null, "", location.pathname + "#/invite");
} else if (!location.hash || location.hash === "#" || location.hash === "#/") {
  const p = location.pathname;
  const start = p.startsWith("/admin") ? "/admin" : p.startsWith("/portal") || p.startsWith("/assessment") ? "/student" : p.startsWith("/start") ? "/invite" : "/";
  history.replaceState(null, "", p + "#" + start);
}

const lazy = (loader, name) => async (params, query) => (await loader())[name](params, query);
const admin = (loader, name) => async (params, query) => {
  if (!(await requireAdmin(location.hash.slice(1)))) return;
  return lazy(loader, name)(params, query);
};
const student = (loader, name) => async (params, query) => {
  if (!(await requireStudent(location.hash.slice(1)))) return;
  return lazy(loader, name)(params, query);
};

const A = {
  overview: () => import("./pages/admin/overview.js"),
  assessments: () => import("./pages/admin/assessments.js"),
  editor: () => import("./pages/admin/assessment_editor.js"),
  bank: () => import("./pages/admin/bank.js"),
  people: () => import("./pages/admin/people.js"),
  results: () => import("./pages/admin/results.js"),
  ops: () => import("./pages/admin/operations.js"),
  assistant: () => import("./pages/admin/assistant.js"),
};
const S = {
  home: () => import("./pages/student/home.js"),
  test: () => import("./pages/student/test.js"),
};

route("/", async () => {
  if (await loadAdmin()) return navigate("/admin", { replace: true });
  if (await loadStudent()) return navigate("/student", { replace: true });
  navigate("/login", { replace: true });
});
route("/login", loginPage);
route("/invite", invitePage);

route("/admin", admin(A.overview, "overviewPage"));
route("/admin/assessments", admin(A.assessments, "assessmentsPage"));
route("/admin/assessments/new", admin(A.assessments, "newAssessmentPage"));
route("/admin/assessments/:id", admin(A.editor, "assessmentPage"));
route("/admin/bank", admin(A.bank, "bankPage"));
route("/admin/candidates", admin(A.people, "candidatesPage"));
route("/admin/batches", admin(A.people, "batchesPage"));
route("/admin/batches/:id", admin(A.people, "batchPage"));
route("/admin/results", admin(A.results, "resultsPage"));
route("/admin/results/:id", admin(A.results, "resultPage"));
route("/admin/emails", admin(A.ops, "emailsPage"));
route("/admin/audit", admin(A.ops, "auditPage"));
route("/admin/assistant", admin(A.assistant, "assistantPage"));

route("/student", student(S.home, "homePage"));
route("/student/history", student(S.home, "historyPage"));
route("/student/results/:id", student(S.home, "resultPage"));
route("/student/test/:id", student(S.test, "testPage"));

fallback(() => navigate("/", { replace: true }));

// A session that expires mid-use sends the person back to the right sign-in screen.
window.addEventListener("auth:expired", (e) => {
  const isStudent = e.detail?.student;
  if (isStudent) session.student = null; else session.admin = null;
  toast("Your session has ended. Please sign in again.", "bad");
  navigate(`/login?role=${isStudent ? "student" : "admin"}`);
});

(async () => {
  try {
    const health = await get("/api/health");
    if (health?.timezone) config.timezone = health.timezone;
  } catch { /* defaults are fine */ }
  await startRouter();
})();
