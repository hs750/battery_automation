from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime, timedelta, timezone

from .config import Config, load_config
from .decision import LONDON, Inputs, decide, in_window
from .growatt import GrowattClient, slot_for_now
from .hypervolt import HypervoltClient
from .octopus import Dispatch, OctopusClient

log = logging.getLogger("battery_automation")


class Service:
    def __init__(
        self,
        cfg: Config,
        octopus: OctopusClient,
        hypervolt: HypervoltClient,
        growatt: GrowattClient,
    ) -> None:
        self._cfg = cfg
        self._octopus = octopus
        self._hypervolt = hypervolt
        self._growatt = growatt
        self._dispatches: list[Dispatch] = []
        self._cheap_now: bool = False
        self._last_growatt_write: datetime | None = None
        self._last_mismatch: str | None = None
        self._stop = asyncio.Event()

    @classmethod
    def from_config(cls, cfg: Config) -> "Service":
        return cls(
            cfg,
            OctopusClient(cfg.octopus_api_key, cfg.octopus_account_number),
            HypervoltClient(cfg.hypervolt_email, cfg.hypervolt_password),
            GrowattClient(
                cfg.growatt_api_token,
                cfg.growatt_device_sn,
                cfg.charge_power_percent,
                cfg.charge_stop_soc,
                cfg.cheap_window_start,
                cfg.cheap_window_end,
            ),
        )

    async def run(self) -> None:
        # Re-assert the permanent cheap-window slot and clear any stale dynamic slot
        # so the inverter starts from a known baseline that this script owns.
        # Non-fatal: if the cloud is unreachable, the next decision-edge will re-attempt.
        try:
            await self._growatt.clear_dynamic_window()
        except Exception as e:  # noqa: BLE001
            log.warning(
                "startup: failed to assert permanent slot / clear dynamic: %s; "
                "continuing — will recover on next decision tick",
                e,
            )

        await self._hypervolt.start()

        try:
            await asyncio.gather(
                self._octopus_loop(),
                self._decision_loop(),
                self._stop.wait(),
                return_exceptions=False,
            )
        finally:
            log.info("shutting down: clearing dynamic slot (permanent fallback retained)")
            try:
                await asyncio.wait_for(
                    self._growatt.clear_dynamic_window(), timeout=15.0
                )
            except Exception as e:  # noqa: BLE001
                log.error("failed to clear dynamic slot on shutdown: %s", e)
            await self._hypervolt.aclose()
            await self._octopus.aclose()

    def request_stop(self) -> None:
        self._stop.set()

    async def _octopus_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._dispatches = await self._octopus.planned_dispatches()
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

    async def _evaluate_once(self, now: datetime | None = None) -> None:
        if now is None:
            now = datetime.now(timezone.utc)
        in_dispatch = any(d.covers(now) for d in self._dispatches)
        ev_charging = self._hypervolt.is_charging()
        decision = decide(
            Inputs(in_planned_dispatch=in_dispatch, hypervolt_charging=ev_charging)
        )
        self._log_signal_mismatch(now, in_dispatch, ev_charging)

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
                await self._growatt.clear_dynamic_window()
            except Exception as e:  # noqa: BLE001
                log.error("failed to clear dynamic slot: %s", e)
                return
            self._last_growatt_write = None
        self._cheap_now = decision.cheap_now

    def _log_signal_mismatch(
        self, now: datetime, in_dispatch: bool, ev_charging: bool | None
    ) -> None:
        """Edge-trigger a log when the IOG dispatch and EV signals disagree.

        Skipped while EV state is unknown. The standard overnight window is not
        a "mismatch" case under our model — we charge overnight regardless of
        whether the car is plugged in — so it's deliberately not flagged here.
        """
        if ev_charging is None:
            return
        in_standard = in_window(
            now.astimezone(LONDON).time(),
            self._cfg.cheap_window_start,
            self._cfg.cheap_window_end,
        )
        if in_dispatch and not ev_charging:
            current = "iog_dispatch_no_ev"
        elif ev_charging and not in_dispatch and not in_standard:
            current = "ev_no_iog"
        else:
            current = None
        if current == self._last_mismatch:
            return
        if current == "iog_dispatch_no_ev":
            log.info("signal mismatch: iog planned dispatch active but ev not charging")
        elif current == "ev_no_iog":
            log.warning(
                "signal mismatch: ev charging outside iog cheap period (peak-rate boost?)"
            )
        elif self._last_mismatch is not None:
            log.info("signal mismatch resolved (was %s)", self._last_mismatch)
        self._last_mismatch = current

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
    service = Service.from_config(cfg)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, service.request_stop)

    await service.run()


def run() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    run()
