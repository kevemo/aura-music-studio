from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

from .branding import PRODUCT_FULL_NAME

DEFAULT_ADMIN_EMAIL = "elevatesoulsproductions@gmail.com"


def _public_url() -> str:
    return (
        os.getenv("LSS_PUBLIC_BASE_URL")
        or os.getenv("LSS_PUBLIC_URL")
        or "http://127.0.0.1:8000"
    ).rstrip("/")


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def send_email(to_address: str, subject: str, body: str) -> dict:
    """Send through configured SMTP.

    If SMTP is not configured, write a development outbox file rather than pretending
    the message was delivered. Production deployment should configure SMTP credentials.
    """
    host = (os.getenv("LSS_SMTP_HOST") or "").strip()
    user = (os.getenv("LSS_SMTP_USERNAME") or os.getenv("LSS_SMTP_USER") or "").strip()
    password = os.getenv("LSS_SMTP_PASSWORD") or ""
    from_address = (os.getenv("LSS_SMTP_FROM") or user or DEFAULT_ADMIN_EMAIL).strip()
    port = int(os.getenv("LSS_SMTP_PORT", "587"))
    use_starttls = _truthy(os.getenv("LSS_SMTP_STARTTLS"), default=True)

    message = EmailMessage()
    message["From"] = from_address
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)

    if not host or not user or not password:
        outbox = Path(os.getenv("LSS_DEV_OUTBOX", "data/dev_outbox"))
        outbox.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() else "_" for c in subject)[:70]
        target = outbox / f"{safe}.eml"
        target.write_bytes(message.as_bytes())
        return {"sent": False, "delivery": "development_outbox", "path": str(target)}

    if port == 465 and not use_starttls:
        with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
            smtp.login(user, password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.ehlo()
            if use_starttls:
                smtp.starttls()
                smtp.ehlo()
            smtp.login(user, password)
            smtp.send_message(message)
    return {"sent": True, "delivery": "smtp", "to": to_address}


def notify_membership_request(*, approval_token: str, applicant_email: str, display_name: str, plan_id: str) -> dict:
    admin_email = (
        os.getenv("LSS_MEMBERSHIP_APPROVAL_EMAIL")
        or os.getenv("LSS_ADMIN_APPROVAL_EMAIL")
        or DEFAULT_ADMIN_EMAIL
    ).strip()
    review_url = f"{_public_url()}/membership/review?token={approval_token}"
    subject = f"{PRODUCT_FULL_NAME} membership request — {display_name}"
    body = f"""A new membership request needs approval.

Applicant: {display_name}
Email: {applicant_email}
Requested plan: {plan_id.upper()}

Review and approve/reject:
{review_url}

This approval link is single-use and expires automatically.
"""
    return send_email(admin_email, subject, body)


def notify_membership_decision(*, applicant_email: str, display_name: str, approved: bool, plan_id: str, payment_url: str | None = None) -> dict:
    if approved:
        subject = f"Your {PRODUCT_FULL_NAME} membership was approved"
        body = f"Hello {display_name},\n\nYour {plan_id.upper()} membership request has been approved.\n"
        if payment_url:
            body += f"\nComplete payment here to activate your paid membership:\n{payment_url}\n"
        else:
            body += "\nYour membership is now active.\n"
        body += f"\nWelcome to {PRODUCT_FULL_NAME}.\n"
    else:
        subject = f"Update on your {PRODUCT_FULL_NAME} membership request"
        body = f"Hello {display_name},\n\nYour membership request was not approved at this time.\n"
    return send_email(applicant_email, subject, body)
