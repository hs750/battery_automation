from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from battery_automation.growatt import GrowattClient, slot_for_now

LONDON = ZoneInfo("Europe/London")


def _client() -> GrowattClient:
    return GrowattClient(
        api_token="t",
        device_sn="SN",
        charge_power_percent=100,
        charge_stop_soc=100,
        cheap_window_start=time(23, 30),
        cheap_window_end=time(5, 30),
    )


class _FakeApi:
    """Stand-in for `growattServer.OpenApiV1`: counts calls and can fail-on-demand."""

    def __init__(self, fail_first_n: int = 0, exc: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._fail_first_n = fail_first_n
        self._exc = exc or RuntimeError("transient")

    def sph_write_ac_charge_times(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) <= self._fail_first_n:
            raise self._exc
        return {"ok": True}


@pytest.mark.asyncio
async def test_set_ac_charge_writes_three_periods_with_permanent_first():
    c = _client()
    api = _FakeApi()
    c._api = api  # type: ignore[assignment]

    now = datetime(2026, 4, 25, 14, 0, tzinfo=LONDON).astimezone(timezone.utc)
    end = now + timedelta(minutes=15)
    await c.set_ac_charge(now, end)

    assert len(api.calls) == 1
    [kwargs] = api.calls
    assert kwargs["device_sn"] == "SN"
    assert kwargs["charge_power"] == 100
    assert kwargs["charge_stop_soc"] == 100
    assert kwargs["mains_enabled"] is True
    periods = kwargs["periods"]
    assert len(periods) == 3
    # Slot 1 (permanent cheap window) is asserted on every write.
    assert periods[0]["enabled"] is True
    assert periods[0]["start_time"] == time(23, 30)
    assert periods[0]["end_time"] == time(5, 30)
    # Slot 2 (dynamic) is the requested window, in local time.
    assert periods[1]["enabled"] is True
    assert periods[1]["start_time"] == time(14, 0)
    assert periods[1]["end_time"] == time(14, 15)
    # Slot 3 stays disabled.
    assert periods[2]["enabled"] is False


@pytest.mark.asyncio
async def test_clear_dynamic_window_disables_slot_2_keeps_slot_1():
    c = _client()
    api = _FakeApi()
    c._api = api  # type: ignore[assignment]

    await c.clear_dynamic_window()

    [kwargs] = api.calls
    periods = kwargs["periods"]
    assert periods[0]["enabled"] is True  # permanent
    assert periods[1]["enabled"] is False
    assert periods[2]["enabled"] is False


@pytest.mark.asyncio
async def test_retry_once_on_transient_failure():
    c = _client()
    api = _FakeApi(fail_first_n=1)
    c._api = api  # type: ignore[assignment]

    await c.clear_dynamic_window()  # should retry and succeed
    assert len(api.calls) == 2


@pytest.mark.asyncio
async def test_two_failures_propagate():
    c = _client()
    api = _FakeApi(fail_first_n=2, exc=RuntimeError("persistent"))
    c._api = api  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="persistent"):
        await c.clear_dynamic_window()
    assert len(api.calls) == 2  # one initial + one retry, both failed


def test_slot_for_now_rounds_to_minute():
    now = datetime(2026, 4, 25, 14, 23, 47, 999, tzinfo=timezone.utc)
    start, end = slot_for_now(now, timedelta(minutes=15))
    assert start.second == 0 and start.microsecond == 0
    assert (end - start) == timedelta(minutes=15)
