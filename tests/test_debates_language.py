"""MyDebate in the comfort language: the 60-second prep screen and the vs-AI analysis."""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from app.main import app
from app.modules.debates import ai
from tests.test_debates_api import TOPIC
from tests.test_student_auth import auth, signed_up_and_verified

Student = tuple[httpx.AsyncClient, dict[str, str]]


@pytest.fixture
async def student() -> AsyncIterator[Student]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, auth(await signed_up_and_verified(client))


async def set_language(client: httpx.AsyncClient, headers: dict[str, str], language: str) -> None:
    response = await client.patch("/v1/me", headers=headers, json={"comfort_language": language})
    assert response.json()["comfort_language"] == language


def recorder(answers: dict[str, dict[str, Any]], seen: dict[str, dict[str, Any]]) -> Any:
    async def generate(*, feature: str, shape: type[BaseModel], **kwargs: Any) -> BaseModel:
        seen[feature] = kwargs | {"shape": shape.__name__}
        return shape.model_validate(answers[feature])

    return generate


HINTS_EN = [
    {"point": "Sleep matters", "why": "Tired students learn less."},
    {"point": "Family time", "why": "Evenings build other skills."},
    {"point": "Play", "why": "Rest helps memory."},
]


async def test_prep_for_an_english_student(student: Student, monkeypatch: pytest.MonkeyPatch) -> None:
    client, headers = student
    seen: dict[str, dict[str, Any]] = {}
    answer = {"explanation": "The motion asks if homework should stop.", "hints": [*HINTS_EN, HINTS_EN[0]]}
    monkeypatch.setattr(ai, "generate", recorder({"debate.prep": answer}, seen))
    response = await client.post(
        "/v1/debates/prep", headers=headers, json={"motion": TOPIC["title"], "side": "for"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["explanation"] == body["explanation_en"] == "The motion asks if homework should stop."
    assert body["hints"] == body["hints_en"] == HINTS_EN
    assert body["language"] == "en"
    assert seen["debate.prep"]["shape"] == "PrepEnglishShape" and seen["debate.prep"]["language"] == "en"
    assert "argues FOR the motion" in seen["debate.prep"]["prompt"]


async def test_prep_in_tamil_with_an_english_toggle(
    student: Student, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = student
    await set_language(client, headers, "ta")
    seen: dict[str, dict[str, Any]] = {}
    hints_ta = [{"point": "தூக்கம் முக்கியம்", "why": "களைப்பான மாணவர்கள் குறைவாக கற்கிறார்கள்."}] * 3
    answer = {
        "explanation": "இந்த தீர்மானம் வீட்டுப்பாடம் பற்றியது.",
        "hints": hints_ta,
        "explanation_en": "The motion is about homework.",
        "hints_en": HINTS_EN,
    }
    monkeypatch.setattr(ai, "generate", recorder({"debate.prep": answer}, seen))
    response = await client.post(
        "/v1/debates/prep", headers=headers, json={"motion": TOPIC["title"], "side": "against"}
    )
    body = response.json()
    assert body["explanation"].startswith("இந்த") and body["hints"] == hints_ta
    assert body["explanation_en"] == "The motion is about homework." and body["hints_en"] == HINTS_EN
    assert body["language"] == "ta"
    assert "most comfortable in Tamil" in seen["debate.prep"]["system"]
    assert seen["debate.prep"]["shape"] == "PrepShape" and seen["debate.prep"]["language"] == "ta"


async def test_prep_with_too_few_hints_or_no_ai_falls_back(
    student: Student,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, headers = student
    body = {"motion": TOPIC["title"], "side": "for"}
    off = await client.post("/v1/debates/prep", headers=headers, json=body)  # no Gemini key in tests
    assert off.status_code == 503 and off.json()["detail"]["code"] == "ai_unavailable"
    answer = {"explanation": "About homework.", "hints": HINTS_EN[:2]}
    monkeypatch.setattr(ai, "generate", recorder({"debate.prep": answer}, {}))
    short = await client.post("/v1/debates/prep", headers=headers, json=body)
    assert short.status_code == 503 and short.json()["detail"]["code"] == "ai_unavailable"


async def test_analysis_explains_in_the_comfort_language(
    student: Student,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, headers = student
    await set_language(client, headers, "hi")
    seen: dict[str, dict[str, Any]] = {}
    answer = {
        "categories": [
            {"id": name, "score": 70, "strengths": ["a"], "improvements": ["b"], "why": "c"}
            for name in ("argument", "rebuttal", "evidence", "structure", "delivery")
        ],
        "opponent_score": 60,
        "summary": "अच्छी बहस।",
        "key_moments": [
            {
                "turn_number": 1,
                "kind": "best",
                "quote": "stress",
                "note": "साफ़",
                "better": "x",
                "meaning": "y",
            },
            {
                "turn_number": 1,
                "kind": "weakest",
                "quote": "Homework causes stress.",
                "note": "कारण नहीं दिया।",
                "better": "Homework causes stress because it cuts into sleep.",
                "meaning": "होमवर्क तनाव देता है क्योंकि नींद कम होती है।",
            },
        ],
        "missed_arguments": [],
        "next_steps": ["एक उदाहरण दें"],
    }
    monkeypatch.setattr(ai, "generate", recorder({"debate.analysis": answer}, seen))
    response = await client.post(
        "/v1/debates/ai/analysis",
        headers=headers,
        json={
            "topic": TOPIC,
            "student_side": "for",
            "difficulty": "easy",
            "turns": [{"speaker": "user", "phase": "opening", "text": "Homework causes stress."}],
        },
    )
    body = response.json()
    assert body["language"] == "hi" and body["summary"] == "अच्छी बहस।"
    assert body["seal"] and body["overall_score"] == 70  # unchanged fields still there
    best, weakest = body["key_moments"]
    assert best["better"] is None and best["meaning"] is None  # only the weakest moment gets a better line
    assert weakest["better"] == "Homework causes stress because it cuts into sleep."
    assert weakest["meaning"] == "होमवर्क तनाव देता है क्योंकि नींद कम होती है।"
    assert "most comfortable in Hindi" in seen["debate.analysis"]["system"]
    assert seen["debate.analysis"]["language"] == "hi"
