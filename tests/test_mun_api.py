import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import func, select

from app.ai import comfort, deepgram
from app.core.db import SessionLocal
from app.main import app
from app.models import AiCall
from app.modules.mun import ai
from tests.test_chanakya_api import set_language
from tests.test_student_auth import auth, signed_up_and_verified

pytestmark = pytest.mark.usefixtures("seeded")

CONTEXT = {
    "committee": "UN Security Council",
    "committee_slug": "unsc",
    "agenda": "Strengthening peacekeeping mandates",
    "country": "India",
}
SPEECH = (
    "India believes peacekeeping mandates must be realistic and funded. Underfunded missions fail civilians."
)


@pytest.fixture
async def student() -> AsyncIterator[tuple[httpx.AsyncClient, dict[str, str]]]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        tokens = await signed_up_and_verified(client)
        yield client, auth(tokens)


def fake_generate(answers: dict[str, dict[str, Any]]) -> Any:
    async def generate(*, feature: str, shape: type[BaseModel], **_: Any) -> BaseModel:
        if feature not in answers:
            raise ai.AiUnavailable("no fake answer")
        return shape.model_validate(answers[feature])

    return generate


async def test_ai_steps_fall_back_cleanly_without_a_key(
    student: tuple[httpx.AsyncClient, dict[str, str]],
) -> None:
    client, headers = student
    response = await client.post("/v1/mun/speech-analysis", headers=headers, json={**CONTEXT, "text": SPEECH})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ai_unavailable"


