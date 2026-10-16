"""Application error type. Services raise AppError; main.py converts it to a safe JSON
response so internal details (SQL, stack traces, secrets) never reach the client."""
from __future__ import annotations


class AppError(Exception):
    def __init__(self, status_code: int, message: str, code: str = "error") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code


def not_found(what: str = "Resource") -> AppError:
    return AppError(404, f"{what} not found.", "not_found")


def conflict(message: str, code: str = "conflict") -> AppError:
    return AppError(409, message, code)


def bad_request(message: str, code: str = "bad_request") -> AppError:
    return AppError(400, message, code)
