"""Hypervolt cloud client.

Auth is OAuth2 password grant against the Hypervolt Keycloak realm; live state
is delivered over a WebSocket. Constants verified against the gndean
home-assistant-hypervolt-charger reference (2026-04-20).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx
import websockets

KEYCLOAK_TOKEN_URL = (
    "https://kc.prod.hypervolt.co.uk/realms/retail-customers/protocol/openid-connect/token"
)
KEYCLOAK_CLIENT_ID = "home-assistant"
USERS_ME_URL = "https://api.hypervolt.co.uk/users/me?includes=chargers"
WS_SYNC_URL = "wss://api.hypervolt.co.uk/ws/charger/{charger_id}/sync"

log = logging.getLogger(__name__)


class HypervoltClient:
    """Tracks live charging state. State is `None` until the first WS message arrives."""

    def __init__(self, email: str, password: str) -> None:
        self._email = email
        self._password = password
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._http = httpx.AsyncClient(timeout=15.0)
        self._charger_id: str | None = None
        self._latest: dict[str, Any] = {}
        self._latest_at: float | None = None
        self._task: asyncio.Task | None = None

    async def aclose(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        await self._http.aclose()

    async def start(self) -> None:
        await self._login()
        await self._discover_charger()
        self._task = asyncio.create_task(self._run_forever(), name="hypervolt-ws")

    async def _login(self) -> None:
        r = await self._http.post(
            KEYCLOAK_TOKEN_URL,
            data={
                "client_id": KEYCLOAK_CLIENT_ID,
                "grant_type": "password",
                "scope": "openid profile email offline_access",
                "username": self._email,
                "password": self._password,
            },
        )
        r.raise_for_status()
        body = r.json()
        self._access_token = body["access_token"]
        self._refresh_token = body.get("refresh_token")
        log.info("hypervolt: logged in")

    async def _refresh(self) -> None:
        if not self._refresh_token:
            await self._login()
            return
        r = await self._http.post(
            KEYCLOAK_TOKEN_URL,
            data={
                "client_id": KEYCLOAK_CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
            },
        )
        if r.status_code >= 400:
            await self._login()
            return
        body = r.json()
        self._access_token = body["access_token"]
        self._refresh_token = body.get("refresh_token", self._refresh_token)

    async def _discover_charger(self) -> None:
        r = await self._http.get(
            USERS_ME_URL,
            headers={"Authorization": f"Bearer {self._access_token}"},
        )
        if r.status_code == 401:
            await self._refresh()
            r = await self._http.get(
                USERS_ME_URL,
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
        r.raise_for_status()
        chargers = r.json().get("chargers") or []
        if not chargers:
            raise RuntimeError("hypervolt: no chargers on account")
        # If the user has multiple, take the first; could surface as config later.
        self._charger_id = chargers[0].get("charger_id") or chargers[0].get("id")
        log.info("hypervolt: charger=%s", self._charger_id)

    async def _run_forever(self) -> None:
        backoff = 3.0
        needs_refresh = False
        while True:
            if needs_refresh:
                try:
                    await self._refresh()
                except (httpx.HTTPError, KeyError) as e:
                    log.warning("hypervolt: token refresh failed: %s; will reconnect anyway", e)
            try:
                await self._consume_ws()
                backoff = 3.0
            except asyncio.CancelledError:
                raise
            except (websockets.WebSocketException, httpx.HTTPError, OSError) as e:
                log.warning("hypervolt: ws error %s; reconnecting in %.0fs", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.7, 300.0)
            needs_refresh = True

    async def _consume_ws(self) -> None:
        assert self._charger_id is not None
        url = WS_SYNC_URL.format(charger_id=self._charger_id)
        async with websockets.connect(url, ping_interval=30, ping_timeout=10) as ws:
            await ws.send(
                json.dumps(
                    {
                        "id": str(int(time.time() * 1_000_000)),
                        "method": "login",
                        "params": {"token": self._access_token, "version": 3},
                        "jsonrpc": "2.0",
                    }
                )
            )
            async for raw in ws:
                self._on_message(raw)

    def _on_message(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        if isinstance(msg, dict) and "error" in msg:
            log.debug("hypervolt: ws rpc error: %s", msg["error"])
            return
        # Snapshots and applies arrive as JSON-RPC notifications/results; field
        # names of interest are at the top level of `params` or `result`.
        params = msg.get("params") or msg.get("result") or {}
        if not isinstance(params, dict):
            return
        for key in ("ct_power", "charging", "max_current"):
            if key in params:
                self._latest[key] = params[key]
        self._latest_at = time.time()

    def is_charging(self) -> bool | None:
        """True if the charger is currently delivering power. None if state is stale/unknown.

        Primary signal: `ct_power` watts > 1000 (the threshold from DECISIONS.md).
        Fallback: the boolean `charging` field from session-in-progress messages.
        We deliberately do NOT consult `release_state` — it tracks user-cancellation
        (RELEASED vs DEFAULT), not power delivery.
        """
        if self._latest_at is None or time.time() - self._latest_at > 300:
            return None
        if "ct_power" in self._latest:
            return float(self._latest["ct_power"]) > 1000.0
        if "charging" in self._latest:
            return bool(self._latest["charging"])
        return None
