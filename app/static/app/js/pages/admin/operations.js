// Email log and audit log.
import { get, post } from "../../core/api.js";
import { adminLayout } from "../../core/layout.js";
import { fmtDateTime } from "../../core/format.js";
import { busy, emptyState, errorBox, h, input, loading, mount, pageHead, pill, select, statusPill, table, toast, toastError } from "../../core/ui.js";

const TYPE_LABELS = {
  invitation: "Invitation", passcode: "New code", login_code: "Sign-in code", result: "Result", reminder: "Reminder",
  admin_result_ready: "Admin: result ready", admin_feedback_failed: "Admin: feedback failed",
};

export async function emailsPage(_p, query) {
  const main = adminLayout("emails");
  const host = h("div");
  const status = select([{ value: "", label: "All statuses" }, "sent", "failed", "queued", "outbox"], query.status || "");
  async function load() {
    mount(host, loading());
    try {
      const logs = await get("/api/admin/email-logs", { status: status.value || null, limit: 300 });
      mount(host, table({
        rows: logs,
        empty: emptyState("No emails yet"),
        columns: [
          { label: "When", render: (l) => h("span", { class: "nowrap small" }, fmtDateTime(l.created_at)) },
          { label: "Type", render: (l) => pill(TYPE_LABELS[l.notification_type] || l.notification_type, "accent") },
          { label: "Recipient", render: (l) => [h("div", null, l.recipient_email), h("div", { class: "sub" }, l.subject)] },
          { label: "Status", render: (l) => statusPill(l.status) },
          { label: "Attempts", class: "num", render: (l) => l.attempts },
          { label: "Error", render: (l) => (l.last_error ? h("div", { class: "small", style: { color: "var(--bad)", maxWidth: "36ch", whiteSpace: "normal" } }, l.last_error) : "—") },
          { label: "", class: "actions", render: (l) => (l.status === "failed" && l.notification_type !== "login_code"
              ? h("button", { class: "btn sm", type: "button", onclick: (e) => busy(e.currentTarget, async () => {
                  try { const res = await post(`/api/admin/email-logs/${l.id}/retry`); toast(res.status === "sent" ? "Sent." : `Still failing: ${res.error || res.status}`, res.status === "sent" ? "good" : "bad"); load(); } catch (err) { toastError(err); }
                }) }, "Retry") : null) },
        ],
      }));
    } catch (err) { mount(host, errorBox(err, load)); }
  }
  status.addEventListener("change", load);
  mount(main, pageHead({ title: "Email log", sub: "Every email the platform sends. Codes, links and passwords are never stored here. Retrying an invitation issues a fresh link and code." }),
    h("div", { class: "toolbar" }, status), host);
  load();
}

export async function auditPage() {
  const main = adminLayout("audit");
  const host = h("div");
  const search = input({ type: "search", class: "input grow", placeholder: "Search by person or action" });
  const actor = select([{ value: "", label: "Everyone" }, { value: "admin", label: "Admins" }, { value: "candidate", label: "Students" }, { value: "agent", label: "AI assistant" }, { value: "anonymous", label: "Not signed in" }], "");
  let offset = 0;
  const pageSize = 50;
  async function load() {
    mount(host, loading());
    try {
      const data = await get("/api/admin/audit", { search: search.value || null, actor_type: actor.value || null, limit: pageSize, offset });
      mount(host,
        table({ rows: data.items, empty: emptyState("No matching events"), columns: [
          { label: "When", render: (e) => h("span", { class: "nowrap small" }, fmtDateTime(e.occurred_at)) },
          { label: "Who", render: (e) => [h("div", null, e.actor_label || "—"), h("div", { class: "sub" }, e.actor_type)] },
          { label: "Action", render: (e) => h("code", { class: "mono" }, e.action) },
          { label: "Target", render: (e) => (e.target_type ? `${e.target_type} #${e.target_id}` : "—") },
          { label: "Details", render: (e) => (e.details ? h("code", { class: "mono small", style: { whiteSpace: "normal", wordBreak: "break-word" } }, JSON.stringify(e.details)) : "—") },
          { label: "IP", render: (e) => h("span", { class: "small muted" }, e.ip_address || "—") },
        ] }),
        h("div", { class: "row between", style: { marginTop: "12px" } },
          h("span", { class: "muted small" }, `${offset + 1}–${Math.min(offset + pageSize, data.total)} of ${data.total}`),
          h("div", { class: "row" },
            h("button", { class: "btn sm", type: "button", disabled: offset === 0, onclick: () => { offset = Math.max(0, offset - pageSize); load(); } }, "← Newer"),
            h("button", { class: "btn sm", type: "button", disabled: offset + pageSize >= data.total, onclick: () => { offset += pageSize; load(); } }, "Older →"))));
    } catch (err) { mount(host, errorBox(err, load)); }
  }
  let t;
  search.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { offset = 0; load(); }, 300); });
  actor.addEventListener("change", () => { offset = 0; load(); });
  mount(main, pageHead({ title: "Audit log", sub: "Security-relevant actions: sign-ins, publishing, deletions, exports, snapshot views and AI assistant actions." }),
    h("div", { class: "toolbar" }, search, actor), host);
  load();
}
