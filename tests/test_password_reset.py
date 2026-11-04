"""Admin password reset (scripts/reset_admin_password.py)."""
from __future__ import annotations

import pytest

from app.services.auth_service import reset_admin_password
from tests.helpers import ADMIN_EMAIL, ADMIN_PASSWORD, admin_headers, setup_admin

NEW_PASSWORD = "Brand-New-Passw0rd!"


def test_reset_changes_password_and_signs_out_old_sessions(client, db):
    old_headers = setup_admin(client)
    assert client.get("/api/auth/me", headers=old_headers).status_code == 200

    reset_admin_password(db, ADMIN_EMAIL.upper(), NEW_PASSWORD)

    assert client.get("/api/auth/me", headers=old_headers).status_code == 401  # old session revoked
    assert client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}).status_code == 401
    assert client.get("/api/auth/me", headers=admin_headers(client, ADMIN_EMAIL, NEW_PASSWORD)).status_code == 200


def test_reset_rejects_weak_password_and_unknown_email(client, db):
    setup_admin(client)
    with pytest.raises(ValueError, match="at least 12"):
        reset_admin_password(db, ADMIN_EMAIL, "short1")
    with pytest.raises(ValueError, match="No admin account"):
        reset_admin_password(db, "nobody@example.com", NEW_PASSWORD)


def test_reset_script(client, monkeypatch, capsys):
    from scripts import reset_admin_password as script

    setup_admin(client)
    answers = iter([NEW_PASSWORD, NEW_PASSWORD])
    monkeypatch.setattr(script.getpass, "getpass", lambda prompt="": next(answers))
    monkeypatch.setattr(script.sys, "argv", ["reset_admin_password", "--email", ADMIN_EMAIL])
    assert script.main() == 0
    out = capsys.readouterr().out
    assert "Password reset" in out and NEW_PASSWORD not in out

    mismatch = iter([NEW_PASSWORD, "something-else-123"])
    monkeypatch.setattr(script.getpass, "getpass", lambda prompt="": next(mismatch))
    assert script.main() == 1
