// Multi-agent AI assistant (Supervisor + specialists, with approvals for risky actions).
import { get, post } from "../../core/api.js";
import { adminLayout } from "../../core/layout.js";
import { busy, h, input, mount, pageHead, pill, toastError } from "../../core/ui.js";

const AGENTS = { supervisor: "Supervisor", analytics: "Analytics", quiz_builder: "Quiz Builder", operations: "Operations", coaching: "Coaching" };
const RISK = { read: ["read", "neutral"], write: ["safe write", "info"], approval: ["needs approval", "warn"] };
const state = { thread: null, messages: [], pending: null }; // kept while the page is open

function traceView(trace) {
  if (!trace?.length) return null;
  const calls = trace.filter((s) => s.type === "tool_call").length;
  const details = h("details", null, h("summary", { class: "small muted", style: { cursor: "pointer", marginTop: "8px" } }, `How the agents worked · ${calls} tool call${calls === 1 ? "" : "s"}`),
    h("div", { class: "trace" }, trace.map((s) => {
      if (s.type === "delegate") return h("div", null, "→ ", s.text);
      if (s.type === "tool_call") return h("div", null, h("b", null, AGENTS[s.agent] || s.agent), " called ", h("code", null, s.tool), " ", pill(RISK[s.risk]?.[0] || s.risk, RISK[s.risk]?.[1] || "neutral"));
      if (s.type === "tool_result") return h("div", { class: "faint" }, `result: ${s.text.slice(0, 220)}${s.text.length > 220 ? "…" : ""}`);
      return h("div", null, h("b", null, AGENTS[s.agent] || s.agent), ": ", s.text.slice(0, 300));
    })));
  return details;
}

export async function assistantPage() {
  const main = adminLayout("assistant");
  const thread = h("div", { class: "thread", "aria-live": "polite" });
  const box = input({ placeholder: "Ask about results, build a quiz, invite a batch…", maxlength: 4000 });
  const send = h("button", { class: "btn primary", type: "submit" }, "Send");
  const composer = h("form", { class: "composer" }, box, send);
  const agentsHost = h("div", { class: "stack", style: { gap: "10px" } });

  function apply(res) {
    state.thread = res.thread_id;
    state.pending = res.pending_approval;
    state.messages.push({ role: "ai", text: res.answer || (res.pending_approval ? "I need your approval before continuing." : "Done."), trace: res.trace });
  }
  function render() {
    mount(thread,
      state.messages.length ? null : h("div", { class: "panel" }, h("h3", null, "Try asking"),
        h("ul", { style: { margin: "8px 0 0", paddingLeft: "18px" } },
          ["Which topic was weakest in the latest Python assessment?", "How did Asha do and what should she work on?", "Create a draft quiz on SQL joins with 5 questions", "Invite the CSE A batch to the SQL assessment"].map((t) =>
            h("li", null, h("button", { class: "btn ghost sm", type: "button", onclick: () => { box.value = t; box.focus(); } }, t))))),
      state.messages.map((m) => h("div", { class: `msg ${m.role === "me" ? "me" : "ai"}` }, m.text, m.role === "ai" ? traceView(m.trace) : null)),
      state.pending ? approvalCard(state.pending) : null);
    box.disabled = send.disabled = Boolean(state.pending);
    box.placeholder = state.pending ? "Approve or reject the pending action first" : "Ask about results, build a quiz, invite a batch…";
    thread.lastElementChild?.scrollIntoView?.({ block: "end" });
  }
  function approvalCard(p) {
    const note = input({ placeholder: "Optional note for the assistant" });
    const decide = (approved) => (e) => busy(e.currentTarget, async () => {
      state.messages.push({ role: "me", text: approved ? "✅ Approved" : "❌ Rejected" });
      try { apply(await post(`/api/admin/agents/${state.thread}/decision`, { approved, note: note.value })); }
      catch (err) { state.pending = null; toastError(err); }
      render();
    });
    return h("div", { class: "approval" },
      h("h3", null, `Approval needed · ${p.action.replace(/_/g, " ")}`),
      h("p", null, p.summary),
      note,
      h("div", { class: "row" },
        h("button", { class: "btn primary", type: "button", onclick: decide(true) }, "Approve"),
        h("button", { class: "btn danger", type: "button", onclick: decide(false) }, "Reject")));
  }
  composer.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = box.value.trim();
    if (!text) return;
    state.messages.push({ role: "me", text });
    box.value = "";
    render();
    busy(send, async () => {
      try { apply(await post("/api/admin/agents/chat", { message: text, thread_id: state.thread })); }
      catch (err) {
        if (err.status === 404) state.thread = null;
        state.messages.push({ role: "ai", text: `Error: ${err.message}` });
      }
      render();
    });
  });

  mount(main,
    pageHead({ title: "AI Assistant", sub: "A Supervisor agent hands your request to specialist agents that use the platform's own tools. Reading data and creating drafts happen directly; publishing, emails and AI re-runs wait for your approval.",
      actions: [h("button", { class: "btn", type: "button", onclick: () => { state.thread = null; state.messages = []; state.pending = null; render(); } }, "New conversation")] }),
    h("div", { class: "chat" }, h("div", null, thread, composer), h("aside", { class: "panel" }, h("h2", { style: { marginBottom: "10px" } }, "Agents and tools"), agentsHost)));
  render();
  try {
    const agents = await get("/api/admin/agents");
    mount(agentsHost, agents.map((a) => h("div", null, h("b", null, a.title),
      h("div", { class: "row", style: { gap: "4px", marginTop: "4px" } }, a.tools.map((t) => pill(t.name.replace(/_/g, " "), RISK[t.risk][1]))))),
      h("p", { class: "small muted" }, "Yellow tools pause for your approval. Agents can never change scores, delete data or see codes."));
  } catch (err) { mount(agentsHost, h("p", { class: "muted" }, err.message)); }
}
