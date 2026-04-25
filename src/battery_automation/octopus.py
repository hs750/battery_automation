import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

GRAPHQL_URL = "https://api.octopus.energy/v1/graphql/"

OBTAIN_TOKEN_MUTATION = """
mutation Login($input: ObtainJSONWebTokenInput!) {
  obtainKrakenToken(input: $input) {
    token
    refreshToken
    refreshExpiresIn
  }
}
"""

PLANNED_DISPATCHES_QUERY = """
query PlannedDispatches($input: String!) {
  plannedDispatches(accountNumber: $input) {
    startDt
    endDt
    delta
    meta { source location }
  }
}
"""

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Dispatch:
    start: datetime
    end: datetime
    delta: str | None
    source: str | None
    location: str | None

    def covers(self, now: datetime) -> bool:
        return self.start <= now <= self.end


class OctopusClient:
    """Authenticates against Kraken and reads IOG planned dispatches."""

    def __init__(self, api_key: str, account_number: str) -> None:
        self._api_key = api_key
        self._account_number = account_number
        self._token: str | None = None
        self._client = httpx.AsyncClient(timeout=15.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _ensure_token(self) -> str:
        if self._token is not None:
            return self._token
        r = await self._client.post(
            GRAPHQL_URL,
            json={
                "query": OBTAIN_TOKEN_MUTATION,
                "variables": {"input": {"APIKey": self._api_key}},
            },
        )
        r.raise_for_status()
        data = r.json()
        if "errors" in data:
            raise RuntimeError(f"octopus token error: {data['errors']}")
        self._token = data["data"]["obtainKrakenToken"]["token"]
        log.info("octopus: obtained kraken token")
        return self._token

    async def planned_dispatches(self) -> list[Dispatch]:
        token = await self._ensure_token()
        r = await self._client.post(
            GRAPHQL_URL,
            headers={"Authorization": token},
            json={
                "query": PLANNED_DISPATCHES_QUERY,
                "variables": {"input": self._account_number},
            },
        )
        if r.status_code == 401:
            self._token = None
            token = await self._ensure_token()
            r = await self._client.post(
                GRAPHQL_URL,
                headers={"Authorization": token},
                json={
                    "query": PLANNED_DISPATCHES_QUERY,
                    "variables": {"input": self._account_number},
                },
            )
        r.raise_for_status()
        data = r.json()
        if "errors" in data:
            raise RuntimeError(f"octopus dispatches error: {data['errors']}")
        return [_parse_dispatch(d) for d in data["data"]["plannedDispatches"] or []]

    async def active_dispatch(self, now: datetime | None = None) -> Dispatch | None:
        now = now or datetime.now(timezone.utc)
        for d in await self.planned_dispatches():
            if d.covers(now):
                return d
        return None


def _parse_dispatch(raw: dict) -> Dispatch:
    meta = raw.get("meta") or {}
    return Dispatch(
        start=_parse_iso(raw["startDt"]),
        end=_parse_iso(raw["endDt"]),
        delta=raw.get("delta"),
        source=meta.get("source"),
        location=meta.get("location"),
    )


def _parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)
