import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import jwt
import pytest
from sqlalchemy import select, update

from app.core.db import SessionLocal
from app.main import app
from app.models.base import utcnow
from app.models.live import Challenge, UserReport
from app.modules.live import router as live_router
from app.modules.live import service
from app.web.debates import debate_results, debate_sessions, user_debate_stats
from tests.test_student_auth import auth, signed_up_and_verified

TOPIC = {
    "id": "homework",
    "title": "Homework should be abolished in schools",
    "category": "education",
    "context": "",
}


class Pair:
    def __init__(
        self, client: httpx.AsyncClient, a: dict[str, str], b: dict[str, str], a_id: int, b_id: int
    ) -> None:
        self.client, self.a, self.b, self.a_id, self.b_id = client, a, b, a_id, b_id


async def make_pair(client: httpx.AsyncClient) -> Pair:
    a = auth(await signed_up_and_verified(client, "riya@school.in"))
    b = auth(await signed_up_and_verified(client, "kabir@school.in"))
    await client.patch("/v1/me", headers=a, json={"username": "riya_m"})
    await client.patch("/v1/me", headers=b, json={"username": "kabir_s", "full_name": "Kabir Shah"})
    a_id = (await client.get("/v1/me", headers=a)).json()["id"]
    b_id = (await client.get("/v1/me", headers=b)).json()["id"]
    return Pair(client, a, b, a_id, b_id)


@pytest.fixture
async def pair() -> AsyncIterator[Pair]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield await make_pair(client)


async def challenge(pair: Pair, **extra: Any) -> dict[str, Any]:
    body = {
        "topic": TOPIC,
        "format": "quick",
        "side_preference": "for",
        "scheduled_at": (utcnow() + timedelta(hours=1)).isoformat(),
        "opponent_id": pair.b_id,
    } | extra
    response = await pair.client.post("/v1/challenges", headers=pair.a, json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def start_room(pair: Pair) -> str:
    made = await challenge(pair)
    await pair.client.post(f"/v1/challenges/{made['id']}/accept", headers=pair.b)
    await pair.client.post(f"/v1/challenges/{made['id']}/ready", headers=pair.a)
    started = (await pair.client.post(f"/v1/challenges/{made['id']}/ready", headers=pair.b)).json()
    assert started["status"] == "live"
    return str(made["id"])


async def shift_start(challenge_id: str, *, seconds_ago: float) -> None:
    async with SessionLocal() as session:
        await session.execute(
            update(Challenge)
            .where(Challenge.id == uuid.UUID(challenge_id))
            .values(started_at=utcnow() - timedelta(seconds=seconds_ago))
        )
        await session.commit()
    live_router._forget_room(uuid.UUID(challenge_id))  # the room cache would otherwise hide the change


def turn(user_id: int, slot: int, text: str, phase: str = "opening") -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "slotIndex": slot,
        "speakerId": str(user_id),
        "side": "for",
        "phase": phase,
        "role": "speech",
        "text": text,
        "input": "voice",
        "usedMs": 100_000,
        "allowedMs": 120_000,
        "cutOff": False,
        "createdAt": utcnow().isoformat(),
    }


async def test_search_finds_students_by_username_or_name(pair: Pair) -> None:
    found = (await pair.client.get("/v1/users/search", headers=pair.a, params={"q": "@kab"})).json()
    assert [person["username"] for person in found] == ["kabir_s"]
    assert (
        await pair.client.get("/v1/users/search", headers=pair.a, params={"q": "riya"})
    ).json() == []  # not yourself


async def test_challenge_lifecycle_and_coin_toss(pair: Pair) -> None:
    made = await challenge(pair, side_preference="toss")
    assert made["challengerSide"] is None and made["opponent"]["username"] == "kabir_s"
    incoming = (await pair.client.get("/v1/challenges", headers=pair.b)).json()
    assert [item["id"] for item in incoming] == [made["id"]]

    assert (await pair.client.post(f"/v1/challenges/{made['id']}/accept", headers=pair.a)).status_code == 409
    later = (utcnow() + timedelta(days=1)).isoformat()
    countered = await pair.client.post(
        f"/v1/challenges/{made['id']}/counter", headers=pair.b, json={"scheduled_at": later}
    )
    assert countered.json()["status"] == "countered"
    again = await pair.client.post(
        f"/v1/challenges/{made['id']}/counter", headers=pair.a, json={"scheduled_at": later}
    )
    assert again.status_code == 409  # one counter per challenge
    accepted = (await pair.client.post(f"/v1/challenges/{made['id']}/accept", headers=pair.a)).json()
    assert accepted["status"] == "accepted" and accepted["challengerSide"] in ("for", "against")


