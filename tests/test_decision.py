from datetime import time

import pytest

from battery_automation.decision import Inputs, decide, in_window


@pytest.mark.parametrize(
    "now, start, end, expected",
    [
        (time(23, 30), time(23, 0), time(5, 30), True),   # mid wrap-around window
        (time(2, 0), time(23, 0), time(5, 30), True),    # after midnight wrap
        (time(5, 30), time(23, 0), time(5, 30), False),  # exclusive end
        (time(22, 59), time(23, 0), time(5, 30), False), # just before
        (time(12, 0), time(9, 0), time(17, 0), True),    # non-wrap window
    ],
)
def test_in_window(now, start, end, expected):
    assert in_window(now, start, end) is expected


def test_no_dispatch_no_charge():
    assert decide(Inputs(in_planned_dispatch=False, hypervolt_charging=True)).cheap_now is False


def test_dispatch_without_ev_charging_no_charge():
    d = decide(Inputs(in_planned_dispatch=True, hypervolt_charging=False))
    assert d.cheap_now is False
    assert "ev not charging" in d.reason


def test_dispatch_with_unknown_ev_state_no_charge():
    assert decide(Inputs(in_planned_dispatch=True, hypervolt_charging=None)).cheap_now is False


def test_dispatch_plus_ev_charging_charges():
    d = decide(Inputs(in_planned_dispatch=True, hypervolt_charging=True))
    assert d.cheap_now is True
    assert "dispatch" in d.reason


def test_ev_charging_without_dispatch_no_charge():
    # No IOG signal — script ignores it; standard window (if any) is the inverter's
    # problem via the permanent slot, not this decision function.
    assert decide(Inputs(in_planned_dispatch=False, hypervolt_charging=True)).cheap_now is False
