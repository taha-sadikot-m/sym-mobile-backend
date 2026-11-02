"""Sending email. Uses Resend when a key is configured; otherwise logs the message (local development)
and keeps it in `outbox` so tests can read the codes that would have been sent."""

import html
import logging
from collections import deque
from dataclasses import dataclass

import httpx

from app.core.config import get_settings

log = logging.getLogger("sym.email")

RESEND_URL = "https://api.resend.com/emails"


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    text: str
    html: str


# The last few messages, for tests and local debugging (bounded so a long-running server never grows it).
outbox: deque[Email] = deque(maxlen=50)


def consent_email(*, to: str, student: str, code: str) -> Email:
    """Sent to a parent or guardian: the code they share with the student to agree to live debates."""
    first = student.split(" ")[0] if student else "Your child"
    subject = f"{code} — {first} is asking to join live debates on Speak Your Mind"
    lead = (
        f"{first} uses Speak Your Mind, SYM Education's practice app for debate and Model UN. "
        "Live 1:1 debates let them debate another student of a similar age by voice, with an AI judge. "
        "Recordings are never stored, students can't chat with each other, "
        "and anyone can be reported or blocked."
    )
    ask = f"If you agree, give {first} this code to enter in the app:"
    footer = "It expires in 15 minutes. If you don't agree, ignore this email and live debates stay off."
    text = f"Hello,\n\n{lead}\n\n{ask}\n\n{code}\n\n{footer}\n\nSpeak Your Mind · SYM Education"
    body = (
        '<div style="font-family:Inter,Arial,sans-serif;max-width:480px;margin:auto;color:#0f1b33">'
        f"<p>Hello,</p><p>{html.escape(lead)}</p><p>{html.escape(ask)}</p>"
        '<p style="font-size:32px;letter-spacing:8px;font-weight:700;color:#012a6c;background:#f5f6fa;'
        f'padding:16px;text-align:center;border-radius:12px">{code}</p>'
        f'<p style="color:#6b7280;font-size:13px">{html.escape(footer)}</p>'
        "<p>Speak Your Mind · SYM Education</p></div>"
    )
    return Email(to=to, subject=subject, text=text, html=body)


def code_email(*, to: str, name: str, code: str, purpose: str) -> Email:
    first = name.split(" ")[0] if name else "there"
    if purpose == "verify_email":
        subject = f"{code} is your Speak Your Mind code"
        lead = "Enter this code in the app to confirm your email address."
    else:
        subject = f"{code} — reset your Speak Your Mind password"
        lead = "Enter this code in the app to choose a new password."
    footer = "It expires in 15 minutes. If you didn’t ask for this, you can ignore this email."
    text = f"Hi {first},\n\n{lead}\n\n{code}\n\n{footer}\n\nSpeak Your Mind"
    body = (
        '<div style="font-family:Inter,Arial,sans-serif;max-width:480px;margin:auto;color:#0f1b33">'
        f"<p>Hi {html.escape(first)},</p><p>{lead}</p>"
        '<p style="font-size:32px;letter-spacing:8px;font-weight:700;color:#012a6c;background:#f5f6fa;'
        f'padding:16px;text-align:center;border-radius:12px">{code}</p>'
        f'<p style="color:#6b7280;font-size:13px">{footer}</p><p>Speak Your Mind</p></div>'
    )
    return Email(to=to, subject=subject, text=text, html=body)


async def send(message: Email) -> bool:
    """Send an email; never raises (a failed email must not break sign-up or sign-in)."""
    settings = get_settings()
    outbox.append(message)
    if settings.email_delivery != "resend" or not (settings.resend_api_key and settings.resend_from_email):
        if settings.is_deployed:
            log.error("Email not sent: Resend isn't configured (EMAIL_DELIVERY=%s)", settings.email_delivery)
        else:
            log.warning(
                "Email not sent (EMAIL_DELIVERY=log) to %s: %s\n%s", message.to, message.subject, message.text
            )
        return False
    sender = f"{settings.email_from_name} <{settings.resend_from_email}>"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                RESEND_URL,
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                json={
                    "from": sender,
                    "to": [message.to],
                    "subject": message.subject,
                    "text": message.text,
                    "html": message.html,
                },
            )
        if response.status_code >= 300:
            log.error("Resend refused an email (%s): %s", response.status_code, response.text[:300])
            return False
        return True
    except httpx.HTTPError as error:
        log.error("Couldn't reach Resend: %s", error)
        return False
