from datetime import datetime, time, timezone

import pytest

from battery_automation.decision import LONDON, Inputs, decide, in_window


def _ts(local_hhmm: str) -> datetime:
    h, m = local_hhmm.split(":")
    return datetime(2026, 4, 25, int(h), int(m), tzinfo=LONDON).astimezone(timezone.utc)


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


def _inputs(local_hhmm: str, *, dispatch=False, hv=None) -> Inputs:
    return Inputs(
        now=_ts(local_hhmm),
        cheap_window_start=time(23, 0),
        cheap_window_end=time(5, 30),
        in_planned_dispatch=dispatch,
        hypervolt_charging=hv,
    )


def test_standard_window_triggers():
    assert decide(_inputs("00:30")).cheap_now is True


def test_outside_window_no_signal():
    assert decide(_inputs("18:00")).cheap_now is False


def test_planned_dispatch_overrides():
    d = decide(_inputs("14:00", dispatch=True))
    assert d.cheap_now is True
    assert "dispatch" in d.reason


def test_hypervolt_only_in_plausible_window():
    assert decide(_inputs("11:00", hv=True)).cheap_now is True
    # Outside trust window — manual boost charge at peak rate, ignore.
    assert decide(_inputs("19:00", hv=True)).cheap_now is False


def test_hypervolt_unknown_doesnt_trigger():
    assert decide(_inputs("11:00", hv=None)).cheap_now is False