async def test_room_turns_are_enforced_and_judged(pair: Pair) -> None:
    room = await start_room(pair)
    state = (await pair.client.get(f"/v1/live/{room}/state", headers=pair.a)).json()
    assert state["clock"]["stage"] == "waiting" and len(state["schedule"]["slots"]) == 6

    await shift_start(room, seconds_ago=4)  # slot 0 (For = the challenger, Riya) is live
    wrong = await pair.client.post(
        f"/v1/live/{room}/events", headers=pair.b, json={"kind": "handover", "slot": 0}
    )
    assert wrong.status_code == 403
    assert (
        await pair.client.post(
            f"/v1/live/{room}/events", headers=pair.a, json={"kind": "handover", "slot": 0}
        )
    ).status_code == 204
    state = (await pair.client.get(f"/v1/live/{room}/state", headers=pair.b)).json()
    assert (state["clock"]["stage"], state["clock"]["slotIndex"]) == ("cue", 1)

    forged = turn(pair.b_id, 0, "I am Riya")
    assert (await pair.client.post(f"/v1/live/{room}/turns", headers=pair.a, json=forged)).status_code == 422
    riya = turn(pair.a_id, 0, "Homework causes stress because it cuts sleep. For example, a study shows it.")
    await pair.client.post(f"/v1/live/{room}/turns", headers=pair.a, json=riya)
    # Kabir can't speak in Riya's slot, nor wipe her turn by re-using its id.
    stolen = turn(pair.b_id, 0, "Riya concedes.")
    assert (await pair.client.post(f"/v1/live/{room}/turns", headers=pair.b, json=stolen)).status_code == 403
    overwrite = turn(pair.b_id, 1, "Homework builds habits.") | {"id": riya["id"]}
    await pair.client.post(f"/v1/live/{room}/turns", headers=pair.b, json=overwrite)
    turns = (await pair.client.get(f"/v1/live/{room}/state", headers=pair.a)).json()["turns"]
    assert sorted((t["slotIndex"], t["speakerId"]) for t in turns) == [
        (0, str(pair.a_id)),
        (1, str(pair.b_id)),
    ]

    assert (await pair.client.post(f"/v1/live/{room}/judge", headers=pair.a)).json()["detail"][
        "code"
    ] == "not_finished"
    await shift_start(room, seconds_ago=3600)  # long over

    # No AI key in tests: the server's built-in judge decides; a client can't post a verdict.
    first = (await pair.client.post(f"/v1/live/{room}/judge", headers=pair.a)).json()
    assert first["judgedBy"] == "app"
    winner = first["judge"]["winnerId"]
    assert winner in (str(pair.a_id), str(pair.b_id))
    assert first["reports"] == {str(pair.a_id): None}
    forged_result = {"judge": {"winnerId": str(pair.b_id)}, "reports": {}}
    assert (
        await pair.client.post(f"/v1/live/{room}/result", headers=pair.b, json=forged_result)
    ).status_code == 405

    # Each student stores only their own private report.
    mine = await pair.client.put(
        f"/v1/live/{room}/my-report",
        headers=pair.a,
        json={"report": {"userId": str(pair.b_id), "secret": "riya"}},
    )
    assert mine.json()["reports"] == {str(pair.a_id): {"userId": str(pair.a_id), "secret": "riya"}}
    theirs = (await pair.client.get(f"/v1/live/{room}/result", headers=pair.b)).json()
    assert theirs["reports"] == {str(pair.b_id): None} and theirs["judge"]["winnerId"] == winner

    async with SessionLocal() as session:
        stored = await session.get(Challenge, uuid.UUID(room))
        assert stored is not None and stored.status == "completed" and stored.web_debate_id
        web = (
            await session.execute(select(debate_sessions).where(debate_sessions.c.id == stored.web_debate_id))
        ).one()
        assert (web.mode, web.creator_id, web.opponent_id, web.is_ai_opponent) == (
            "PVP",
            pair.a_id,
            pair.b_id,
            False,
        )
        outcome = (
            await session.execute(select(debate_results).where(debate_results.c.debate_id == web.id))
        ).one()
        assert str(outcome.winner_id) == winner
        stats = {row.user_id: row for row in (await session.execute(select(user_debate_stats))).all()}
        loser = pair.b_id if winner == str(pair.a_id) else pair.a_id
        assert (stats[int(winner)].pvp_debates, stats[int(winner)].total_wins) == (1, 1)
        assert (stats[loser].pvp_debates, stats[loser].total_losses) == (1, 1)


