# app/services/email_service.py
"""Minimal SMTP email sending for transactional messages: teammate
temp-password credentials, and landing page contact form notifications.
Uses plain smtplib deliberately — swap this for a provider (SES,
Postmark, SendGrid, etc.) later without touching any caller.

REQUIRES these settings to exist on app.config.settings — add them to your
Settings class and .env if they aren't there yet:

    smtp_host: str
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str
    smtp_use_tls: bool = True
    contact_notify_email: str  (where contact-form submissions get sent)

Runs as a FastAPI BackgroundTask (see routers/organizations.py and
routers/contact.py) rather than a Celery task, since app/workers/tasks.py
and celery_app.py weren't available to edit directly here. If you want
this fully async/retryable via Celery instead, move each function body
into a @celery_app.task in workers/tasks.py and call `.delay(...)` from
the router instead of background_tasks.add_task(...) — signatures can
stay identical.
"""
import logging
import smtplib
from email.mime.text import MIMEText

from app.config import settings

logger = logging.getLogger(__name__)


def send_email(to_email: str, subject: str, body: str, reply_to: str | None = None) -> None:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from_email
    msg["To"] = to_email
    if reply_to:
        msg["Reply-To"] = reply_to

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            if settings.smtp_use_tls:
                server.starttls()
            if settings.smtp_user and settings.smtp_password:
                server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.smtp_from_email, [to_email], msg.as_string())
    except Exception:
        # Don't let an email failure break the request that triggered it.
        # Log loudly so it's visible in ops; consider adding a
        # retry/dead-letter path later.
        logger.exception("Failed to send email to %s", to_email)


def send_temp_credentials_email(
    to_email: str,
    full_name: str | None,
    temp_password: str,
    organization_name: str,
) -> None:
    greeting = f"Hi {full_name}," if full_name else "Hi,"
    body = (
        f"{greeting}\n\n"
        f"You've been added to {organization_name} on the Construction Progress Platform.\n\n"
        f"Login email: {to_email}\n"
        f"Temporary password: {temp_password}\n\n"
        f"Please log in and change your password as soon as possible.\n"
    )
    send_email(to_email, f"Your account for {organization_name}", body)


def send_contact_notification(from_email: str, description: str) -> None:
    """Landing page contact form → notifies the team's contact address.
    reply_to is set to the visitor's own email so replying from a normal
    mail client goes straight back to them, not to smtp_from_email.
    """
    body = (
        f"New contact form submission from the landing page.\n\n"
        f"From: {from_email}\n\n"
        f"Message:\n{description}\n"
    )
    send_email(
        settings.contact_notify_email,
        "New contact form submission",
        body,
        reply_to=from_email,
    )