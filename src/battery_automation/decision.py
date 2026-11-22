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
from datetime import datetime, time
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")


@dataclass(frozen=True)
class Inputs:
    in_planned_dispatch: bool
    hypervolt_charging: bool | None  # None = unknown / stale
    booked_happy_hour_end: datetime | None = None


@dataclass(frozen=True)
class Decision:
    cheap_now: bool
    reason: str
    charge_until: datetime | None = None


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
    iog_charge = inp.in_planned_dispatch and inp.hypervolt_charging is True
    happy_hour_charge = inp.booked_happy_hour_end is not None
    if iog_charge and happy_hour_charge:
        return Decision(True, "iog planned dispatch + ev charging + booked happy hour")
    if iog_charge:
        return Decision(True, "iog planned dispatch + ev charging")
    if happy_hour_charge:
        return Decision(
            True,
            "booked happy hour",
            charge_until=inp.booked_happy_hour_end,
        )
    if not inp.in_planned_dispatch:
        return Decision(False, "no iog dispatch")
    return Decision(False, "iog dispatch but ev not charging")
