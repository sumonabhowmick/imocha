"""Low-level security helpers: secure random values, keyed hashing, password hashing,
and a small in-memory rate limiter."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.config import get_settings

_password_hasher = PasswordHasher()
# Used to keep login timing similar whether or not the email exists.
_DUMMY_PASSWORD_HASH = _password_hasher.hash("assessflow-dummy-password-for-timing")


# ---------------------------------------------------------------------------
# Passwords (Argon2id)
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-effort verification; returns False for a missing or invalid hash."""
    try:
        return _password_hasher.verify(password_hash or _DUMMY_PASSWORD_HASH, password) and bool(
            password_hash
        )
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# ---------------------------------------------------------------------------
# Random secrets and keyed hashes
# ---------------------------------------------------------------------------
def generate_token() -> str:
    """256-bit URL-safe random token (invitation links, session tokens, CSRF tokens)."""
    return secrets.token_urlsafe(32)


def generate_passcode(length: int | None = None) -> str:
    """Numeric one-time passcode from a CSPRNG (e.g. '048213')."""
    length = length or get_settings().passcode_length
    return "".join(secrets.choice("0123456789") for _ in range(length))


def keyed_hash(purpose: str, value: str) -> str:
    """HMAC-SHA256 with the application secret.

    A keyed hash (rather than a plain hash) matters for low-entropy values such as a
    6-digit passcode: without the server secret, a leaked hash cannot be brute-forced.
    `purpose` separates domains so a token hash can never match a passcode hash.
    """
    key = get_settings().app_secret_key.encode()
    return hmac.new(key, f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()


def hash_invitation_token(token: str) -> str:
    return keyed_hash("invitation-token", token)


def hash_passcode(invitation_id: int, passcode: str) -> str:
    # Binding the hash to the invitation id means a code is only valid for its invitation.
    return keyed_hash(f"passcode:{invitation_id}", passcode)


def hash_session_token(token: str) -> str:
    return keyed_hash("session-token", token)


def constant_time_equals(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return False
    return hmac.compare_digest(a, b)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
class RateLimiter:
    """Sliding-window limiter kept in process memory.

    Good enough for a single-process deployment. Persistent protections (passcode
    failure counters and lockouts) are stored in PostgreSQL separately.
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        """Record a hit; return True if the caller is still within the limit."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)


rate_limiter = RateLimiter()
