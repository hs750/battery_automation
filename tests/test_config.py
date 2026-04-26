import pytest

from battery_automation.config import load_config


REQUIRED = {
    "OCTOPUS_API_KEY": "k",
    "OCTOPUS_ACCOUNT_NUMBER": "A-1",
    "HYPERVOLT_EMAIL": "e@example.com",
    "HYPERVOLT_PASSWORD": "p",
    "GROWATT_API_TOKEN": "t",
    "GROWATT_DEVICE_SN": "s",
}


def _set_env(monkeypatch, **extra):
    # python-dotenv's load_dotenv() in load_config() is a no-op when the keys
    # are already in os.environ, so monkeypatch.setenv is sufficient here.
    for k, v in {**REQUIRED, **extra}.items():
        monkeypatch.setenv(k, v)
    # Strip anything we didn't explicitly set so test ordering is independent.
    for k in (
        "CHEAP_WINDOW_START",
        "CHEAP_WINDOW_END",
        "CHARGE_POWER_PERCENT",
        "CHARGE_STOP_SOC",
        "OCTOPUS_POLL_SECONDS",
        "DECISION_INTERVAL_SECONDS",
        "GROWATT_KEEPALIVE_SECONDS",
        "GROWATT_SLOT_LENGTH_SECONDS",
        "HYPERVOLT_STALE_SECONDS",
    ):
        if k not in extra:
            monkeypatch.delenv(k, raising=False)


def test_loads_with_defaults(monkeypatch):
    _set_env(monkeypatch)
    cfg = load_config()
    assert cfg.charge_power_percent == 100
    assert cfg.charge_stop_soc == 100
    assert cfg.hypervolt_stale_seconds == 300


def test_missing_required_raises(monkeypatch):
    _set_env(monkeypatch)
    monkeypatch.delenv("OCTOPUS_API_KEY")
    with pytest.raises(RuntimeError, match="OCTOPUS_API_KEY"):
        load_config()


@pytest.mark.parametrize("bad", ["-1", "101", "200"])
def test_charge_power_out_of_range_rejected(monkeypatch, bad):
    _set_env(monkeypatch, CHARGE_POWER_PERCENT=bad)
    with pytest.raises(RuntimeError, match="CHARGE_POWER_PERCENT"):
        load_config()


@pytest.mark.parametrize("bad", ["-5", "150"])
def test_charge_stop_soc_out_of_range_rejected(monkeypatch, bad):
    _set_env(monkeypatch, CHARGE_STOP_SOC=bad)
    with pytest.raises(RuntimeError, match="CHARGE_STOP_SOC"):
        load_config()


def test_non_integer_percent_rejected(monkeypatch):
    _set_env(monkeypatch, CHARGE_POWER_PERCENT="abc")
    with pytest.raises(RuntimeError, match="not an integer"):
        load_config()


@pytest.mark.parametrize(
    "var", ["OCTOPUS_POLL_SECONDS", "DECISION_INTERVAL_SECONDS", "HYPERVOLT_STALE_SECONDS"]
)
def test_non_positive_intervals_rejected(monkeypatch, var):
    _set_env(monkeypatch, **{var: "0"})
    with pytest.raises(RuntimeError, match=var):
        load_config()


@pytest.mark.parametrize("bad", ["23:30s", "abc", "25:00", "23:99", ""])
def test_malformed_cheap_window_rejected(monkeypatch, bad):
    _set_env(monkeypatch, CHEAP_WINDOW_START=bad)
    with pytest.raises(RuntimeError):
        load_config()
