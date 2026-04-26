import asyncio
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from battery_automation.config import Config
from battery_automation.main import Service
from battery_automation.octopus import Dispatch

LONDON = ZoneInfo("Europe/London")


def _cfg(**overrides) -> Config:
    base = dict(
        octopus_api_key="k",
        octopus_account_number="A-1",
        hypervolt_email="e@example.com",
        hypervolt_password="p",
        growatt_api_token="t",
        growatt_device_sn="s",
        cheap_window_start=time(23, 0),
        cheap_window_end=time(5, 30),
        charge_power_percent=100,
        charge_stop_soc=100,
        log_level="WARNING",
        octopus_poll_seconds=120,
        decision_interval_seconds=60,
        growatt_keepalive_seconds=600,
        growatt_slot_length_seconds=900,
        hypervolt_stale_seconds=300,
    )
    base.update(overrides)
    return Config(**base)


class FakeOctopus:
    def __init__(self):
        self.dispatches: list[Dispatch] = []
        self.closed = False

    async def planned_dispatches(self):
        return self.dispatches

    async def aclose(self):
        self.closed = True


class FakeHypervolt:
    def __init__(self):
        self.charging: bool | None = None
        self.started = False
        self.closed = False

    async def start(self):
        self.started = True

    async def aclose(self):
        self.closed = True

    def is_charging(self):
        return self.charging


class FakeGrowatt:
    def __init__(self):
        # ("set", start, end) | ("clear_dynamic",)
        self.calls: list[tuple] = []
        self.fail_next_set = False
        self.fail_next_clear = False

    async def set_ac_charge(self, start, end):
        if self.fail_next_set:
            self.fail_next_set = False
            raise RuntimeError("boom")
        self.calls.append(("set", start, end))

    async def clear_dynamic_window(self):
        if self.fail_next_clear:
            self.fail_next_clear = False
            raise RuntimeError("boom")
        self.calls.append(("clear_dynamic",))


def _service() -> Service:
    return Service(_cfg(), FakeOctopus(), FakeHypervolt(), FakeGrowatt())


def _at(local_hhmm: str) -> datetime:
    h, m = local_hhmm.split(":")
    return datetime(2026, 4, 25, int(h), int(m), tzinfo=LONDON).astimezone(timezone.utc)


def _dispatch(now: datetime, start_offset_min: int, end_offset_min: int) -> Dispatch:
    return Dispatch(
        start=now + timedelta(minutes=start_offset_min),
        end=now + timedelta(minutes=end_offset_min),
        delta=None,
        source=None,
        location=None,
    )


@pytest.mark.asyncio
async def test_no_signal_no_writes():
    s = _service()
    await s._evaluate_once(_at("18:00"))
    assert s._cheap_now is False
    assert s._growatt.calls == []


@pytest.mark.asyncio
async def test_standard_window_does_not_drive_dynamic_slot():
    """The permanent slot (slot 1) owns 23:30–05:30; the script leaves slot 2 alone."""
    s = _service()
    s._hypervolt.charging = True
    await s._evaluate_once(_at("00:30"))
    assert s._cheap_now is False
    assert s._growatt.calls == []


@pytest.mark.asyncio
async def test_rising_edge_during_planned_dispatch_writes():
    s = _service()
    s._hypervolt.charging = True
    now = _at("14:00")
    s._dispatches = [_dispatch(now, -10, 20)]
    await s._evaluate_once(now)
    assert s._cheap_now is True
    assert s._growatt.calls[0][0] == "set"
    kind, start, end = s._growatt.calls[0]
    assert (end - start) == timedelta(seconds=900)


@pytest.mark.asyncio
async def test_planned_dispatch_without_ev_charging_skips_dynamic_slot():
    s = _service()
    s._hypervolt.charging = False
    now = _at("14:00")
    s._dispatches = [_dispatch(now, -10, 20)]
    await s._evaluate_once(now)
    assert s._cheap_now is False
    assert s._growatt.calls == []


@pytest.mark.asyncio
async def test_falling_edge_disables():
    s = _service()
    s._hypervolt.charging = True
    t0 = _at("14:00")
    s._dispatches = [_dispatch(t0, -5, 10)]

    await s._evaluate_once(t0)
    assert s._cheap_now is True

    # Past the dispatch end — should fall.
    await s._evaluate_once(t0 + timedelta(minutes=15))
    assert s._cheap_now is False
    assert s._growatt.calls[-1] == ("clear_dynamic",)
    assert s._last_growatt_write is None


@pytest.mark.asyncio
async def test_keepalive_only_after_interval():
    s = _service()
    s._hypervolt.charging = True
    t0 = _at("14:00")
    s._dispatches = [_dispatch(t0, -5, 30)]

    await s._evaluate_once(t0)
    assert len(s._growatt.calls) == 1  # rising edge

    # 60s later: still cheap, but well below 600s keepalive interval
    await s._evaluate_once(t0 + timedelta(seconds=60))
    assert len(s._growatt.calls) == 1

    # 700s later: keepalive should fire
    await s._evaluate_once(t0 + timedelta(seconds=700))
    assert len(s._growatt.calls) == 2
    assert s._growatt.calls[1][0] == "set"


