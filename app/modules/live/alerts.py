"""The push notifications live debates send (wording in one place)."""

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks

from app.models.live import Challenge
from app.notify.push import notify

# Students are in India; times in notifications are shown in IST.
LOCAL = ZoneInfo("Asia/Kolkata")
FORMATS = {"quick": "Quick Clash", "standard": "Standard", "rapid": "Rapid Fire"}


def when(moment: datetime) -> str:
    local = moment.astimezone(LOCAL)
    return local.strftime("%a %d %b, %I:%M %p").replace(" 0", " ")


def _topic(challenge: Challenge) -> str:
    title = str(challenge.topic.get("title", "a motion"))
    return f"“{title[:80]}”"


def message(kind: str, challenge: Challenge, actor: str = "") -> tuple[str, str]:
    """Title and body for one kind of live-debate notification."""
    first = actor.split(" ")[0] or "Your opponent"
    fmt = FORMATS.get(challenge.format, "Live")
    texts = {
        "challenge.received": (
            f"{first} challenged you to a live debate",
            f"{_topic(challenge)} · {fmt} · {when(challenge.scheduled_at)} IST",
        ),
        "challenge.claimed": (
            f"{first} opened your invite",
            f"{_topic(challenge)} — waiting for them to accept.",
        ),
        "challenge.accepted": (
            f"{first} accepted your challenge",
            f"{_topic(challenge)} · {when(challenge.scheduled_at)} IST",
        ),
        "challenge.declined": (f"{first} can’t make this debate", _topic(challenge)),
        "challenge.countered": (
            f"{first} proposed a new time",
            f"{when(challenge.scheduled_at)} IST · tap to answer",
        ),
        "challenge.cancelled": (f"{first} cancelled the debate", _topic(challenge)),
        "challenge.lobby": (f"{first} is in the lobby", "Tap I’m ready to start your debate."),
        "challenge.reminder": (
            "Your live debate starts soon",
            f"{_topic(challenge)} at {when(challenge.scheduled_at)} IST",
        ),
        "result.ready": ("The verdict is in", f"See who won {_topic(challenge)} and your personal report."),
    }
    return texts[kind]


def send(
    background: BackgroundTasks, kind: str, recipients: list[int | None], *, challenge: Challenge, actor: str
) -> None:
    """Queue one live-debate notification to run after the response is sent."""
    title, body = message(kind, challenge, actor)
    targets = [uid for uid in recipients if uid is not None]
    background.add_task(
        notify,
        targets,
        kind=kind,
        title=title,
        body=body,
        data={"type": kind, "challengeId": str(challenge.id)},
    )
