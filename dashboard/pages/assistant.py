import json

import streamlit as st

import api_client as api

AGENT_LABELS = {
    "supervisor": "🧭 Supervisor",
    "analytics": "📊 Analytics Agent",
    "quiz_builder": "📝 Quiz Builder Agent",
    "operations": "✉️ Operations Agent",
    "coaching": "🎓 Coaching Agent",
}
RISK_LABELS = {"read": "read", "write": "safe write", "approval": "needs approval"}

st.title("AI Assistant")
st.caption(
    "A Supervisor agent delegates your request to specialist agents that use the platform's own tools. "
    "Reading data and creating drafts happen directly; publishing, sending emails and re-running AI "
    "always wait for your approval. The assistant cannot change scores or delete data."
)

state = st.session_state
state.setdefault("agent_thread", None)
state.setdefault("agent_chat", [])  # [{"role": "admin"|"assistant", "text": str, "trace": list}]
state.setdefault("agent_pending", None)


def render_trace(trace: list[dict]) -> None:
    if not trace:
        return
    with st.expander(f"How the agents worked ({sum(s['type'] == 'tool_call' for s in trace)} tool calls)"):
        for step in trace:
            agent = AGENT_LABELS.get(step.get("agent"), step.get("agent") or "")
            if step["type"] == "delegate":
                st.markdown(f"**{agent}** → {step['text']}")
            elif step["type"] == "tool_call":
                st.markdown(f"**{agent}** called `{step['tool']}` ({RISK_LABELS.get(step.get('risk'), '')})")
                if step.get("args"):
                    st.code(json.dumps(step["args"], indent=1, default=str), language="json")
            elif step["type"] == "tool_result":
                st.caption(f"Result of `{step['tool']}`: {step['text'][:400]}")
            elif step["type"] == "message":
                st.markdown(f"**{agent}:** {step['text']}")


def handle(response: dict) -> None:
    state.agent_thread = response["thread_id"]
    state.agent_pending = response.get("pending_approval")
    text = response.get("answer") or ("Waiting for your approval below." if state.agent_pending else "Done.")
    state.agent_chat.append({"role": "assistant", "text": text, "trace": response.get("trace", [])})


# --- sidebar: agents and tools ---------------------------------------------------
with st.sidebar:
    if st.button("New conversation", width="stretch"):
        state.agent_thread, state.agent_chat, state.agent_pending = None, [], None
        st.rerun()
    with st.expander("Agents and their tools"):
        try:
            for agent in api.get("/api/admin/agents"):
                st.markdown(f"**{AGENT_LABELS.get(agent['agent'], agent['title'])}**")
                for tool in agent["tools"]:
                    st.caption(f"`{tool['name']}` · {RISK_LABELS[tool['risk']]}")
        except api.APIError as exc:
            api.show_error(exc)

# --- conversation ------------------------------------------------------------------
if not state.agent_chat:
    st.info(
        "Try: *“Which topic was weakest in the Python assessment?”*, *“How did Asha do and what should she work on?”*, "
        "*“Create a draft quiz on SQL joins with 5 questions”* or *“Invite Ravi to the Python assessment.”*"
    )

for entry in state.agent_chat:
    with st.chat_message("user" if entry["role"] == "admin" else "assistant"):
        st.markdown(entry["text"])
        render_trace(entry.get("trace", []))

# --- pending approval --------------------------------------------------------------
if state.agent_pending:
    pending = state.agent_pending
    with st.container(border=True):
        st.subheader("⚠️ Approval needed")
        st.markdown(f"**Action:** `{pending['action']}`")
        st.markdown(pending["summary"])
        note = st.text_input("Note for the assistant (optional)", key="agent_note")
        approve, reject = st.columns(2)
        decision = None
        if approve.button("Approve", type="primary", width="stretch"):
            decision = True
        if reject.button("Reject", width="stretch"):
            decision = False
        if decision is not None:
            state.agent_chat.append({"role": "admin", "text": ("✅ Approved" if decision else "❌ Rejected") + (f": {note}" if note else "")})
            with st.spinner("The agents are continuing..."):
                try:
                    handle(api.post(f"/api/admin/agents/{state.agent_thread}/decision", json={"approved": decision, "note": note}))
                except api.APIError as exc:
                    state.agent_pending = None
                    state.agent_chat.append({"role": "assistant", "text": f"Error: {exc.message}"})
            st.rerun()

# --- input -------------------------------------------------------------------------
prompt = st.chat_input("Ask the assistant...", disabled=bool(state.agent_pending))
if prompt:
    state.agent_chat.append({"role": "admin", "text": prompt})
    with st.spinner("The agents are working..."):
        try:
            handle(api.post("/api/admin/agents/chat", json={"message": prompt, "thread_id": state.agent_thread}))
        except api.APIError as exc:
            if exc.status == 404:  # conversation expired (e.g. server restarted)
                state.agent_thread = None
            state.agent_chat.append({"role": "assistant", "text": f"Error: {exc.message}"})
    st.rerun()
