from app.modules.live.clock import CUE_MS, Event, Slot, compute, done_at_ms

SLOTS = [
    Slot(0, "opening", "for", 60_000),
    Slot(1, "opening", "against", 60_000),
    Slot(2, "closing", "for", 30_000),
]
GRACE, TIMEOUT = 5_000, 30_000


def at(now: int, events: list[Event] | None = None):  # type: ignore[no-untyped-def]
    return compute(
        slots=SLOTS, started_at_ms=0, events=events or [], now_ms=now, grace_ms=GRACE, timeout_ms=TIMEOUT
    )


def test_waiting_before_the_start() -> None:
    clock = compute(
        slots=SLOTS, started_at_ms=10_000, events=[], now_ms=5_000, grace_ms=GRACE, timeout_ms=TIMEOUT
    )
    assert clock.stage == "waiting"


def test_cue_then_turn_then_grace_then_next_slot() -> None:
    assert at(1_000).stage == "cue"
    live = at(CUE_MS + 1)
    assert (live.stage, live.slot_index, live.ends_at_ms) == ("live", 0, CUE_MS + 60_000 + GRACE)
    assert at(CUE_MS + 60_000 + 4_999).slot_index == 0  # still in the grace period
    after = at(CUE_MS + 60_000 + GRACE)
    assert (after.stage, after.slot_index) == ("cue", 1)


def test_handing_over_early_starts_the_next_cue() -> None:
    events = [Event("handover", 0, 20_000)]
    clock = at(21_000, events)
    assert (clock.stage, clock.slot_index, clock.cue_ends_at_ms) == ("cue", 1, 20_000 + CUE_MS)


def test_a_handover_for_another_slot_is_ignored() -> None:
    assert at(21_000, [Event("handover", 2, 20_000)]).slot_index == 0


def test_time_out_lengthens_that_cue_only() -> None:
    events = [Event("handover", 0, 20_000), Event("timeout", 1, 21_000)]
    assert at(20_000 + CUE_MS + 1_000, events).stage == "cue"
    clock = at(20_000 + CUE_MS + TIMEOUT + 1, events)
    assert (clock.stage, clock.slot_index) == ("live", 1)


def test_leaving_ends_the_debate() -> None:
    events = [Event("leave", None, 30_000)]
    assert at(29_000, events).stage == "live"
    assert at(30_001, events).stage == "done"
    assert (
        done_at_ms(slots=SLOTS, started_at_ms=0, events=events, grace_ms=GRACE, timeout_ms=TIMEOUT) == 30_000
    )


def test_done_after_the_last_slot() -> None:
    total = 3 * CUE_MS + 60_000 + 60_000 + 30_000 + 3 * GRACE
    assert at(total - 1).stage == "live"
    assert at(total).stage == "done"
    assert done_at_ms(slots=SLOTS, started_at_ms=0, events=[], grace_ms=GRACE, timeout_ms=TIMEOUT) == total
