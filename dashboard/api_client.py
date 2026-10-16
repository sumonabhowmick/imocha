"""Thin HTTP client for the FastAPI backend.

The dashboard never talks to PostgreSQL directly: every operation goes through the
backend, which performs authentication and authorization. The admin's bearer token
is kept only in this browser session's Streamlit state.
"""
from __future__ import annotations

import os

import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
TIMEOUT = 120  # AI feedback generation can take a while


class APIError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _headers() -> dict:
    token = st.session_state.get("token")
    return {"Authorization": f"Bearer {token}"} if token else {}


def request(method: str, path: str, **kwargs):
    try:
        response = requests.request(method, f"{API_BASE_URL}{path}", headers=_headers(), timeout=TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        raise APIError(0, f"Cannot reach the backend at {API_BASE_URL}. Is FastAPI running?") from exc
    if response.status_code == 401 and st.session_state.get("token"):
        # Session expired or revoked on the server: force a fresh login.
        st.session_state.pop("token", None)
        st.session_state.pop("admin", None)
        raise APIError(401, "Your session has expired. Please log in again.")
    if response.status_code >= 400:
        try:
            body = response.json()
            message = body.get("detail", "Request failed.")
            if body.get("errors"):
                message += " " + "; ".join(f"{'.'.join(map(str, e['loc'][1:]))}: {e['msg']}" for e in body["errors"])
        except ValueError:
            message = f"Request failed (HTTP {response.status_code})."
        raise APIError(response.status_code, message)
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


def get(path: str, **params):
    return request("GET", path, params={k: v for k, v in params.items() if v is not None})


def post(path: str, json: dict | None = None, **params):
    return request("POST", path, json=json, params=params or None)


def patch(path: str, json: dict):
    return request("PATCH", path, json=json)


def delete(path: str):
    return request("DELETE", path)


def login(email: str, password: str) -> None:
    data = request("POST", "/api/auth/login", json={"email": email, "password": password})
    st.session_state["token"] = data["access_token"]
    st.session_state["admin"] = data["admin"]


def logout() -> None:
    try:
        request("POST", "/api/auth/logout")
    except APIError:
        pass
    st.session_state.clear()


def show_error(exc: APIError) -> None:
    st.error(exc.message)
