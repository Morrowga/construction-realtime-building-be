# app/routers/contact.py
from fastapi import APIRouter, BackgroundTasks, status

from app.schemas.contact import ContactRequest, ContactResponse
from app.services.email_service import send_contact_notification

router = APIRouter(prefix="/api/v1/contact", tags=["contact"])


@router.post("", response_model=ContactResponse, status_code=status.HTTP_202_ACCEPTED)
async def submit_contact(body: ContactRequest, background_tasks: BackgroundTasks) -> ContactResponse:
    """Landing page contact form — intentionally public, no auth required.

    Sends a notification email to the team's contact address via
    BackgroundTasks so the visitor gets an instant response regardless of
    SMTP latency. No DB record is kept — this is a lightweight notify-only
    endpoint. If you want a history of submissions later, add a
    ContactSubmission model here rather than bolting it onto something else.

    NOTE: no rate limiting or spam protection yet (no captcha, no
    honeypot, no per-IP throttle). Fine for now since this is unauthenticated
    and low-traffic, but worth adding before this is a public production
    marketing page getting real traffic.
    """
    background_tasks.add_task(
        send_contact_notification,
        from_email=body.email,
        description=body.description,
    )
    return ContactResponse(message="お問い合わせを受け付けました")