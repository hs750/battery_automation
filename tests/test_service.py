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
    )
    base.update(overrides)
    return Config(**base)


class FakeOctopus:
    def __init__(self):
        self.dispatches: list[Dispatch] = []

    async def planned_dispatches(self):
        return self.dispatches

    async def aclose(self):
        pass


class FakeHypervolt:
    def __init__(self):
        self.charging: bool | None = None

    async def start(self):
        pass

    async def aclose(self):
        pass

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


@pytest.mark.asyncio
async def test_no_signal_no_writes():
    s = _service()
    await s._evaluate_once(_at("18:00"))
    assert s._cheap_now is False
    assert s._growatt.calls == []


@pytest.mark.asyncio
async def test_rising_edge_inside_standard_window_writes():
    s = _service()
    await s._evaluate_once(_at("00:30"))
    assert s._cheap_now is True
    assert len(s._growatt.calls) == 1
    kind, start, end = s._growatt.calls[0]
    assert kind == "set"
    assert (end - start) == timedelta(seconds=900)


@pytest.mark.asyncio
async def test_rising_edge_during_planned_dispatch_writes():
    s = _service()
    now = _at("14:00")
    s._dispatches = [
        Dispatch(
            start=now - timedelta(minutes=10),
            end=now + timedelta(minutes=20),
            delta=None,
            source=None,
            location=None,
        )
    ]
    await s._evaluate_once(now)
    assert s._cheap_now is True
    assert s._growatt.calls[0][0] == "set"


@pytest.mark.asyncio
async def test_falling_edge_disables():
    s = _service()
    await s._evaluate_once(_at("00:30"))
    assert s._cheap_now is True

    await s._evaluate_once(_at("06:00"))
    assert s._cheap_now is False
    assert s._growatt.calls[-1] == ("clear_dynamic",)
    assert s._last_growatt_write is None


@pytest.mark.asyncio
async def test_keepalive_only_after_interval():
    s = _service()
    t0 = _at("00:30")

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
async def test_failed_rising_write_self_heals_next_tick():
    s = _service()
    s._growatt.fail_next_set = True

    await s._evaluate_once(_at("00:30"))
    # Write failed. cheap_now still becomes True so the next tick treats this
    # as the sustained branch and retries via the keepalive path.
    assert s._cheap_now is True
    assert s._last_growatt_write is None
    assert s._growatt.calls == []

    await s._evaluate_once(_at("00:31"))
    assert any(c[0] == "set" for c in s._growatt.calls)
    assert s._last_growatt_write is not None


@pytest.mark.asyncio
async def test_failed_falling_clear_does_not_clear_state():
    s = _service()
    await s._evaluate_once(_at("00:30"))
    assert s._cheap_now is True

    s._growatt.fail_next_clear = True
    await s._evaluate_once(_at("06:00"))
    # Clear failed: cheap_now stays True so next tick will retry the clear.
    assert s._cheap_now is True

    await s._evaluate_once(_at("06:01"))
    assert s._cheap_now is False
    assert s._growatt.calls[-1] == ("clear_dynamic",)


@pytest.mark.asyncio
async def test_hypervolt_signal_only_in_trust_window():
    s = _service()
    s._hypervolt.charging = True

    # 11:00 — inside the 09:00–16:00 trust window
    await s._evaluate_once(_at("11:00"))
    assert s._cheap_now is True
    s._growatt.calls.clear()
    s._cheap_now = False  # reset for next case
    s._last_growatt_write = None

    # 19:00 — outside trust window; manual boost charge ignored
    await s._evaluate_once(_at("19:00"))
    assert s._cheap_now is False
    assert s._growatt.calls == []
