import os
from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

LONDON = ZoneInfo("Europe/London")


def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _required(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"missing required env var: {name}")
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
        charge_power_percent=int(os.environ.get("CHARGE_POWER_PERCENT", "100")),
        charge_stop_soc=int(os.environ.get("CHARGE_STOP_SOC", "100")),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        octopus_poll_seconds=int(os.environ.get("OCTOPUS_POLL_SECONDS", "120")),
        decision_interval_seconds=int(os.environ.get("DECISION_INTERVAL_SECONDS", "60")),
        growatt_keepalive_seconds=int(os.environ.get("GROWATT_KEEPALIVE_SECONDS", "600")),
        growatt_slot_length_seconds=int(os.environ.get("GROWATT_SLOT_LENGTH_SECONDS", "900")),
    )