async def test_leaving_early_loses_by_forfeit(pair: Pair) -> None:
    room = await start_room(pair)
    await shift_start(room, seconds_ago=4)
    strong = (
        "First, homework harms sleep because studies show stress. "
        "For example, research from Finland. Therefore abolish it."
    )
    await pair.client.post(f"/v1/live/{room}/turns", headers=pair.a, json=turn(pair.a_id, 0, strong))
    left = await pair.client.post(
        f"/v1/live/{room}/events", headers=pair.a, json={"kind": "leave", "slot": None}
    )
    assert left.status_code == 204
    result = (await pair.client.post(f"/v1/live/{room}/judge", headers=pair.b)).json()
    assert result["judge"]["winnerId"] == str(pair.b_id)
    assert "left the debate early" in result["judge"]["reasons"][0]


async def test_open_invite_is_claimed_once(pair: Pair) -> None:
    made = await challenge(pair, opponent_id=None)
    claimed = await pair.client.get(f"/v1/challenges/code/{made['code'].lower()}", headers=pair.b)
    assert claimed.json()["opponent"]["username"] == "kabir_s"
    third = auth(await signed_up_and_verified(pair.client, "zara@school.in"))
    taken = await pair.client.get(f"/v1/challenges/code/{made['code']}", headers=third)
    assert taken.status_code == 409


async def test_blocking_hides_and_cancels(pair: Pair) -> None:
    made = await challenge(pair)
    assert (await pair.client.post(f"/v1/users/{pair.a_id}/block", headers=pair.b)).status_code == 204
    assert (await pair.client.get(f"/v1/challenges/{made['id']}", headers=pair.a)).json()[
        "status"
    ] == "cancelled"
    assert (await pair.client.get("/v1/users/search", headers=pair.a, params={"q": "kabir"})).json() == []
    again = await pair.client.post(
        "/v1/challenges",
        headers=pair.a,
        json={
            "topic": TOPIC,
            "format": "quick",
            "side_preference": "for",
            "scheduled_at": (utcnow() + timedelta(hours=1)).isoformat(),
            "opponent_id": pair.b_id,
        },
    )
    assert again.status_code == 404


async def test_reports_reach_the_moderation_queue(pair: Pair) -> None:
    room = await start_room(pair)
    sent = await pair.client.post(
        f"/v1/users/{pair.a_id}/report",
        headers=pair.b,
        json={"reason": "abuse", "details": "Rude", "challenge_id": room},
    )
    assert sent.status_code == 201
    async with SessionLocal() as session:
        report = await session.scalar(select(UserReport))
        assert report is not None and report.reported_id == pair.a_id and str(report.challenge_id) == room


async def test_audio_needs_livekit(pair: Pair, monkeypatch: pytest.MonkeyPatch) -> None:
    room = await start_room(pair)
    assert (await pair.client.get(f"/v1/live/{room}/audio", headers=pair.a)).json()["detail"][
        "code"
    ] == "audio_unavailable"

    settings = type(
        "S",
        (),
        {"livekit_url": "wss://x.livekit.cloud", "livekit_api_key": "key", "livekit_api_secret": "s" * 32},
    )()
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    token = service.livekit_token(room="sym-live-1", identity="42", name="Riya")
    assert token is not None
    claims = jwt.decode(token["token"], "s" * 32, algorithms=["HS256"])
    assert (
        claims["sub"] == "42"
        and claims["video"]["room"] == "sym-live-1"
        and claims["video"]["canPublish"] is True
    )


async def test_moderators_resolve_reports_and_can_pause_access(pair: Pair, admin_client: Any) -> None:
    room = await start_room(pair)
    await pair.client.post(
        f"/v1/users/{pair.a_id}/report", headers=pair.b, json={"reason": "abuse", "challenge_id": room}
    )

    analyst = await admin_client("analyst")
    assert (await analyst.get("/admin/api/moderation/reports")).status_code == 403
    assert (await analyst.get("/admin/api/moderation/open-count")).json() == {"open": 1}

    moderator = await admin_client("moderator")
    queue = (await moderator.get("/admin/api/moderation/reports")).json()
    report = queue["items"][0]
    assert report["reported"]["username"] == "riya_m" and report["reports_against_reported"] == 1
    detail = (await moderator.get(f"/admin/api/live/challenges/{room}")).json()
    assert detail["challenge"]["id"] == room

    resolved = await moderator.post(
        f"/admin/api/moderation/reports/{report['id']}/resolve",
        json={"action": "actioned", "note": "Abusive language", "pause_app_access": True},
    )
    assert resolved.json() == {"status": "actioned"}
    blocked = await pair.client.get("/v1/me", headers=pair.a)
    assert blocked.status_code == 403 and blocked.json()["detail"]["code"] == "app_suspended"
    assert (await moderator.get("/admin/api/moderation/open-count")).json() == {"open": 0}

    listing = (await moderator.get("/admin/api/live/challenges", params={"status": "live"})).json()
    assert listing["total"] == 1 and listing["items"][0]["challenger"] == "Riya Mehta"
    assert (await moderator.post(f"/admin/api/live/challenges/{room}/cancel")).json() == {
        "status": "cancelled"
    }
