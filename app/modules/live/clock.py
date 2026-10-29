"""The live room's clock: one pure function both the server and the app run on the same inputs.

The room's state is never pushed around; it is *computed* from the start time, the format's schedule and
the append-only event log. Two phones (and the server) therefore always agree on whose turn it is, even
after a reconnect. The app has a line-for-line TypeScript twin (src/features/live/roomClock.ts).

Each slot is a 3-second cue (plus a 30-second time-out if the speaker took one), then the turn itself.
A turn ends early when its owner hands over, otherwise at its length plus the grace period.
"""

from dataclasses import dataclass
from typing import Literal

CUE_MS = 3000


@dataclass(frozen=True)
class Slot:
    index: int
    phase: str
    side: Literal["for", "against"]
    duration_ms: int


@dataclass(frozen=True)
class Event:
    kind: Literal["handover", "timeout", "leave"]
    slot: int | None
    at_ms: int


@dataclass(frozen=True)
class Clock:
    stage: Literal["waiting", "cue", "live", "done"]
    slot_index: int  # the current slot (or the number of slots when done)
    cue_ends_at_ms: int | None
    live_started_at_ms: int | None
    ends_at_ms: int | None  # when the current turn ends at the latest (length + grace)
    timeout_slots: frozenset[int]


def compute(
    *,
    slots: list[Slot],
    started_at_ms: int | None,
    events: list[Event],
    now_ms: int,
    grace_ms: int,
    timeout_ms: int,
) -> Clock:
    timeouts = frozenset(event.slot for event in events if event.kind == "timeout" and event.slot is not None)
    if started_at_ms is None or now_ms < started_at_ms:
        return Clock("waiting", 0, started_at_ms, None, None, timeouts)
    leave_at = min((event.at_ms for event in events if event.kind == "leave"), default=None)
    t = started_at_ms
    for slot in slots:
        cue_end = t + CUE_MS + (timeout_ms if slot.index in timeouts else 0)
        natural_end = cue_end + slot.duration_ms + grace_ms
        handovers = [
            event.at_ms
            for event in events
            if event.kind == "handover" and event.slot == slot.index and t <= event.at_ms <= natural_end
        ]
        end = min([natural_end, *handovers])
        if leave_at is not None and leave_at < end:
            if now_ms < leave_at:
                stage: Literal["cue", "live"] = "cue" if now_ms < cue_end else "live"
                return Clock(
                    stage, slot.index, cue_end, cue_end if stage == "live" else None, natural_end, timeouts
                )
            return Clock("done", len(slots), None, None, None, timeouts)
        if now_ms < cue_end:
            return Clock("cue", slot.index, cue_end, None, natural_end, timeouts)
        if now_ms < end:
            return Clock("live", slot.index, cue_end, cue_end, natural_end, timeouts)
        t = end
    return Clock("done", len(slots), None, None, None, timeouts)


def done_at_ms(
    *, slots: list[Slot], started_at_ms: int, events: list[Event], grace_ms: int, timeout_ms: int
) -> int:
    """When the debate finished (or will finish if nobody hands over early)."""
    timeouts = {event.slot for event in events if event.kind == "timeout"}
    leave_at = min((event.at_ms for event in events if event.kind == "leave"), default=None)
    t = started_at_ms
    for slot in slots:
        cue_end = t + CUE_MS + (timeout_ms if slot.index in timeouts else 0)
        natural_end = cue_end + slot.duration_ms + grace_ms
        handovers = [
            event.at_ms
            for event in events
            if event.kind == "handover" and event.slot == slot.index and t <= event.at_ms <= natural_end
        ]
        end = min([natural_end, *handovers])
        if leave_at is not None and leave_at < end:
            return leave_at
        t = end
    return t
