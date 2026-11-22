from datetime import UTC, datetime, timedelta

import pytest

from battery_automation.happy_hours import HAPPY_HOURS_QUERY, HappyHoursClient

NOW = datetime(2026, 4, 25, 12, tzinfo=UTC)


def _event(event_type="WEEKEND_HAPPY_HOUR", start=NOW, end=None):
    return {
        "eventType": event_type,
        "startAt": start.isoformat(),
        "endAt": (end or start + timedelta(hours=1)).isoformat(),
    }


def _response(events):
    return {"savingSessions": {"account": {"joinedEvents": events}}}


class FakeOctopus:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def post_backend_authed(self, query, variables):
        self.calls.append((query, variables))
        return self.response


@pytest.mark.asyncio
async def test_active_booked_happy_hour_is_detected_with_inclusive_start_exclusive_end():
    start = NOW - timedelta(minutes=5)
    end = NOW + timedelta(minutes=55)
    octopus = FakeOctopus(_response([_event(start=start, end=end)]))

    snapshot = await HappyHoursClient(octopus, "A-1").poll()

    assert snapshot.active_end_at(NOW) == end
    assert snapshot.active_end_at(start) == end
    assert snapshot.active_end_at(start - timedelta(microseconds=1)) is None
    assert snapshot.active_end_at(end) is None
    assert octopus.calls == [(HAPPY_HOURS_QUERY, {"accountNumber": "A-1"})]


@pytest.mark.asyncio
async def test_unbooked_happy_hour_is_not_detected():
    octopus = FakeOctopus(_response([]))

    snapshot = await HappyHoursClient(octopus, "A-1").poll()

    assert snapshot.active_end_at(NOW) is None


@pytest.mark.asyncio
async def test_unrelated_joined_events_do_not_require_happy_hour_fields():
    octopus = FakeOctopus(_response([{"eventType": "TURN_DOWN"}]))

    snapshot = await HappyHoursClient(octopus, "A-1").poll()

    assert snapshot.active_end_at(NOW) is None


def test_query_is_read_only_and_only_selects_account_joined_events():
    assert "joinedEvents" in HAPPY_HOURS_QUERY
    assert "account(accountNumber: $accountNumber)" in HAPPY_HOURS_QUERY
    assert "events(" not in HAPPY_HOURS_QUERY
    assert "joinSavingSessionsEvent" not in HAPPY_HOURS_QUERY
    assert "mutation" not in HAPPY_HOURS_QUERY.lower()


@pytest.mark.parametrize(
    "event",
    [
        {"eventType": "WEEKEND_HAPPY_HOUR", "startAt": "not a timestamp", "endAt": NOW.isoformat()},
        {
            "eventType": "WEEKEND_HAPPY_HOUR",
            "startAt": NOW.isoformat(),
            "endAt": "2026-04-25T13:00:00",
        },
        {
            "eventType": "WEEKEND_HAPPY_HOUR",
            "startAt": NOW.isoformat(),
            "endAt": (NOW - timedelta(seconds=1)).isoformat(),
        },
    ],
)
@pytest.mark.asyncio
async def test_malformed_happy_hour_booking_fails_closed(event):
    with pytest.raises(RuntimeError, match="invalid Happy Hours response"):
        await HappyHoursClient(FakeOctopus(_response([event])), "A-1").poll()


@pytest.mark.parametrize("response", [{}, {"savingSessions": {"account": {}}}])
@pytest.mark.asyncio
async def test_missing_booking_data_fails_closed(response):
    with pytest.raises(RuntimeError, match="invalid Happy Hours response"):
        await HappyHoursClient(FakeOctopus(response), "A-1").poll()
