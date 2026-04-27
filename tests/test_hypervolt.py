import json
import time as time_module

import pytest

from battery_automation import hypervolt
from battery_automation.hypervolt import HypervoltClient


@pytest.fixture
async def client():
    c = HypervoltClient("e@example.com", "p", stale_seconds=300)
    yield c
    await c.aclose()


def _msg(**params) -> str:
    return json.dumps({"jsonrpc": "2.0", "params": params})


async def test_unrecognized_message_does_not_update_latest_at(client, monkeypatch):
    monkeypatch.setattr(time_module, "time", lambda: 1000.0)
    client._on_message(_msg(some_other_field=42))
    # Message parsed (so _last_message_at advances) but no recognized field
    # was found, so _latest_at stays None — is_charging() must return None.
    assert client._last_message_at == 1000.0
    assert client._latest_at is None
    assert client.is_charging() is None


async def test_ct_power_above_threshold_means_charging(client, monkeypatch):
    monkeypatch.setattr(time_module, "time", lambda: 1000.0)
    client._on_message(_msg(ct_power=1500))
    assert client.is_charging() is True


async def test_ct_power_below_threshold_means_not_charging(client, monkeypatch):
    monkeypatch.setattr(time_module, "time", lambda: 1000.0)
    client._on_message(_msg(ct_power=500))
    assert client.is_charging() is False


async def test_charging_field_used_when_ct_power_absent(client, monkeypatch):
    monkeypatch.setattr(time_module, "time", lambda: 1000.0)
    client._on_message(_msg(charging=True))
    assert client.is_charging() is True


async def test_stale_state_returns_none(client, monkeypatch):
    monkeypatch.setattr(time_module, "time", lambda: 1000.0)
    client._on_message(_msg(ct_power=1500))
    # Jump past the 300s stale threshold.
    monkeypatch.setattr(time_module, "time", lambda: 1400.0)
    assert client.is_charging() is None


async def test_stale_seconds_is_configurable(monkeypatch):
    short = HypervoltClient("e@example.com", "p", stale_seconds=10)
    try:
        monkeypatch.setattr(time_module, "time", lambda: 1000.0)
        short._on_message(_msg(ct_power=1500))
        # 5s later: still fresh.
        monkeypatch.setattr(time_module, "time", lambda: 1005.0)
        assert short.is_charging() is True
        # 11s later: past the 10s threshold.
        monkeypatch.setattr(time_module, "time", lambda: 1011.0)
        assert short.is_charging() is None
    finally:
        await short.aclose()


async def test_drift_warning_fires_when_messages_arrive_but_no_recognized_fields(
    client, monkeypatch, caplog
):
    monkeypatch.setattr(time_module, "time", lambda: 1000.0)
    # Get a recognized field first so _latest_at is set.
    client._on_message(_msg(ct_power=1500))
    assert client.is_charging() is True

    # Now jump forward and only deliver unrecognized messages — schema drift.
    for offset in (350, 400, 450):
        monkeypatch.setattr(time_module, "time", lambda o=offset: 1000.0 + o)
        client._on_message(_msg(some_renamed_field=999))

    # Now check is_charging() — _latest_at is stale (>300s old) but
    # _last_message_at is fresh, so a drift warning should fire.
    monkeypatch.setattr(time_module, "time", lambda: 1450.0)
    with caplog.at_level("WARNING", logger=hypervolt.__name__):
        assert client.is_charging() is None
        assert any("api drift" in r.message for r in caplog.records)


async def test_drift_warning_rate_limited(client, monkeypatch, caplog):
    monkeypatch.setattr(time_module, "time", lambda: 1000.0)
    client._on_message(_msg(ct_power=1500))
    monkeypatch.setattr(time_module, "time", lambda: 1500.0)
    client._on_message(_msg(some_renamed_field=999))  # bumps _last_message_at

    with caplog.at_level("WARNING", logger=hypervolt.__name__):
        client.is_charging()
        client.is_charging()
        client.is_charging()
        warnings = [r for r in caplog.records if "api drift" in r.message]
        assert len(warnings) == 1


