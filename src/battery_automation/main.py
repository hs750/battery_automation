from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime, timedelta, timezone

from .config import Config, load_config
from .decision import Inputs, decide
from .growatt import GrowattClient, slot_for_now
from .hypervolt import HypervoltClient
from .octopus import Dispatch, OctopusClient

log = logging.getLogger("battery_automation")


class Service:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._octopus = OctopusClient(cfg.octopus_api_key, cfg.octopus_account_number)
        self._hypervolt = HypervoltClient(cfg.hypervolt_email, cfg.hypervolt_password)
        self._growatt = GrowattClient(
            cfg.growatt_api_token,
            cfg.growatt_device_sn,
            cfg.charge_power_percent,
            cfg.charge_stop_soc,
        )
        self._dispatches: list[Dispatch] = []
        self._dispatches_at: datetime | None = None
        self._cheap_now: bool = False
        self._last_growatt_write: datetime | None = None
        self._stop = asyncio.Event()

    async def run(self) -> None:
        # Clear any pre-existing schedule on the inverter so this script owns it entirely.
        await self._growatt.disable_ac_charge()

        await self._hypervolt.start()

        try:
            await asyncio.gather(
                self._octopus_loop(),
                self._decision_loop(),
                self._stop.wait(),
                return_exceptions=False,
            )
        finally:
            log.info("shutting down: clearing AC-charge schedule")
            try:
                await asyncio.wait_for(self._growatt.disable_ac_charge(), timeout=15.0)
            except Exception as e:  # noqa: BLE001
                log.error("failed to clear schedule on shutdown: %s", e)
            await self._hypervolt.aclose()
            await self._octopus.aclose()

    def request_stop(self) -> None:
        self._stop.set()

    async def _octopus_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._dispatches = await self._octopus.planned_dispatches()
                self._dispatches_at = datetime.now(timezone.utc)
                log.debug("octopus: %d planned dispatches", len(self._dispatches))
            except Exception as e:  # noqa: BLE001
                log.warning("octopus: poll failed: %s", e)
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._cfg.octopus_poll_seconds
                )
            except asyncio.TimeoutError:
                pass

    async def _decision_loop(self) -> None:
        while not self._stop.is_set():
            await self._evaluate_once()
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._cfg.decision_interval_seconds
                )
            except asyncio.TimeoutError:
                pass

    async def _evaluate_once(self) -> None:
        now = datetime.now(timezone.utc)
        in_dispatch = any(d.covers(now) for d in self._dispatches)
        hv_charging = self._hypervolt.is_charging()
        decision = decide(
            Inputs(
                now=now,
                cheap_window_start=self._cfg.cheap_window_start,
                cheap_window_end=self._cfg.cheap_window_end,
                in_planned_dispatch=in_dispatch,
                hypervolt_charging=hv_charging,
            )
        )

        if decision.cheap_now and not self._cheap_now:
            log.info("cheap_now → True (%s)", decision.reason)
            await self._write_charge_window(now)
        elif decision.cheap_now and self._cheap_now:
            if self._needs_keepalive(now):
                log.debug("cheap_now keepalive (%s)", decision.reason)
                await self._write_charge_window(now)
        elif not decision.cheap_now and self._cheap_now:
            log.info("cheap_now → False (%s)", decision.reason)
            try:
                await self._growatt.disable_ac_charge()
            except Exception as e:  # noqa: BLE001
                log.error("failed to disable AC-charge: %s", e)
                return
            self._last_growatt_write = None
        self._cheap_now = decision.cheap_now

    def _needs_keepalive(self, now: datetime) -> bool:
        if self._last_growatt_write is None:
            return True
        age = (now - self._last_growatt_write).total_seconds()
        return age >= self._cfg.growatt_keepalive_seconds

    async def _write_charge_window(self, now: datetime) -> None:
        slot_start, slot_end = slot_for_now(
            now, timedelta(seconds=self._cfg.growatt_slot_length_seconds)
        )
        try:
            await self._growatt.set_ac_charge(slot_start, slot_end)
        except Exception as e:  # noqa: BLE001
            log.error("failed to set AC-charge window: %s", e)
            return
        self._last_growatt_write = now


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def _main() -> None:
    cfg = load_config()
    _setup_logging(cfg.log_level)
    service = Service(cfg)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, service.request_stop)

    await service.run()


def run() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    run()
