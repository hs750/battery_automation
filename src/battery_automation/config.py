import os
from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

LONDON = ZoneInfo("Europe/London")


def _parse_hhmm(s: str) -> time:
    try:
        h, m = s.strip().split(":")
        return time(int(h), int(m))
    except (ValueError, AttributeError) as e:
        raise RuntimeError(f"invalid HH:MM time {s!r}: {e}") from e


def _required(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"missing required env var: {name}")
    return v


def _percent(name: str, default: str) -> int:
    raw = os.environ.get(name, default)
    try:
        v = int(raw)
    except ValueError as e:
        raise RuntimeError(f"{name}={raw!r} is not an integer") from e
    if not 0 <= v <= 100:
        raise RuntimeError(f"{name}={v} out of range (must be 0..100)")
    return v


def _positive_int(name: str, default: str) -> int:
    raw = os.environ.get(name, default)
    try:
        v = int(raw)
    except ValueError as e:
        raise RuntimeError(f"{name}={raw!r} is not an integer") from e
    if v <= 0:
        raise RuntimeError(f"{name}={v} must be > 0")
    return v


@dataclass(frozen=True)
class Config:
    octopus_api_key: str
    octopus_account_number: str

    hypervolt_email: str
    hypervolt_password: str

    growatt_api_token: str
    growatt_device_sn: str

    cheap_window_start: time
    cheap_window_end: time

    charge_power_percent: int
    charge_stop_soc: int

    log_level: str
    octopus_poll_seconds: int
    decision_interval_seconds: int
    growatt_keepalive_seconds: int
    growatt_slot_length_seconds: int
    hypervolt_stale_seconds: int


def load_config() -> Config:
    load_dotenv()
    return Config(
        octopus_api_key=_required("OCTOPUS_API_KEY"),
        octopus_account_number=_required("OCTOPUS_ACCOUNT_NUMBER"),
        hypervolt_email=_required("HYPERVOLT_EMAIL"),
        hypervolt_password=_required("HYPERVOLT_PASSWORD"),
        growatt_api_token=_required("GROWATT_API_TOKEN"),
        growatt_device_sn=_required("GROWATT_DEVICE_SN"),
        cheap_window_start=_parse_hhmm(os.environ.get("CHEAP_WINDOW_START", "23:30")),
        cheap_window_end=_parse_hhmm(os.environ.get("CHEAP_WINDOW_END", "05:30")),
        charge_power_percent=_percent("CHARGE_POWER_PERCENT", "100"),
        charge_stop_soc=_percent("CHARGE_STOP_SOC", "100"),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        octopus_poll_seconds=_positive_int("OCTOPUS_POLL_SECONDS", "120"),
        decision_interval_seconds=_positive_int("DECISION_INTERVAL_SECONDS", "60"),
        growatt_keepalive_seconds=_positive_int("GROWATT_KEEPALIVE_SECONDS", "600"),
        growatt_slot_length_seconds=_positive_int("GROWATT_SLOT_LENGTH_SECONDS", "900"),
        hypervolt_stale_seconds=_positive_int("HYPERVOLT_STALE_SECONDS", "300"),
    )
