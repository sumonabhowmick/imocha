"""Reset an admin's password (for when it has been forgotten).

Usage (from the project root, with the virtual environment active):
    python -m scripts.reset_admin_password --email you@example.com

The new password is prompted for (hidden) and never accepted as a command-line
argument, so it does not end up in shell history. Every existing session of that admin
is signed out. Only someone with access to this server and its .env can run this.
"""
from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy.exc import OperationalError, ProgrammingError

from app.database import SessionLocal
from app.services import audit_service
from app.services.auth_service import reset_admin_password


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset an AssessFlow AI admin password.")
    parser.add_argument("--email", required=True)
    args = parser.parse_args()

    password = getpass.getpass("New password (min 12 characters, letters with digits or symbols): ")
    if password != getpass.getpass("Confirm new password: "):
        print("Passwords do not match.", file=sys.stderr)
        return 1
    try:
        with SessionLocal() as db:
            user = reset_admin_password(db, args.email, password)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except OperationalError:
        print("Error: cannot connect to PostgreSQL. Check DATABASE_URL in .env.", file=sys.stderr)
        return 1
    except ProgrammingError:
        print("Error: tables are missing. Run 'alembic upgrade head' first.", file=sys.stderr)
        return 1
    audit_service.record("admin.password_reset", actor_type="system", actor_label="reset_admin_password script", target_type="user", target_id=user.id)
    print(f"Password reset for {user.email}. All of their existing sessions were signed out.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
