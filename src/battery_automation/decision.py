"""Pure decision logic: should the home battery be AC-charging right now?

Kept free of IO so it can be unit-tested without mocks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")

# We trust the EV-is-drawing-power signal only between 09:00 and 16:00 local time —
# the plausible window for IOG daytime dispatches. Outside that, a charging EV is
# more likely a manual boost charge at peak rate, not an IOG-controlled slot.
_HYPERVOLT_TRUST_START = time(9, 0)
_HYPERVOLT_TRUST_END = time(16, 0)


@dataclass(frozen=True)
class Inputs:
    now: datetime  # tz-aware
    cheap_window_start: time
    cheap_window_end: time
    in_planned_dispatch: bool
    hypervolt_charging: bool | None  # None = unknown / stale


@dataclass(frozen=True)
class Decision:
    cheap_now: bool
    reason: str


def in_window(now_local: time, start: time, end: time) -> bool:
    """Whether `now_local` falls within [start, end). Handles wrap-around midnight."""
    if start <= end:
        return start <= now_local < end
    return now_local >= start or now_local < end


def decide(inp: Inputs) -> Decision:
    now_local = inp.now.astimezone(LONDON).time()
    if in_window(now_local, inp.cheap_window_start, inp.cheap_window_end):
        return Decision(True, "standard cheap window")
    if inp.in_planned_dispatch:
        return Decision(True, "octopus planned dispatch")
    if inp.hypervolt_charging and in_window(
        now_local, _HYPERVOLT_TRUST_START, _HYPERVOLT_TRUST_END
    ):
        return Decision(True, "hypervolt charging in plausible-dispatch window")
    return Decision(False, "no cheap signal")
