from datetime import UTC, datetime, time

import pytest

from battery_automation.decision import Inputs, decide, in_window


@pytest.mark.parametrize(
    "now, start, end, expected",
    [
        (time(23, 30), time(23, 0), time(5, 30), True),  # mid wrap-around window
        (time(2, 0), time(23, 0), time(5, 30), True),  # after midnight wrap
        (time(5, 30), time(23, 0), time(5, 30), False),  # exclusive end
        (time(22, 59), time(23, 0), time(5, 30), False),  # just before
        (time(12, 0), time(9, 0), time(17, 0), True),  # non-wrap window
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


def test_booked_happy_hour_enables_charge_without_iog():
    end = datetime(2026, 4, 25, 13, tzinfo=UTC)
    decision = decide(
        Inputs(
            in_planned_dispatch=False,
            hypervolt_charging=None,
            booked_happy_hour_end=end,
        )
    )
    assert decision.cheap_now is True
    assert decision.charge_until == end
    assert "happy hour" in decision.reason


def test_iog_and_happy_hour_combined_does_not_cap_iog_window():
    end = datetime(2026, 4, 25, 13, tzinfo=UTC)
    decision = decide(
        Inputs(
            in_planned_dispatch=True,
            hypervolt_charging=True,
            booked_happy_hour_end=end,
        )
    )
    assert decision.cheap_now is True
    assert decision.charge_until is None


def test_booked_happy_hour_does_not_require_ev_or_iog():
    decision = decide(
        Inputs(
            in_planned_dispatch=False,
            hypervolt_charging=False,
            booked_happy_hour_end=datetime(2026, 4, 25, 13, tzinfo=UTC),
        )
    )
    assert decision.cheap_now is True
