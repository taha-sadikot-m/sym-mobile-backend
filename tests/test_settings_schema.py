import pytest
from pydantic import ValidationError

from app.content.settings import SETTINGS, LiveFormats, PoiSettings, SpeechSettings


def test_defaults_match_the_app_today() -> None:
    assert SpeechSettings().model_dump() == {
        "target_seconds": 90,
        "warn_seconds_left": 15,
        "hard_stop_seconds": 120,
    }
    pois = PoiSettings()
    assert (pois.count, pois.answer_seconds, pois.follow_up_seconds) == (3, 45, 15)
    totals = {item.id: sum(turn.seconds for turn in item.turns) // 60 for item in LiveFormats().formats}
    assert totals == {"quick": 12, "standard": 20, "rapid": 10}


def test_every_group_has_valid_defaults() -> None:
    for model in SETTINGS.values():
        model.model_validate(model().model_dump())


@pytest.mark.parametrize(
    ("model", "value", "message"),
    [
        (SpeechSettings, {"target_seconds": 90, "hard_stop_seconds": 60}, "hard stop"),
        (PoiSettings, {"answer_seconds": 20, "conclude_warning_seconds": 25}, "conclude warning"),
        (PoiSettings, {"answer_seconds": 5}, "greater than or equal"),
        (SpeechSettings, {"surprise": 1}, "Extra inputs"),
    ],
)
def test_impossible_values_are_rejected(model: type, value: dict[str, int], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        model.model_validate(value)


def test_formats_must_give_both_sides_equal_time() -> None:
    formats = LiveFormats().model_dump()
    formats["formats"][0]["turns"][0]["seconds"] = 150
    with pytest.raises(ValidationError, match="same total speaking time"):
        LiveFormats.model_validate(formats)


def test_at_least_one_format_stays_enabled() -> None:
    formats = LiveFormats().model_dump()
    for item in formats["formats"]:
        item["enabled"] = False
    with pytest.raises(ValidationError, match="At least one format"):
        LiveFormats.model_validate(formats)
