// Page chrome: admin rail layout and student top bar.
import { h, mount } from "./dom.js";
import { navigate } from "./router.js";
import { session, logoutAdmin, logoutStudent } from "./session.js";

const ADMIN_NAV = [
  { group: "Monitor" },
  { key: "overview", href: "#/admin", label: "Overview", ico: "◧" },
  { key: "results", href: "#/admin/results", label: "Results", ico: "✓" },
  { group: "Build" },
  { key: "assessments", href: "#/admin/assessments", label: "Assessments", ico: "▤" },
  { key: "bank", href: "#/admin/bank", label: "Question bank", ico: "❏" },
  { group: "People" },
  { key: "candidates", href: "#/admin/candidates", label: "Candidates", ico: "☺" },
  { key: "batches", href: "#/admin/batches", label: "Batches", ico: "▦" },
  { group: "Operations" },
  { key: "emails", href: "#/admin/emails", label: "Email log", ico: "✉" },
  { key: "audit", href: "#/admin/audit", label: "Audit log", ico: "⌘" },
  { group: "AI" },
  { key: "assistant", href: "#/admin/assistant", label: "AI Assistant", ico: "✦" },
];

function brand(sub) {
  return h("a", { class: "brand", href: "#/" },
    h("span", { class: "brand-logo", "aria-hidden": "true" }, "A"),
    h("span", null, h("span", { class: "brand-name" }, "AssessFlow AI"), sub ? h("span", { class: "brand-sub", style: { display: "block" } }, sub) : null));
}

/** Render the admin layout; returns the content container. */
export function adminLayout(active) {
  const content = h("div", { id: "main" });
  const rail = h("aside", { class: "rail", "aria-label": "Admin navigation" },
    brand("Admin console"),
    h("nav", { class: "nav" }, ADMIN_NAV.map((item) => item.group
      ? h("div", { class: "nav-group" }, item.group)
      : h("a", { href: item.href, "aria-current": item.key === active ? "page" : null, onclick: () => rail.classList.remove("open") },
          h("span", { class: "ico", "aria-hidden": "true" }, item.ico), item.label))),
    h("div", { class: "rail-foot" },
      h("div", null, h("div", { class: "who" }, session.admin?.name || "Admin"), h("div", null, session.admin?.email || "")),
      h("div", null, "Times shown in IST"),
      h("button", { type: "button", onclick: async () => { await logoutAdmin(); navigate("/login?role=admin"); } }, "Sign out")));
  const toggle = h("button", { class: "btn sm menu-toggle", type: "button", onclick: () => rail.classList.toggle("open") }, "☰ Menu");
  mount(document.getElementById("app"), h("div", { class: "layout" }, rail, h("main", { class: "content" }, toggle, content)));
  document.getElementById("app").className = "";
  return content;
}

/** Render the student layout; returns the content container. */
export function studentLayout(active) {
  const content = h("main", { class: "page", id: "main" });
  const link = (key, href, label) => h("a", { href, "aria-current": key === active ? "page" : null }, label);
  mount(document.getElementById("app"),
    h("header", { class: "topbar" },
      h("div", { class: "topbar-inner" },
        brand(),
        h("nav", { "aria-label": "Student navigation" },
          link("home", "#/student", "My assessments"),
          link("history", "#/student/history", "History")),
        h("span", { class: "spacer" }),
        h("span", { class: "muted small" }, session.student?.name || ""),
        h("button", { class: "btn sm", type: "button", onclick: async () => { await logoutStudent(); navigate("/login?role=student"); } }, "Sign out"))),
    content);
  document.getElementById("app").className = "";
  return content;
}

/** Full-screen layout without chrome (login, test taking). */
export function bareLayout() {
  const root = document.getElementById("app");
  root.className = "";
  mount(root);
  return root;
}
