"""The Skill Ladder, ported line for line from the web's apps/skill_ladder.py (MyDebate).

The website shows students this ladder (score, level, progress to the next level) from their
`user_debate_stats` row, so the app computes it the same way from the same row: a student sees the same
level on both. Keep this file identical in behaviour to the web's `build_debate_skill_ladder`.
"""

from typing import Any

from pydantic import BaseModel

LEVELS: list[tuple[str, int, int | None]] = [
    ("Beginner", 0, 149),
    ("Rising", 150, 299),
    ("Emerging", 300, 499),
    ("Confident", 500, 749),
    ("Champion", 750, None),
]


class Ladder(BaseModel):
    score: int
    dimensions: dict[str, float]
    dimension_percentages: dict[str, int]
    streak_bonus: int
    completed_count: int
    lifetime_score: int
    level: str
    next_level: str | None
    progress_percent: int
    points_to_next_level: int
    encouragement: str


def _safe_number(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: Any, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, _safe_number(value)))


def _to_percent(value: Any) -> int:
    raw = _safe_number(value)
    if raw <= 25:
        return round(_clamp(raw, 0, 25) * 4)
    return round(_clamp(raw, 0, 100))


def _to_quarter_score(percent_value: Any) -> float:
    return round(_to_percent(percent_value) / 4, 1)


def determine_skill_level(lifetime_score: int) -> tuple[str, int, int | None, str | None]:
    lifetime_score = max(0, int(lifetime_score))
    for index, (level, start, end) in enumerate(LEVELS):
        if end is None or start <= lifetime_score <= end:
            following = LEVELS[index + 1][0] if index + 1 < len(LEVELS) else None
            return level, start, end, following
    return "Beginner", 0, 149, "Rising"


def build_skill_ladder_summary(
    *,
    clarity: Any,
    confidence: Any,
    content: Any,
    consistency: Any,
    completed_count: int = 0,
    streak: int = 0,
    previous_lifetime_score: int | None = None,
) -> Ladder:
    percentages = {
        "clarity": _to_percent(clarity),
        "confidence": _to_percent(confidence),
        "content": _to_percent(content),
        "consistency": _to_percent(consistency),
    }
    dimensions = {key: _to_quarter_score(value) for key, value in percentages.items()}
    score = int(sum(percentages.values()) / 4)
    completed_count = max(0, int(completed_count or 0))
    streak = max(0, int(streak or 0))
    streak_bonus = min(streak * 2, 10)

    history_baseline = (completed_count * 35) + (score // 5) + streak_bonus
    if previous_lifetime_score is None:
        lifetime_score = history_baseline
    else:
        previous = max(0, int(previous_lifetime_score))
        growth = max(8, round(score / 10) + streak_bonus)
        lifetime_score = max(previous, history_baseline, previous + growth)

    level, start, end, following = determine_skill_level(lifetime_score)
    if end is None:
        progress_percent, points_to_next = 100, 0
    else:
        span = max(1, end - start + 1)
        progress_percent = int(max(0, min(100, round(((lifetime_score - start) / span) * 100))))
        points_to_next = max(0, end + 1 - lifetime_score)

    if score >= 85:
        encouragement = "Excellent momentum — keep stacking strong sessions."
    elif score >= 70:
        encouragement = "Solid growth — a few more consistent reps will move you up."
    else:
        encouragement = "Every attempt adds to your ladder. Keep practicing."

    return Ladder(
        score=score,
        dimensions=dimensions,
        dimension_percentages=percentages,
        streak_bonus=streak_bonus,
        completed_count=completed_count,
        lifetime_score=lifetime_score,
        level=level,
        next_level=following,
        progress_percent=progress_percent,
        points_to_next_level=points_to_next,
        encouragement=encouragement,
    )


def build_debate_skill_ladder(stats: dict[str, Any] | None) -> Ladder:
    """`stats` is the student's user_debate_stats row (or None before their first debate)."""
    stats = stats or {}
    logical = _safe_number(stats.get("avg_logical_score"))
    emotional = _safe_number(stats.get("avg_emotional_score"))
    rebuttal = _safe_number(stats.get("avg_rebuttal_score"))
    total = _safe_number(stats.get("avg_total_score"))
    return build_skill_ladder_summary(
        clarity=logical,
        confidence=emotional,
        content=(logical + rebuttal) / 2,
        consistency=total / 3 if total else (logical + emotional + rebuttal) / 3,
        completed_count=int(stats.get("total_debates") or 0),
        streak=int(stats.get("current_win_streak") or 0),
    )
