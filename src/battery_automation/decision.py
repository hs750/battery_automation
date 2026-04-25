"""Pure decision logic: should the home battery be AC-charging right now?

The standard overnight cheap window is owned by the inverter's permanent slot
(slot 1), so this function ignores it entirely — overnight charging proceeds
unconditionally via that slot whether or not this script is alive. What's left
for the live decision loop is the *dynamic* slot, which only fires for
out-of-window IOG dispatches that the EV is actually drawing.

Kept free of IO so it can be unit-tested without mocks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")


@dataclass(frozen=True)
class Inputs:
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
    """Fire the dynamic AC-charge slot only when an IOG planned dispatch is active
    AND the EV is actually drawing power. The standard cheap window is owned by
    the inverter's permanent slot 1 and is intentionally outside the scope of
    this function.
    """
    if not inp.in_planned_dispatch:
        return Decision(False, "no iog dispatch")
    if not inp.hypervolt_charging:
        return Decision(False, "iog dispatch but ev not charging")
    return Decision(True, "iog planned dispatch + ev charging")
