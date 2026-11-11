import pytest

from app.ai import comfort


def test_english_explains_in_plain_english_with_no_meanings() -> None:
    rule = comfort.rule("en")
    assert "plain, simple English" in rule
    assert "Leave every 'meaning' field empty" in rule
    assert "Tamil" not in rule and "Hindi" not in rule


@pytest.mark.parametrize(
    ("language", "spoken", "script"),
    [
        ("ta", "Tamil", "Tamil script"),
        ("ta_latn", "Tamil", "English letters"),
        ("hi", "Hindi", "Devanagari script"),
        ("hi_latn", "Hindi", "English letters"),
    ],
)
def test_comfort_languages_explain_in_their_language_but_practice_stays_english(
    language: str, spoken: str, script: str
) -> None:
    rule = comfort.rule(language)
    assert f"most comfortable in {spoken}" in rule
    assert script in rule
    # What the student says, and every better version, stays English with the meaning in brackets.
    assert "every improved or better version stays in English" in rule
    assert f"followed by its {spoken} meaning in brackets" in rule
    assert f"put the {spoken} meaning there" in rule
    assert f"Never ask the student to speak in {spoken}" in rule


def test_romanised_variants_never_use_the_native_script() -> None:
    assert "never in Tamil script" in comfort.rule("ta_latn")
    assert "never in Devanagari" in comfort.rule("hi_latn")
    assert "never in" not in comfort.rule("ta") and "never in" not in comfort.rule("hi")


def test_unknown_languages_fall_back_to_english() -> None:
    assert comfort.normalise("fr") == "en"
    assert comfort.normalise(None) == "en"
    assert comfort.normalise("hi_latn") == "hi_latn"
    assert comfort.rule("xx") == comfort.rule("en")


def test_meanings_are_dropped_for_english_and_blanks() -> None:
    assert comfort.meaning("en", "anything") is None
    assert comfort.meaning("ta", "  ") is None
    assert comfort.meaning("ta", " நான் தயார் ") == "நான் தயார்"


def test_log_label_names_each_language_once() -> None:
    assert comfort.log_label("ta") == "ta"
    assert comfort.log_label("ta", "ta") == "ta"
    assert comfort.log_label("ta_latn", "hi_latn") == "ta_latn+hi_latn"
    assert len(comfort.log_label("ta_latn", "hi_latn")) <= 16  # fits ai_calls.language
