"""AssessFlow AI - admin dashboard (Streamlit).

Run with:  streamlit run dashboard/app.py

The dashboard is a client of the FastAPI backend. It is not the security boundary:
every request is authorized again by the backend.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))  # allow `import api_client` from pages

import api_client as api  # noqa: E402

st.set_page_config(page_title="AssessFlow AI Admin", page_icon="📋", layout="wide")


def login_page() -> None:
    st.title("AssessFlow AI")
    st.caption("Administrator sign-in")
    with st.form("login", clear_on_submit=False):
        email = st.text_input("Admin email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in", type="primary")
    if submitted:
        if not email or not password:
            st.error("Enter your email and password.")
            return
        try:
            api.login(email, password)
            st.rerun()
        except api.APIError as exc:
            st.error(exc.message)


if "token" not in st.session_state:
    st.navigation([st.Page(login_page, title="Login", icon="🔐")]).run()
else:
    pages_dir = Path(__file__).resolve().parent / "pages"
    navigation = st.navigation(
        {
            "Monitor": [
                st.Page(pages_dir / "overview.py", title="Overview", icon="📊", default=True),
                st.Page(pages_dir / "results.py", title="Results", icon="🏁"),
                st.Page(pages_dir / "feedback.py", title="Feedback reports", icon="🧠"),
            ],
            "Manage": [
                st.Page(pages_dir / "assessments.py", title="Assessments", icon="📝"),
                st.Page(pages_dir / "candidates.py", title="Candidates & invitations", icon="✉️"),
                st.Page(pages_dir / "email_status.py", title="Email status", icon="📬"),
            ],
        }
    )
    with st.sidebar:
        admin = st.session_state.get("admin", {})
        st.markdown(f"Signed in as **{admin.get('name', '')}**")
        st.caption(admin.get("email", ""))
        if st.button("Log out"):
            api.logout()
            st.rerun()
    navigation.run()
