"""Check SMTP settings and optionally send one test email to your own address.

Usage (from the project root, with the virtual environment active):
    python -m scripts.send_test_email --check-only            # TLS + login only, sends nothing
    python -m scripts.send_test_email --to you@gmail.com      # sends one harmless test email

It always uses SMTP (even if EMAIL_BACKEND=outbox), reads every setting from .env,
never prints the password, and does not touch the database.
"""
from __future__ import annotations

import argparse
import sys

from email_validator import EmailNotValidError, validate_email

from app.config import get_settings
from app.services.email_service import EmailDeliveryError, check_smtp_connection, send_test_email


def _mask(address: str) -> str:
    local, _, domain = address.partition("@")
    return f"{local[:2]}***@{domain}" if domain else "(not set)"


def main() -> int:
    parser = argparse.ArgumentParser(description="Test AssessFlow AI SMTP email delivery.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--to", help="Recipient address for one test email (use your own).")
    group.add_argument("--check-only", action="store_true", help="Verify TLS and login without sending.")
    args = parser.parse_args()

    settings = get_settings()
    print(f"SMTP host:      {settings.smtp_host or '(not set)'}:{settings.smtp_port}")
    print(f"Encryption:     {'implicit TLS (SSL)' if settings.smtp_use_ssl else 'STARTTLS'}")
    print(f"Username:       {_mask(settings.smtp_username)}")
    print(f"Password:       {'set' if settings.smtp_password else 'NOT SET'}")
    print(f"From address:   {_mask(settings.smtp_from_email)}")
    print(f"EMAIL_BACKEND:  {settings.email_backend}" + ("  (app currently saves emails to files, not sending)" if settings.email_backend == "outbox" else ""))
    if (
        settings.smtp_host.lower().endswith("gmail.com")
        and settings.smtp_from_email.lower() != settings.smtp_username.lower()
    ):
        print("Warning: Gmail rewrites the From address unless SMTP_FROM_EMAIL matches SMTP_USERNAME.")

    try:
        if args.check_only:
            check_smtp_connection()
            print("OK: connected, TLS established and login accepted. Nothing was sent.")
        else:
            try:
                to = validate_email(args.to, check_deliverability=False).normalized
            except EmailNotValidError as exc:
                print(f"Invalid --to address: {exc}", file=sys.stderr)
                return 2
            send_test_email(to)
            print(f"OK: the SMTP server accepted a test email for {_mask(to)}. Check that inbox (and spam).")
    except EmailDeliveryError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
