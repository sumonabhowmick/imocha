"""Create an admin account (there is no public admin registration).

Usage (from the project root, with the virtual environment active):
    python -m scripts.create_admin --name "Your Name" --email you@example.com

The password is prompted for (hidden) and never accepted as a command-line argument,
so it does not end up in shell history.
"""
from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy.exc import OperationalError, ProgrammingError

from app.database import SessionLocal
from app.services.auth_service import create_admin


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an AssessFlow AI admin account.")
    parser.add_argument("--name", required=True)
    parser.add_argument("--email", required=True)
    args = parser.parse_args()

    password = getpass.getpass("Password (min 12 characters): ")
    if password != getpass.getpass("Confirm password: "):
        print("Passwords do not match.", file=sys.stderr)
        return 1
    try:
        with SessionLocal() as db:
            user = create_admin(db, args.name, args.email, password)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except OperationalError:
        print("Error: cannot connect to PostgreSQL. Check DATABASE_URL in .env.", file=sys.stderr)
        return 1
    except ProgrammingError:
        print("Error: tables are missing. Run 'alembic upgrade head' first.", file=sys.stderr)
        return 1
    print(f"Admin created: {user.email} (id {user.id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