async def test_drift_warning_resets_on_recognized_field(client, monkeypatch, caplog):
    monkeypatch.setattr(time_module, "time", lambda: 1000.0)
    client._on_message(_msg(ct_power=1500))
    monkeypatch.setattr(time_module, "time", lambda: 1500.0)
    client._on_message(_msg(some_renamed_field=1))
    with caplog.at_level("WARNING", logger=hypervolt.__name__):
        client.is_charging()
    assert client._drift_warned is True

    # Recognized field arrives — drift state clears.
    client._on_message(_msg(ct_power=2000))
    assert client._drift_warned is False


async def test_malformed_json_ignored(client, monkeypatch):
    monkeypatch.setattr(time_module, "time", lambda: 1000.0)
    client._on_message("not json at all")
    assert client._last_message_at is None  # parse failed, nothing advanced
    assert client._latest_at is None


async def test_error_message_does_not_update_state(client, monkeypatch):
    monkeypatch.setattr(time_module, "time", lambda: 1000.0)
    client._on_message(json.dumps({"error": {"code": -32000, "message": "auth"}}))
    # _last_message_at advances (we did parse it) but _latest_at must not.
    assert client._last_message_at == 1000.0
    assert client._latest_at is None


async def test_pilot_status_a_means_unplugged(client, monkeypatch):
    monkeypatch.setattr(time_module, "time", lambda: 1000.0)
    client._on_message(_msg(pilot_status="A"))
    assert client.is_plugged_in() is False


async def test_pilot_status_b_means_plugged(client, monkeypatch):
    monkeypatch.setattr(time_module, "time", lambda: 1000.0)
    client._on_message(_msg(pilot_status="B"))
    assert client.is_plugged_in() is True


async def test_pilot_status_c_means_plugged(client, monkeypatch):
    monkeypatch.setattr(time_module, "time", lambda: 1000.0)
    client._on_message(_msg(pilot_status="C"))
    assert client.is_plugged_in() is True


async def test_is_plugged_in_unknown_when_no_pilot_status(client, monkeypatch):
    monkeypatch.setattr(time_module, "time", lambda: 1000.0)
    client._on_message(_msg(ct_power=1500))
    assert client.is_plugged_in() is None


async def test_is_plugged_in_stale_returns_none(client, monkeypatch):
    monkeypatch.setattr(time_module, "time", lambda: 1000.0)
    client._on_message(_msg(pilot_status="B"))
    monkeypatch.setattr(time_module, "time", lambda: 1400.0)
    assert client.is_plugged_in() is None


async def test_plugged_in_event_fires_on_a_to_b_transition(client, monkeypatch):
    monkeypatch.setattr(time_module, "time", lambda: 1000.0)
    client._on_message(_msg(pilot_status="A"))
    assert not client.plugged_in_event.is_set()
    client._on_message(_msg(pilot_status="B"))
    assert client.plugged_in_event.is_set()


async def test_plugged_in_event_does_not_fire_on_b_to_c(client, monkeypatch):
    monkeypatch.setattr(time_module, "time", lambda: 1000.0)
    client._on_message(_msg(pilot_status="B"))
    client.plugged_in_event.clear()
    client._on_message(_msg(pilot_status="C"))
    assert not client.plugged_in_event.is_set()


async def test_plugged_in_event_does_not_fire_on_unplug(client, monkeypatch):
    monkeypatch.setattr(time_module, "time", lambda: 1000.0)
    client._on_message(_msg(pilot_status="C"))
    client.plugged_in_event.clear()
    client._on_message(_msg(pilot_status="A"))
    assert not client.plugged_in_event.is_set()


async def test_plugged_in_event_fires_when_first_message_is_already_plugged(
    client, monkeypatch
):
    """Startup case: first message we ever see is `B`. Treat unknown→plugged as
    a plug-in transition so the octopus loop wakes immediately on cold start."""
    monkeypatch.setattr(time_module, "time", lambda: 1000.0)
    client._on_message(_msg(pilot_status="B"))
    assert client.plugged_in_event.is_set()