"""The Skill Ladder must match the web's apps/skill_ladder.py exactly (values worked out by hand from it)."""

from app.modules.me.ladder import build_debate_skill_ladder, build_skill_ladder_summary, determine_skill_level


def test_new_student_is_a_beginner() -> None:
    ladder = build_debate_skill_ladder(None)
    assert (ladder.level, ladder.lifetime_score, ladder.next_level, ladder.points_to_next_level) == (
        "Beginner",
        0,
        "Rising",
        150,
    )


def test_matches_the_web_for_a_typical_row() -> None:
    # logical 70, emotional 60, rebuttal 50, total 180 → clarity 70, confidence 60, content 60, consistency 60
    ladder = build_debate_skill_ladder(
        {
            "avg_logical_score": 70,
            "avg_emotional_score": 60,
            "avg_rebuttal_score": 50,
            "avg_total_score": 180,
            "total_debates": 5,
            "current_win_streak": 2,
        }
    )
    assert ladder.dimension_percentages == {"clarity": 70, "confidence": 60, "content": 60, "consistency": 60}
    assert ladder.score == 62
    # 5 * 35 + 62 // 5 + min(2 * 2, 10) = 175 + 12 + 4
    assert ladder.lifetime_score == 191
    assert (ladder.level, ladder.next_level) == ("Rising", "Emerging")
    assert ladder.progress_percent == round((191 - 150) / 150 * 100)
    assert ladder.points_to_next_level == 300 - 191


def test_quarter_scale_scores_are_read_as_out_of_25() -> None:
    assert build_skill_ladder_summary(clarity=20, confidence=20, content=20, consistency=20).score == 80


def test_levels_and_streak_cap() -> None:
    assert determine_skill_level(750)[0] == "Champion"
    assert determine_skill_level(749)[0] == "Confident"
    ladder = build_skill_ladder_summary(clarity=90, confidence=90, content=90, consistency=90, streak=9)
    assert ladder.streak_bonus == 10
    assert ladder.encouragement.startswith("Excellent")