@pytest.mark.asyncio
async def test_failed_rising_write_retries_as_rising_edge_next_tick():
    s = _service()
    s._hypervolt.charging = True
    t0 = _at("14:00")
    s._dispatches = [_dispatch(t0, -5, 30)]
    s._growatt.fail_next_set = True

    await s._evaluate_once(t0)
    # Write failed: cheap_now stays False so the next tick is a fresh rising
    # edge rather than a sustained-high keepalive (symmetric with falling-edge
    # behavior on clear failure).
    assert s._cheap_now is False
    assert s._last_growatt_write is None
    assert s._growatt.calls == []

    await s._evaluate_once(t0 + timedelta(minutes=1))
    assert s._cheap_now is True
    assert any(c[0] == "set" for c in s._growatt.calls)
    assert s._last_growatt_write is not None


@pytest.mark.asyncio
async def test_failed_falling_clear_does_not_clear_state():
    s = _service()
    s._hypervolt.charging = True
    t0 = _at("14:00")
    s._dispatches = [_dispatch(t0, -5, 10)]

    await s._evaluate_once(t0)
    assert s._cheap_now is True

    s._growatt.fail_next_clear = True
    await s._evaluate_once(t0 + timedelta(minutes=15))  # dispatch over
    # Clear failed: cheap_now stays True so next tick will retry the clear.
    assert s._cheap_now is True

    await s._evaluate_once(t0 + timedelta(minutes=16))
    assert s._cheap_now is False
    assert s._growatt.calls[-1] == ("clear_dynamic",)


@pytest.mark.asyncio
async def test_logs_dispatch_without_ev_mismatch(caplog):
    s = _service()
    s._hypervolt.charging = False
    now = _at("14:00")
    s._dispatches = [_dispatch(now, -5, 30)]
    with caplog.at_level("INFO", logger="battery_automation"):
        await s._evaluate_once(now)
        assert any(
            "iog planned dispatch active but ev not charging" in r.message
            for r in caplog.records
        )
        caplog.clear()

        # Same state on the next tick — no duplicate log.
        await s._evaluate_once(now + timedelta(minutes=1))
        assert not any("mismatch" in r.message for r in caplog.records)

        # EV starts charging — mismatch resolved.
        s._hypervolt.charging = True
        await s._evaluate_once(now + timedelta(minutes=2))
        assert any("mismatch resolved" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_standard_window_without_ev_is_not_a_mismatch(caplog):
    """Overnight without the EV plugged in is the normal case, not a mismatch."""
    s = _service()
    s._hypervolt.charging = False
    with caplog.at_level("INFO", logger="battery_automation"):
        await s._evaluate_once(_at("00:30"))
        assert not any("mismatch" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_logs_peak_rate_boost_mismatch(caplog):
    s = _service()
    s._hypervolt.charging = True
    with caplog.at_level("WARNING", logger="battery_automation"):
        # 19:00, EV charging, no IOG cheap signal — manual peak-rate boost.
        await s._evaluate_once(_at("19:00"))
        assert any(
            "peak-rate boost" in r.message and r.levelname == "WARNING"
            for r in caplog.records
        )


@pytest.mark.asyncio
async def test_request_stop_shuts_down_cleanly():
    # Tight intervals so the loops don't keep us waiting after stop fires.
    s = Service(
        _cfg(decision_interval_seconds=1, octopus_poll_seconds=1),
        FakeOctopus(),
        FakeHypervolt(),
        FakeGrowatt(),
    )

    run_task = asyncio.create_task(s.run())
    # Let the startup phase complete so clear_dynamic_window has been called
    # and the loops have spun up.
    await asyncio.sleep(0.1)

    s.request_stop()
    await asyncio.wait_for(run_task, timeout=2.0)

    # Clear called once on startup + once on shutdown.
    clears = [c for c in s._growatt.calls if c == ("clear_dynamic",)]
    assert len(clears) == 2
    # Clients closed.
    assert s._hypervolt.closed is True
    assert s._octopus.closed is True
    assert s._hypervolt.started is True


@pytest.mark.asyncio
async def test_shutdown_clears_dynamic_slot_even_when_startup_clear_failed():
    fake_growatt = FakeGrowatt()
    fake_growatt.fail_next_clear = True  # startup clear will fail
    s = Service(
        _cfg(decision_interval_seconds=1, octopus_poll_seconds=1),
        FakeOctopus(),
        FakeHypervolt(),
        fake_growatt,
    )

    run_task = asyncio.create_task(s.run())
    await asyncio.sleep(0.1)
    s.request_stop()
    await asyncio.wait_for(run_task, timeout=2.0)

    # Startup clear raised (and was swallowed), so calls only contains the
    # shutdown clear.
    assert ("clear_dynamic",) in s._growatt.calls
