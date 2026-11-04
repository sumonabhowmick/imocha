// Shared UI building blocks.
import { h, mount, clear } from "./dom.js";

/* ---------- toasts ---------- */
export function toast(message, tone = "") {
  const root = document.getElementById("toasts");
  const el = h("div", { class: `toast ${tone}` }, message);
  root.appendChild(el);
  setTimeout(() => el.remove(), tone === "bad" ? 7000 : 3800);
}
export const toastError = (err) => toast(err?.message || "Something went wrong.", "bad");

/* ---------- busy buttons ---------- */
export async function busy(button, fn) {
  if (button) { button.classList.add("loading"); button.disabled = true; }
  try { return await fn(); }
  finally { if (button) { button.classList.remove("loading"); button.disabled = false; } }
}

/* ---------- modal ---------- */
/**
 * openModal({title, body, wide, actions: [{label, primary, danger, onClick: async (close) => ...}]})
 * Returns {close, body}. Escape and backdrop click close it.
 */
export function openModal({ title, body, wide = false, actions = [], onClose }) {
  const root = document.getElementById("modal-root");
  const previous = document.activeElement;
  const bodyEl = h("div", { class: "modal-body" }, body);
  const foot = h("div", { class: "modal-foot" });
  const dialog = h("div", { class: `modal ${wide ? "wide" : ""}`, role: "dialog", "aria-modal": "true", "aria-label": title },
    h("div", { class: "modal-head" }, h("h2", null, title), h("button", { class: "icon-btn", "aria-label": "Close", onclick: () => close() }, "✕")),
    bodyEl,
    actions.length ? foot : null);
  const backdrop = h("div", { class: "modal-backdrop", onmousedown: (e) => { if (e.target === backdrop) close(); } }, dialog);
  const onKey = (e) => { if (e.key === "Escape") close(); };
  function close() {
    document.removeEventListener("keydown", onKey);
    backdrop.remove();
    previous?.focus?.();
    onClose?.();
  }
  for (const a of actions) {
    const btn = h("button", { class: `btn ${a.primary ? "primary" : ""} ${a.danger ? "danger solid" : ""}`, type: "button" }, a.label);
    btn.addEventListener("click", async () => {
      if (!a.onClick) return close();
      try { await busy(btn, () => a.onClick(close, btn)); } catch (err) { toastError(err); }
    });
    foot.appendChild(btn);
  }
  document.addEventListener("keydown", onKey);
  root.appendChild(backdrop);
  setTimeout(() => (dialog.querySelector("input, select, textarea, button.primary") || dialog).focus?.(), 30);
  return { close, body: bodyEl, dialog };
}

/** Confirmation dialog built into the page (browsers' confirm() is not used). */
export function confirmDialog({ title, message, confirmLabel = "Confirm", danger = false }) {
  return new Promise((resolve) => {
    let decided = false;
    const m = openModal({
      title,
      body: h("p", null, message),
      onClose: () => { if (!decided) resolve(false); },
      actions: [
        { label: "Cancel", onClick: (close) => close() },
        { label: confirmLabel, primary: !danger, danger, onClick: (close) => { decided = true; resolve(true); close(); } },
      ],
    });
    return m;
  });
}

/* ---------- small components ---------- */
export const pill = (text, tone = "neutral") => h("span", { class: `pill ${tone}` }, text);

const STATUS_TONES = {
  draft: "neutral", scheduled: "info", active: "good", completed: "accent", archived: "neutral", published: "good",
  final: "good", provisional: "warn", sent: "good", outbox: "info", failed: "bad", queued: "neutral",
  completed_feedback: "good", pending: "neutral", assigned: "neutral", invited: "info", in_progress: "warn", revoked: "bad",
  verified: "good", expired: "warn", consumed: "neutral", locked: "bad", invalidated: "neutral",
  open: "good", upcoming: "info", missed: "bad", closed: "neutral",
};
export const statusPill = (status, label) =>
  status ? pill(label || String(status).replace(/_/g, " "), STATUS_TONES[status] || "neutral") : pill("—");

const CATEGORY_TONES = { Competitive: "good", Developer: "warn", Adaptive: "bad" };
export const categoryPill = (category) => (category ? pill(category, CATEGORY_TONES[category] || "accent") : pill("—"));

export function pageHead({ title, sub, crumbs, actions }) {
  return h("div", { class: "page-head" },
    h("div", null,
      crumbs ? h("div", { class: "crumbs" }, crumbs.map((c, i) => [i ? " / " : "", c.href ? h("a", { href: c.href }, c.label) : c.label])) : null,
      h("h1", null, title),
      sub ? h("p", { class: "sub" }, sub) : null),
    actions ? h("div", { class: "actions" }, actions) : null);
}

export function stat(label, value, hint) {
  return h("div", { class: "stat" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value), hint ? h("div", { class: "hint" }, hint) : null);
}

export function emptyState(title, text, action) {
  return h("div", { class: "empty" }, h("h3", null, title), text ? h("p", null, text) : null, action || null);
}

export const loading = (text = "Loading…") => h("div", { class: "empty" }, h("div", { class: "boot-mark", style: { margin: "0 auto 10px" } }), h("p", null, text));

export function errorBox(err, retry) {
  return h("div", { class: "alert bad" }, h("div", null, h("b", null, "Could not load this page. "), err?.message || "",
    retry ? [" ", h("button", { class: "btn sm", onclick: retry }, "Try again")] : null));
}

/**
 * table({columns: [{label, render(row), class, width}], rows, empty, onRowClick})
 */
export function table({ columns, rows, empty = "Nothing here yet.", onRowClick }) {
  if (!rows.length) return typeof empty === "string" ? emptyState(empty) : empty;
  return h("div", { class: "table-wrap" },
    h("table", null,
      h("thead", null, h("tr", null, columns.map((c) => h("th", { class: c.class || null, style: c.width ? { width: c.width } : null }, c.label)))),
      h("tbody", null, rows.map((row) => {
        const tr = h("tr", { class: onRowClick ? "clickable" : null }, columns.map((c) => h("td", { class: c.class || null }, c.render(row))));
        if (onRowClick) tr.addEventListener("click", (e) => { if (!e.target.closest("button, a, input, select, label")) onRowClick(row); });
        return tr;
      }))));
}

/* ---------- forms ---------- */
let fieldSeq = 0;
export function field(label, control, hint) {
  const id = control.id || `f${++fieldSeq}`;
  control.id = id;
  return h("div", { class: "field" }, h("label", { for: id }, label), control, hint ? h("div", { class: "hint" }, hint) : null);
}
export const input = (attrs = {}) => h("input", { class: "input", type: "text", ...attrs });
export const textarea = (attrs = {}) => h("textarea", { class: "textarea", ...attrs });
export function select(options, value, attrs = {}) {
  return h("select", { class: "select", ...attrs },
    options.map((o) => (typeof o === "string" ? { value: o, label: o } : o)).map((o) => h("option", { value: o.value, selected: String(o.value) === String(value) }, o.label)));
}
export function checkbox(label, checked, attrs = {}) {
  const box = h("input", { type: "checkbox", checked, ...attrs });
  return { el: h("label", { class: "check" }, box, h("span", null, label)), input: box };
}

/* ---------- tabs ---------- */
export function tabs(items, active, onSelect) {
  return h("div", { class: "tabs", role: "tablist" },
    items.map((t) => h("button", { role: "tab", type: "button", "aria-selected": String(t.id === active), onclick: () => onSelect(t.id) },
      t.label, t.count !== undefined ? h("span", { class: "count" }, t.count) : null)));
}

export { h, mount, clear };
