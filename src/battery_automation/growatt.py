"""Growatt SPH3000 control via the OpenAPI v1 cloud (growattServer >= 2.1.0)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import growattServer

LONDON = ZoneInfo("Europe/London")

log = logging.getLogger(__name__)

_DISABLED_PERIOD = {"start_time": time(0, 0), "end_time": time(0, 0), "enabled": False}


class GrowattClient:
    """Wraps growattServer.OpenApiV1 with an async-friendly interface.

    The library is sync (uses requests). Calls are pushed to a thread pool so they
    don't block the asyncio loop.
    """

    def __init__(
        self,
        api_token: str,
        device_sn: str,
        charge_power_percent: int,
        charge_stop_soc: int,
    ) -> None:
        self._api = growattServer.OpenApiV1(token=api_token)
        self._device_sn = device_sn
        self._charge_power = charge_power_percent
        self._charge_stop_soc = charge_stop_soc

    async def set_ac_charge(self, start: datetime, end: datetime) -> None:
        """Configure period 1 to AC-charge from `start` to `end` (Europe/London local time)."""
        start_local = start.astimezone(LONDON).time().replace(second=0, microsecond=0)
        end_local = end.astimezone(LONDON).time().replace(second=0, microsecond=0)
        periods = [
            {"start_time": start_local, "end_time": end_local, "enabled": True},
            _DISABLED_PERIOD,
            _DISABLED_PERIOD,
        ]
        log.info(
            "growatt: enabling AC-charge %s → %s (power=%d%%, stop_soc=%d%%)",
            start_local.strftime("%H:%M"),
            end_local.strftime("%H:%M"),
            self._charge_power,
            self._charge_stop_soc,
        )
        await asyncio.to_thread(
            self._api.sph_write_ac_charge_times,
            device_sn=self._device_sn,
            charge_power=self._charge_power,
            charge_stop_soc=self._charge_stop_soc,
            mains_enabled=True,
            periods=periods,
        )

    async def disable_ac_charge(self) -> None:
        """Clear all three time periods and disable mains charging."""
        log.info("growatt: disabling AC-charge")
        await asyncio.to_thread(
            self._api.sph_write_ac_charge_times,
            device_sn=self._device_sn,
            charge_power=self._charge_power,
            charge_stop_soc=self._charge_stop_soc,
            mains_enabled=False,
            periods=[_DISABLED_PERIOD, _DISABLED_PERIOD, _DISABLED_PERIOD],
        )


def slot_for_now(now: datetime, length: timedelta) -> tuple[datetime, datetime]:
    """Return a [now, now + length] window rounded to the minute, in `now`'s tz."""
    start = now.replace(second=0, microsecond=0)
    return start, start + length
