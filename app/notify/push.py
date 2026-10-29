"""Push notifications through Expo's push service (it delivers to Apple and Google for us).

`notify()` is called after the database change it reports has been committed. It never raises: a
notification that can't be delivered must not break the action that caused it. Tokens Expo reports as no
longer registered (app uninstalled, notifications turned off) are removed.
"""

import logging
from collections.abc import Iterable
from typing import Any

import httpx
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import Notification, PushToken

log = logging.getLogger("sym.push")

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
BATCH = 100  # Expo accepts up to 100 messages per request


async def _send(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    access_token = get_settings().expo_access_token
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    tickets: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=15) as client:
        for start in range(0, len(messages), BATCH):
            response = await client.post(EXPO_PUSH_URL, json=messages[start : start + BATCH], headers=headers)
            response.raise_for_status()
            tickets.extend(response.json().get("data", []))
    return tickets


async def notify(
    user_ids: Iterable[int] | None,
    *,
    kind: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> None:
    """Send to these students' devices (None = every student with the app: an announcement)."""
    targets = None if user_ids is None else sorted(set(user_ids))
    payload = data or {}
    try:
        async with SessionLocal() as session:
            stmt = select(PushToken.token, PushToken.user_id)
            if targets is not None:
                if not targets:
                    return
                stmt = stmt.where(PushToken.user_id.in_(targets))
            devices = list(await session.execute(stmt))
            accepted: dict[int, int] = {}
            error = None
            if devices and get_settings().push_enabled:
                messages = [
                    {"to": token, "title": title, "body": body, "data": payload, "sound": "default"}
                    for token, _ in devices
                ]
                try:
                    tickets = await _send(messages)
                    gone = []
                    for (token, owner), ticket in zip(devices, tickets, strict=False):
                        if ticket.get("status") == "ok":
                            accepted[owner] = accepted.get(owner, 0) + 1
                        elif ticket.get("details", {}).get("error") == "DeviceNotRegistered":
                            gone.append(token)
                    if gone:
                        await session.execute(delete(PushToken).where(PushToken.token.in_(gone)))
                except (httpx.HTTPError, ValueError) as failure:
                    error = f"{type(failure).__name__}: {str(failure)[:250]}"
                    log.warning("Push %s failed: %s", kind, error)
            elif devices:
                error = "Push sending is switched off (PUSH_ENABLED=false)"
            for user_id in targets if targets is not None else [None]:
                session.add(
                    Notification(
                        user_id=user_id,
                        kind=kind,
                        title=title[:120],
                        body=body[:300],
                        data=payload,
                        devices=sum(1 for _, owner in devices if user_id is None or owner == user_id),
                        delivered=sum(accepted.values()) if user_id is None else accepted.get(user_id, 0),
                        error=error,
                    )
                )
            await session.commit()
    except Exception:  # never let a notification break the action that triggered it
        log.exception("Couldn't record or send the %s notification", kind)
