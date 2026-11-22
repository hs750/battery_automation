"""Read account-booked Weekend Happy Hours from Octopus."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .octopus import OctopusClient

HAPPY_HOURS_QUERY = """
query HappyHours($accountNumber: String!) {
  savingSessions {
    account(accountNumber: $accountNumber) {
      joinedEvents {
        eventType
        startAt
        endAt
      }
    }
  }
}
"""

_HAPPY_HOUR_EVENT_TYPE = "WEEKEND_HAPPY_HOUR"


@dataclass(frozen=True)
class BookedHappyHour:
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True)
class HappyHoursSnapshot:
    booked_happy_hours: tuple[BookedHappyHour, ...]

    def active_end_at(self, now: datetime) -> datetime | None:
        now = _as_utc(now)
        active_ends = [
            event.end_at
            for event in self.booked_happy_hours
            if event.start_at <= now < event.end_at
        ]
        return max(active_ends) if active_ends else None


class HappyHoursClient:
    """Poll account bookings; this client never discovers or joins events."""

    def __init__(self, octopus: OctopusClient, account_number: str) -> None:
        self._octopus = octopus
        self._account_number = account_number

    async def poll(self) -> HappyHoursSnapshot:
        data = await self._octopus.post_backend_authed(
            HAPPY_HOURS_QUERY, {"accountNumber": self._account_number}
        )
        try:
            raw_events = data["savingSessions"]["account"]["joinedEvents"]
            if not isinstance(raw_events, list):
                raise TypeError("joinedEvents must be a list")
            booked = tuple(
                _parse_happy_hour(event)
                for event in raw_events
                if _event_type(event) == _HAPPY_HOUR_EVENT_TYPE
            )
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError(f"invalid Happy Hours response: {error}") from error
        return HappyHoursSnapshot(booked)


def _event_type(raw: object) -> str:
    if not isinstance(raw, dict):
        raise TypeError("joined event must be an object")
    event_type = raw.get("eventType")
    if not isinstance(event_type, str) or not event_type:
        raise ValueError("joined event type must be a non-empty string")
    return event_type


def _parse_happy_hour(raw: dict) -> BookedHappyHour:
    start_at = _parse_datetime(raw["startAt"])
    end_at = _parse_datetime(raw["endAt"])
    if end_at <= start_at:
        raise ValueError("Happy Hour end must be after its start")
    return BookedHappyHour(start_at, end_at)


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError("Happy Hour timestamp must be a string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Happy Hour timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("current time must include a timezone")
    return value.astimezone(UTC)
