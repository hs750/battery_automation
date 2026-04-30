"""Growatt SPH3000 control via the OpenAPI v1 cloud (growattServer >= 2.1.0).

Slot layout on the inverter is fixed by this client:
  - slot 1: permanent cheap-window fallback (the inverter accepts wrap-around
            time-of-day pairs natively, so 23:30→05:30 fits in a single slot)
  - slot 2: dynamic slot, written/cleared by the live decision loop
  - slot 3: unused

The permanent slot is re-asserted on every write, so the cheap window stays
honored even if this process crashes — the inverter will keep AC-charging
during 23:30–05:30 (or whatever the configured cheap window is) regardless of
the script's state.

DST note: writes are time-of-day local (Europe/London). Around DST transitions
a 15-min slot crossing 01:00–02:00 may execute twice (autumn fall-back) or be
skipped (spring forward). The 15-min sliding window bounds the impact.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import growattServer
from growattServer.exceptions import GrowattV1ApiError

LONDON = ZoneInfo("Europe/London")

log = logging.getLogger(__name__)

_DISABLED_PERIOD = {"start_time": time(0, 0), "end_time": time(0, 0), "enabled": False}


class GrowattClient:
    """Wraps growattServer.OpenApiV1 with an async-friendly interface.

    The library is sync (uses requests). Calls are pushed to a thread pool so they
    don't block the asyncio loop. Each write retries once on transient failure.
    """

    def __init__(
        self,
        api_token: str,
        device_sn: str,
        charge_power_percent: int,
        charge_stop_soc: int,
        cheap_window_start: time,
        cheap_window_end: time,
    ) -> None:
        self._api = growattServer.OpenApiV1(token=api_token)
        self._device_sn = device_sn
        self._charge_power = charge_power_percent
        self._charge_stop_soc = charge_stop_soc
        self._cheap_period = {
            "start_time": cheap_window_start,
            "end_time": cheap_window_end,
            "enabled": True,
        }

    async def set_ac_charge(self, start: datetime, end: datetime) -> None:
        """Activate the dynamic slot for `start`→`end` (Europe/London local time)."""
        start_local = start.astimezone(LONDON).time().replace(second=0, microsecond=0)
        end_local = end.astimezone(LONDON).time().replace(second=0, microsecond=0)
        dynamic = {"start_time": start_local, "end_time": end_local, "enabled": True}
        log.info(
            "growatt: enabling dynamic AC-charge %s → %s (power=%d%%, stop_soc=%d%%)",
            start_local.strftime("%H:%M"),
            end_local.strftime("%H:%M"),
            self._charge_power,
            self._charge_stop_soc,
        )
        await self._write_periods([self._cheap_period, dynamic, _DISABLED_PERIOD])

    async def clear_dynamic_window(self) -> None:
        """Disable the dynamic slot. Permanent cheap-window fallback stays active."""
        log.info("growatt: clearing dynamic AC-charge slot (permanent fallback retained)")
        await self._write_periods([self._cheap_period, _DISABLED_PERIOD, _DISABLED_PERIOD])

    async def _write_periods(self, periods: list[dict]) -> None:
        kwargs = {
            "device_sn": self._device_sn,
            "charge_power": self._charge_power,
            "charge_stop_soc": self._charge_stop_soc,
            "mains_enabled": True,
            "periods": periods,
        }
        await self._call_with_retry(self._api.sph_write_ac_charge_times, **kwargs)

    async def _call_with_retry(self, fn, /, **kwargs) -> None:
        request_summary = _summarise_request(fn, kwargs)
        try:
            await asyncio.to_thread(fn, **kwargs)
        except Exception as e:  # noqa: BLE001  -- vendor lib raises a mix of types
            log.warning(
                "growatt: %s call failed [%s]; retrying once. request=%s; cause=%s",
                getattr(fn, "__name__", "api"),
                _describe_exc(e),
                request_summary,
                e,
            )
            await asyncio.sleep(1.0)
            await asyncio.to_thread(fn, **kwargs)


def _describe_exc(e: BaseException) -> str:
    parts = [type(e).__name__]
    if isinstance(e, GrowattV1ApiError):
        parts.append(f"error_code={e.error_code}")
        parts.append(f"error_msg={e.error_msg!r}")
    return " ".join(parts)


def _summarise_request(fn, kwargs: dict) -> str:
    name = getattr(fn, "__name__", "api")
    safe = {k: v for k, v in kwargs.items() if k != "periods"}
    if "device_sn" in safe:
        safe["device_sn"] = _redact(safe["device_sn"])
    periods = kwargs.get("periods")
    if isinstance(periods, list):
        safe["periods"] = [_format_period(p) for p in periods]
    return f"{name}({safe})"


def _format_period(p: dict) -> str:
    start = p.get("start_time")
    end = p.get("end_time")

    def fmt(t):
        return t.strftime("%H:%M") if hasattr(t, "strftime") else repr(t)

    return f"{fmt(start)}-{fmt(end)} enabled={p.get('enabled')}"


def _redact(value: str) -> str:
    if not isinstance(value, str) or len(value) <= 4:
        return "***"
    return f"***{value[-4:]}"


def slot_for_now(now: datetime, length: timedelta) -> tuple[datetime, datetime]:
    """Return a [now, now + length] window rounded to the minute, in `now`'s tz."""
    start = now.replace(second=0, microsecond=0)
    return start, start + length