async def test_opponents_must_come_from_the_committee(
    student: tuple[httpx.AsyncClient, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = student
    picks = [
        {
            "country_code": "FR",
            "stance": "ally",
            "interests": ["Africa"],
            "position": "Backs robust mandates.",
            "personality": "Formal and legalistic",
            "context": "Leads many Sahel files.",
        },
        {
            "country_code": "US",
            "stance": "different",
            "interests": ["Budget"],
            "position": "Wants efficiency.",
            "personality": "Direct",
            "context": "Largest funder.",
        },
        {
            "country_code": "RU",
            "stance": "challenger",
            "interests": ["Sovereignty"],
            "position": "Wary of mandates.",
            "personality": "Precise",
            "context": "Permanent member.",
        },
    ]
    monkeypatch.setattr(ai, "generate", fake_generate({"mun.opponents": {"opponents": picks}}))
    good = await client.post("/v1/mun/opponents", headers=headers, json={**CONTEXT, "country_code": "IN"})
    assert good.status_code == 200, good.text
    assert [item["country_name"] for item in good.json()] == ["France", "United States", "Russia"]

    # India isn't seated on the Security Council in 2026, and a country outside the seat list is refused.
    picks[0]["country_code"] = "IN"
    monkeypatch.setattr(ai, "generate", fake_generate({"mun.opponents": {"opponents": picks}}))
    bad = await client.post("/v1/mun/opponents", headers=headers, json={**CONTEXT, "country_code": "IN"})
    assert bad.status_code == 503


async def test_feedback_overall_score_is_computed_by_the_server(
    student: tuple[httpx.AsyncClient, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = student

    def category(category_id: str, score: int) -> dict[str, Any]:
        return {
            "id": category_id,
            "score": score,
            "strengths": ["Clear"],
            "improvements": ["Slow down"],
            "evidence": ["“funded”"],
        }

    answer = {
        "categories": [
            category("research", 70),
            category("speechClarity", 80),
            category("poiPerformance", 150),
            category("foreignPolicy", 60),
        ],
        "supported_claims": ["Mandates must be funded"],
        "questionable_claims": ["Underfunded missions fail civilians"],
        "references": ["UN Charter (1945)"],
        "improvement_actions": ["Add one statistic"],
    }
    monkeypatch.setattr(ai, "generate", fake_generate({"mun.feedback": answer}))
    response = await client.post(
        "/v1/mun/feedback",
        headers=headers,
        json={
            **CONTEXT,
            "text": SPEECH,
            "duration_ms": 60000,
            "points": [
                {"country": "France", "question": "How?", "answer": "Through assessed contributions."}
            ],
        },
    )
    body = response.json()
    assert response.status_code == 200, body
    assert [item["id"] for item in body["categories"]] == [
        "speechClarity",
        "foreignPolicy",
        "research",
        "poiPerformance",
    ]
    assert body["categories"][3]["score"] == 100  # clamped
    assert body["overall_score"] == round((80 + 60 + 70 + 100) / 4)
    assert body["categories"][0]["label"] == "Speech Clarity"


async def test_pois_must_match_the_delegations(
    student: tuple[httpx.AsyncClient, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = student
    analysis = {
        "arguments": ["Fund mandates"],
        "proposals": [],
        "claims": [{"text": "x", "supported": False}],
        "mentioned_countries": [],
    }
    opponents = [
        {"code": "fr", "name": "France", "stance": "ally", "position": "Supports"},
        {"code": "us", "name": "United States", "stance": "different", "position": "Costs"},
    ]
    points = {
        "points": [
            {"country_code": "us", "based_on": "funded", "question": "Thank you, Chair. Who pays for it?"},
            {
                "country_code": "fr",
                "based_on": "realistic",
                "question": "Thank you, Chair. What does realistic mean?",
            },
        ]
    }
    monkeypatch.setattr(ai, "generate", fake_generate({"mun.pois": points}))
    body = {**CONTEXT, "text": SPEECH, "analysis": analysis, "opponents": opponents}
    ordered = await client.post("/v1/mun/pois", headers=headers, json=body)
    assert [item["country_code"] for item in ordered.json()] == ["fr", "us"]

    points["points"][0]["country_code"] = "cn"
    monkeypatch.setattr(ai, "generate", fake_generate({"mun.pois": points}))
    assert (await client.post("/v1/mun/pois", headers=headers, json=body)).status_code == 503


async def test_transcription_checks_the_upload(
    student: tuple[httpx.AsyncClient, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = student
    empty = await client.post(
        "/v1/mun/transcribe", headers=headers, files={"file": ("a.m4a", b"x", "audio/m4a")}
    )
    assert empty.status_code == 422 and empty.json()["detail"]["code"] == "empty_audio"

    seen: dict[str, Any] = {}

    async def fake(audio: bytes, content_type: str, **kwargs: Any) -> tuple[str, float]:
        seen.update(content_type=content_type, size=len(audio), **kwargs)
        return "Honourable chair, India believes.", 12.5

    monkeypatch.setattr(deepgram, "transcribe", fake)
    ok = await client.post(
        "/v1/mun/transcribe",
        headers=headers,
        data={"purpose": "poi_answer"},
        files={"file": ("answer.m4a", b"\0" * 4000, "audio/m4a; codecs=aac")},
    )
    assert ok.json() == {"text": "Honourable chair, India believes.", "seconds": 12.5}
    assert (
        seen["content_type"] == "audio/m4a" and seen["feature"] == "mun.poi_answer" and seen["size"] == 4000
    )


async def test_sessions_are_saved_once_per_run_and_private(
    student: tuple[httpx.AsyncClient, dict[str, str]],
) -> None:
    client, headers = student
    run = str(uuid.uuid4())
    body = {
        "run_id": run,
        **CONTEXT,
        "overall_score": 74,
        "scores": {"research": 70, "speechClarity": 140},
        "speech_seconds": 80,
        "pois_answered": 3,
        "ai_feedback": True,
        "report": {"overallScore": 74},
    }
    assert (await client.put(f"/v1/mun/sessions/{run}", headers=headers, json=body)).status_code == 204
    body["overall_score"] = 76
    assert (await client.put(f"/v1/mun/sessions/{run}", headers=headers, json=body)).status_code == 204
    sessions = (await client.get("/v1/mun/sessions", headers=headers)).json()
    assert len(sessions) == 1 and sessions[0]["overall_score"] == 76
    assert sessions[0]["scores"]["speechClarity"] == 100

    other = auth(await signed_up_and_verified(client, "kabir@school.in"))
    assert (await client.get("/v1/mun/sessions", headers=other)).json() == []
    stolen = await client.put(f"/v1/mun/sessions/{run}", headers=other, json=body)
    assert stolen.status_code == 409


async def test_failed_ai_calls_are_recorded_for_the_dashboard(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ai import gemini

    monkeypatch.setattr(
        gemini, "get_settings", lambda: type("S", (), {"gemini_api_key": "x", "gemini_model": "m"})()
    )

    class Boom(httpx.AsyncClient):
        async def post(self, *args: Any, **kwargs: Any) -> httpx.Response:  # type: ignore[override]
            raise httpx.ConnectError("down")

    monkeypatch.setattr(gemini.httpx, "AsyncClient", Boom)
    with pytest.raises(ai.AiUnavailable):
        await ai.analyse_speech(committee="UNSC", agenda="Peace", country="India", text=SPEECH, user_id=None)
    async with SessionLocal() as session:
        failures = await session.scalar(
            select(func.count()).where(AiCall.ok.is_(False), AiCall.feature == "mun.speech")
        )
    assert failures == 2  # one per attempt


async def test_dashboard_insights_add_up(
    student: tuple[httpx.AsyncClient, dict[str, str]], admin_client: Any
) -> None:
    client, headers = student
    for score, ai_feedback in ((60, True), (80, False)):
        run = str(uuid.uuid4())
        await client.put(
            f"/v1/mun/sessions/{run}",
            headers=headers,
            json={"run_id": run, **CONTEXT, "overall_score": score, "ai_feedback": ai_feedback},
        )
    from app.ai.usage import record

    await record(
        provider="gemini", feature="mun.feedback", model="m", input_tokens=1_000_000, output_tokens=100_000
    )
    await record(provider="deepgram", feature="mun.speech", model="nova-3", audio_seconds=600, ok=False)

    analyst = await admin_client("analyst")
    data = (await analyst.get("/admin/api/insights")).json()
    practice, usage = data["practice"], data["ai"]
    assert (practice["sessions_total"], practice["average_score"], practice["ai_feedback_share"]) == (
        2,
        70,
        50,
    )
    assert practice["top_committees"][0] == {
        "committee": "UN Security Council",
        "sessions": 2,
        "average_score": 70,
    }
    assert (usage["calls_today"], usage["failures_today"]) == (2, 1)
    # 1M input at $0.30 + 0.1M output at $2.50 + 10 min at $0.0077
    assert usage["cost_today_usd"] == round(0.30 + 0.25 + 0.077, 2)


# ---------- The comfort language ----------


def recording_generate(answers: dict[str, dict[str, Any]], seen: dict[str, dict[str, Any]]) -> Any:
    async def generate(*, feature: str, shape: type[BaseModel], **kwargs: Any) -> BaseModel:
        seen[feature] = kwargs
        if feature not in answers:
            raise ai.AiUnavailable("no fake answer")
        return shape.model_validate(answers[feature])

    return generate


FEEDBACK_ANSWER = {
    "categories": [
        {"id": category, "score": 70, "strengths": ["Nalla"], "improvements": ["Konjam slow"], "evidence": []}
        for category in ("speechClarity", "foreignPolicy", "research", "poiPerformance")
    ],
    "supported_claims": [],
    "questionable_claims": [],
    "references": [],
    "improvement_actions": ["Oru statistic add pannu"],
}


async def test_feedback_follows_the_comfort_language(
    student: tuple[httpx.AsyncClient, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = student
    seen: dict[str, dict[str, Any]] = {}
    monkeypatch.setattr(ai, "generate", recording_generate({"mun.feedback": FEEDBACK_ANSWER}, seen))
    body = {**CONTEXT, "text": SPEECH, "duration_ms": 60000}

    english = await client.post("/v1/mun/feedback", headers=headers, json=body)
    assert english.json()["language"] == "en" and seen["mun.feedback"]["language"] == "en"

    await set_language(client, headers, "ta_latn")
    tamil = await client.post("/v1/mun/feedback", headers=headers, json=body)
    assert tamil.status_code == 200, tamil.text
    assert tamil.json()["language"] == "ta_latn"
    assert seen["mun.feedback"]["language"] == "ta_latn"
    assert comfort.rule("ta_latn") in seen["mun.feedback"]["system"]


async def test_content_prompts_stay_english(
    student: tuple[httpx.AsyncClient, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = student
    await set_language(client, headers, "hi")
    seen: dict[str, dict[str, Any]] = {}
    monkeypatch.setattr(ai, "generate", recording_generate({}, seen))
    await client.post("/v1/mun/speech-analysis", headers=headers, json={**CONTEXT, "text": SPEECH})
    assert seen["mun.speech"]["language"] == "en" and "Hindi" not in seen["mun.speech"]["system"]


EXPLAIN_BODY = {
    "items": [
        {"id": "gsl.weakest", "text": "Diction (52): cut “I think”."},
        {"id": "action.0", "text": "Add one statistic."},
        {"id": "blank", "text": "  "},
    ],
    "phrases": [
        {
            "id": "p0",
            "quote": "I think we need peace.",
            "rewrite": "The delegation of India firmly believes that",
        },
        {"id": "p1", "quote": "Hello everyone.", "rewrite": "Honourable Chair, fellow delegates,"},
    ],
}


async def test_explain_returns_english_unchanged_without_calling_the_ai(
    student: tuple[httpx.AsyncClient, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = student
    seen: dict[str, dict[str, Any]] = {}
    monkeypatch.setattr(ai, "generate", recording_generate({}, seen))
    response = await client.post("/v1/mun/explain", headers=headers, json=EXPLAIN_BODY)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "items": EXPLAIN_BODY["items"],
        "phrases": [{"id": "p0", "meaning": None}, {"id": "p1", "meaning": None}],
        "language": "en",
    }
    assert seen == {}


async def test_explain_puts_the_report_in_the_comfort_language(
    student: tuple[httpx.AsyncClient, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = student
    await set_language(client, headers, "ta")
    seen: dict[str, dict[str, Any]] = {}
    answer = {
        # Out of order, one item left out, one id the app never sent, a phrase meaning missing.
        "items": [
            {"id": "gsl.weakest", "text": "உச்சரிப்பு (52): “I think” என்பதை விடுங்கள்."},
            {"id": "made.up", "text": "ignored"},
        ],
        "phrases": [{"id": "p1", "meaning": "மதிப்பிற்குரிய தலைவர் அவர்களே"}],
    }
    monkeypatch.setattr(ai, "generate", recording_generate({"mun.explain": answer}, seen))
    response = await client.post("/v1/mun/explain", headers=headers, json=EXPLAIN_BODY)
    body = response.json()
    assert response.status_code == 200, body
    assert body["language"] == "ta"
    assert body["items"] == [
        {"id": "gsl.weakest", "text": "உச்சரிப்பு (52): “I think” என்பதை விடுங்கள்."},
        {"id": "action.0", "text": "Add one statistic."},
        {"id": "blank", "text": "  "},
    ]
    assert body["phrases"] == [
        {"id": "p0", "meaning": None},
        {"id": "p1", "meaning": "மதிப்பிற்குரிய தலைவர் அவர்களே"},
    ]
    call = seen["mun.explain"]
    assert call["language"] == "ta" and comfort.rule("ta") in call["system"]
    assert 'id="blank"' not in call["prompt"] and "Honourable Chair, fellow delegates," in call["prompt"]

    # Not one of today's free scored attempts or Chanakya messages.
    usage = (await client.get("/v1/chanakya/usage", headers=headers)).json()
    assert usage["scored_attempts"]["used"] == 0 and usage["chat_messages"]["used"] == 0


async def test_explain_fails_cleanly_and_checks_its_input(
    student: tuple[httpx.AsyncClient, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = student
    await set_language(client, headers, "hi_latn")
    monkeypatch.setattr(ai, "generate", recording_generate({}, {}))
    failed = await client.post("/v1/mun/explain", headers=headers, json=EXPLAIN_BODY)
    assert failed.status_code == 503 and failed.json()["detail"]["code"] == "ai_unavailable"

    # An answer with none of the items is no answer.
    empty = {"items": [{"id": "nope", "text": "x"}], "phrases": []}
    monkeypatch.setattr(ai, "generate", recording_generate({"mun.explain": empty}, {}))
    assert (await client.post("/v1/mun/explain", headers=headers, json=EXPLAIN_BODY)).status_code == 503

    too_many = {"items": [{"id": f"i{index}", "text": "Tip"} for index in range(41)]}
    assert (await client.post("/v1/mun/explain", headers=headers, json=too_many)).status_code == 422
    too_long = {"items": [{"id": "i0", "text": "x" * 601}]}
    assert (await client.post("/v1/mun/explain", headers=headers, json=too_long)).status_code == 422
    twice = {"items": [{"id": "i0", "text": "a"}, {"id": "i0", "text": "b"}]}
    assert (await client.post("/v1/mun/explain", headers=headers, json=twice)).status_code == 422
    phrases = {"phrases": [{"id": f"p{index}", "quote": "a", "rewrite": "b"} for index in range(21)]}
    assert (await client.post("/v1/mun/explain", headers=headers, json=phrases)).status_code == 422
