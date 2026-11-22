from __future__ import annotations

import asyncio
import logging
import os
import signal
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from .config import Config, load_config
from .decision import LONDON, Inputs, decide, in_window
from .growatt import GrowattClient, slot_for_now
from .happy_hours import HappyHoursClient, HappyHoursSnapshot
from .hypervolt import HypervoltClient
from .octopus import BACKEND_GRAPHQL_URL, Dispatch, OctopusClient

log = logging.getLogger("battery_automation")

_HTTPX_REQUEST_LOG = 'HTTP Request: %s %s "%s %d %s"'

# Touched after each successful decision tick. Docker healthcheck reads its mtime.
LIVENESS_PATH = Path(os.environ.get("LIVENESS_PATH", "/tmp/battery-automation-alive"))


class Service:
    def __init__(
        self,
        cfg: Config,
        octopus: OctopusClient,
        hypervolt: HypervoltClient,
        growatt: GrowattClient,
        happy_hours: HappyHoursClient | None = None,
    ) -> None:
        self._cfg = cfg
        self._octopus = octopus
        self._hypervolt = hypervolt
        self._growatt = growatt
        self._happy_hours = happy_hours
        self._dispatches: list[Dispatch] = []
        self._happy_hours_snapshot: HappyHoursSnapshot | None = None
        self._happy_hours_refreshed_at: datetime | None = None
        self._cheap_now: bool = False
        self._charge_signature: tuple[str, datetime | None] | None = None
        self._last_growatt_write: datetime | None = None
        self._last_mismatch: str | None = None
        self._stop = asyncio.Event()

    @classmethod
    def from_config(cls, cfg: Config) -> Service:
        octopus = OctopusClient(cfg.octopus_api_key, cfg.octopus_account_number)
        return cls(
            cfg,
            octopus,
            HypervoltClient(
                cfg.hypervolt_email,
                cfg.hypervolt_password,
                stale_seconds=cfg.hypervolt_stale_seconds,
            ),
            GrowattClient(
                cfg.growatt_api_token,
                cfg.growatt_device_sn,
                cfg.charge_power_percent,
                cfg.charge_stop_soc,
                cfg.cheap_window_start,
                cfg.cheap_window_end,
            ),
            HappyHoursClient(octopus, cfg.octopus_account_number),
        )

    async def run(self) -> None:
        # Re-assert the permanent cheap-window slot and clear any stale dynamic slot
        # so the inverter starts from a known baseline that this script owns.
        # Non-fatal: if the cloud is unreachable, the next decision-edge will re-attempt.
        # Broad except: growattServer wraps requests and can raise anything.
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
            loops = [
                self._octopus_loop(),
                self._decision_loop(),
                self._stop.wait(),
            ]
            if self._happy_hours is not None:
                loops.append(self._happy_hours_loop())
            await asyncio.gather(*loops, return_exceptions=False)
        finally:
            log.info("shutting down: clearing dynamic slot (permanent fallback retained)")
            try:
                await asyncio.wait_for(self._growatt.clear_dynamic_window(), timeout=15.0)
            except Exception as e:  # noqa: BLE001  -- vendor lib raises a mix of types
                log.error("failed to clear dynamic slot on shutdown: %s", e)
            await self._hypervolt.aclose()
            await self._octopus.aclose()

    def request_stop(self) -> None:
        self._stop.set()

    async def _octopus_loop(self) -> None:
        while not self._stop.is_set():
            # Clear before checking so a plug-in arriving *during* this iteration
            # is observed by the next .wait() rather than being lost.
            self._hypervolt.plugged_in_event.clear()
            plugged = self._hypervolt.is_plugged_in()
            if plugged is False:
                # Car is unplugged — IOG won't schedule dispatches. Drop any
                # cached entries so a stale in-window dispatch can't drive a
                # write after unplug.
                if self._dispatches:
                    log.debug(
                        "octopus: car unplugged, clearing %d cached dispatches",
                        len(self._dispatches),
                    )
                    self._dispatches = []
            else:
                # Plugged in (True) or unknown (None) — poll. Treating unknown
                # as a poll keeps us safe on V2 chargers that don't report
                # pilot_status, and on cold start before the WS warms up.
                try:
                    self._dispatches = await self._octopus.planned_dispatches()
                    log.debug("octopus: %d planned dispatches", len(self._dispatches))
                except (TimeoutError, httpx.HTTPError, RuntimeError) as e:
                    log.warning("octopus: poll failed: %s", e)
            await self._wait_octopus_interval()

    async def _wait_octopus_interval(self) -> None:
        """Sleep until the next poll, but wake early on stop or plug-in."""
        stop_task = asyncio.create_task(self._stop.wait())
        plug_task = asyncio.create_task(self._hypervolt.plugged_in_event.wait())
        try:
            await asyncio.wait(
                {stop_task, plug_task},
                timeout=self._cfg.octopus_poll_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for t in (stop_task, plug_task):
                if not t.done():
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass

    async def _happy_hours_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._happy_hours_snapshot = await self._happy_hours.poll()
                self._happy_hours_refreshed_at = datetime.now(UTC)
                log.debug(
                    "happy hours: refreshed account bookings (%d)",
                    len(self._happy_hours_snapshot.booked_happy_hours),
                )
            except Exception as error:  # noqa: BLE001 -- backend failures must not stop service
                log.warning("happy hours: poll failed: %s", error)
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._cfg.happy_hours_poll_seconds
                )
            except TimeoutError:
                pass

    async def _decision_loop(self) -> None:
        while not self._stop.is_set():
            await self._evaluate_once()
            _touch_liveness()
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._cfg.decision_interval_seconds
                )
            except TimeoutError:
                pass

    async def _evaluate_once(self, now: datetime | None = None) -> None:
        if now is None:
            now = datetime.now(UTC)
        in_dispatch = any(d.covers(now) for d in self._dispatches)
        ev_charging = self._hypervolt.is_charging()
        happy_hour_end = self._active_happy_hour_end(now)
        decision = decide(
            Inputs(
                in_planned_dispatch=in_dispatch,
                hypervolt_charging=ev_charging,
                booked_happy_hour_end=happy_hour_end,
            )
        )
        self._log_signal_mismatch(now, in_dispatch, ev_charging)
        signature = (decision.reason, decision.charge_until)

        if decision.cheap_now and (not self._cheap_now or signature != self._charge_signature):
            # Rising edge: only flip _cheap_now after a successful write so a
            # transient Growatt failure causes the next tick to retry as a
            # fresh rising edge rather than masquerading as sustained-high.
            log.info("dynamic charging → True (%s)", decision.reason)
            if await self._write_charge_window(now, decision.charge_until):
                self._cheap_now = True
                self._charge_signature = signature
            elif self._cheap_now and decision.charge_until is not None:
                # The previous IOG or Happy Hour window may extend beyond the
                # newly required bound. Clear it if the replacement failed.
                try:
                    await self._growatt.clear_dynamic_window()
                except Exception:
                    log.exception("failed to clear previous dynamic slot after failed rewrite")
                else:
                    self._cheap_now = False
                    self._charge_signature = None
                    self._last_growatt_write = None
        elif decision.cheap_now and self._cheap_now:
            if self._needs_keepalive(now):
                log.debug("cheap_now keepalive (%s)", decision.reason)
                if await self._write_charge_window(now, decision.charge_until):
                    self._charge_signature = signature
        elif not decision.cheap_now and self._cheap_now:
            # Falling edge: keep _cheap_now=True on failure to retry next tick.
            log.info("cheap_now → False (%s)", decision.reason)
            try:
                await self._growatt.clear_dynamic_window()
            except Exception as e:  # noqa: BLE001  -- vendor lib raises a mix of types
                log.error("failed to clear dynamic slot: %s", e)
                return
            self._last_growatt_write = None
            self._cheap_now = False
            self._charge_signature = None

    def _active_happy_hour_end(self, now: datetime) -> datetime | None:
        snapshot = self._happy_hours_snapshot
        refreshed_at = self._happy_hours_refreshed_at
        if snapshot is None or refreshed_at is None:
            return None
        age = (now - refreshed_at).total_seconds()
        if age < 0 or age >= self._cfg.happy_hours_stale_seconds:
            return None
        end = snapshot.active_end_at(now)
        if end is None:
            return None
        return min(end, refreshed_at + timedelta(seconds=self._cfg.happy_hours_stale_seconds))

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
            log.warning("signal mismatch: ev charging outside iog cheap period (peak-rate boost?)")
        elif self._last_mismatch is not None:
            log.info("signal mismatch resolved (was %s)", self._last_mismatch)
        self._last_mismatch = current

    def _needs_keepalive(self, now: datetime) -> bool:
        if self._last_growatt_write is None:
            return True
        age = (now - self._last_growatt_write).total_seconds()
        return age >= self._cfg.growatt_keepalive_seconds

    async def _write_charge_window(
        self, now: datetime, charge_until: datetime | None = None
    ) -> bool:
        """Write the dynamic AC-charge slot. Returns True on success, False on failure."""
        slot_start, slot_end = slot_for_now(
            now, timedelta(seconds=self._cfg.growatt_slot_length_seconds)
        )
        if charge_until is not None:
            slot_end = min(slot_end, charge_until)
        slot_end = slot_end.replace(second=0, microsecond=0)
        if slot_end <= slot_start:
            log.warning("dynamic charging window is shorter than inverter time resolution")
            return False
        try:
            await self._growatt.set_ac_charge(slot_start, slot_end)
        except Exception:
            log.exception(
                "failed to set AC-charge window %s → %s",
                slot_start.isoformat(),
                slot_end.isoformat(),
            )
            return False
        self._last_growatt_write = now
        return True


def _touch_liveness() -> None:
    try:
        LIVENESS_PATH.touch(exist_ok=True)
    except OSError as e:
        log.debug("liveness touch failed: %s", e)


class _SuppressSuccessfulBackendRequest(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno != logging.INFO or record.msg != _HTTPX_REQUEST_LOG:
            return True
        if not isinstance(record.args, tuple) or len(record.args) != 5:
            return True
        method, url, _, status_code, _ = record.args
        return not (method == "POST" and str(url) == BACKEND_GRAPHQL_URL and status_code == 200)


def _setup_logging(level: str) -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    httpx_logger = logging.getLogger("httpx")
    for existing_filter in httpx_logger.filters:
        if isinstance(existing_filter, _SuppressSuccessfulBackendRequest):
            httpx_logger.removeFilter(existing_filter)
    if numeric_level > logging.DEBUG:
        httpx_logger.addFilter(_SuppressSuccessfulBackendRequest())


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
