import json

import httpx
import pytest

from battery_automation.octopus import OctopusClient


def _token_response(token: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": {
                "obtainKrakenToken": {
                    "token": token,
                    "refreshToken": "r",
                    "refreshExpiresIn": 3600,
                }
            }
        },
    )


@pytest.mark.asyncio
async def test_planned_dispatches_parses_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "obtainKrakenToken" in body["query"]:
            return _token_response("jwt-1")
        return httpx.Response(
            200,
            json={
                "data": {
                    "plannedDispatches": [
                        {
                            "startDt": "2026-04-25T14:00:00+00:00",
                            "endDt": "2026-04-25T15:00:00+00:00",
                            "delta": "-3.0",
                            "meta": {"source": "smart-charge", "location": "AT_HOME"},
                        }
                    ]
                }
            },
        )

    client = OctopusClient("apikey", "A-1")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=15)

    [d] = await client.planned_dispatches()
    assert d.start.year == 2026 and d.start.hour == 14
    assert d.delta == "-3.0"
    assert d.source == "smart-charge"
    assert d.location == "AT_HOME"

    await client.aclose()


@pytest.mark.asyncio
async def test_iso_z_suffix_accepted():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "obtainKrakenToken" in body["query"]:
            return _token_response("jwt-1")
        return httpx.Response(
            200,
            json={
                "data": {
                    "plannedDispatches": [
                        {
                            "startDt": "2026-04-25T14:00:00Z",
                            "endDt": "2026-04-25T15:00:00Z",
                            "delta": None,
                            "meta": None,
                        }
                    ]
                }
            },
        )

    client = OctopusClient("k", "A-1")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=15)

    [d] = await client.planned_dispatches()
    assert d.start.tzinfo is not None
    assert d.source is None

    await client.aclose()


@pytest.mark.asyncio
async def test_401_refreshes_token_and_retries_once():
    state = {"token_calls": 0, "dispatch_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "obtainKrakenToken" in body["query"]:
            state["token_calls"] += 1
            return _token_response(f"jwt-{state['token_calls']}")
        # plannedDispatches
        state["dispatch_calls"] += 1
        if state["dispatch_calls"] == 1:
            assert request.headers["Authorization"] == "jwt-1"
            return httpx.Response(401, json={"errors": [{"message": "expired"}]})
        # second attempt should carry the freshly-minted token
        assert request.headers["Authorization"] == "jwt-2"
        return httpx.Response(200, json={"data": {"plannedDispatches": []}})

    client = OctopusClient("apikey", "A-1")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=15)

    dispatches = await client.planned_dispatches()
    assert dispatches == []
    assert state["token_calls"] == 2  # initial + post-401 refresh
    assert state["dispatch_calls"] == 2  # 401 + retry

    await client.aclose()


@pytest.mark.asyncio
async def test_persistent_401_propagates():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "obtainKrakenToken" in body["query"]:
            return _token_response("jwt")
        return httpx.Response(401, json={"errors": [{"message": "nope"}]})

    client = OctopusClient("k", "A-1")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=15)

    with pytest.raises(httpx.HTTPStatusError):
        await client.planned_dispatches()

    await client.aclose()


@pytest.mark.asyncio
async def test_expired_jwt_graphql_error_refreshes_and_retries():
    """Kraken returns expired-JWT as HTTP 200 + errorCode KT-CT-1124, not 401."""
    state = {"token_calls": 0, "dispatch_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "obtainKrakenToken" in body["query"]:
            state["token_calls"] += 1
            return _token_response(f"jwt-{state['token_calls']}")
        state["dispatch_calls"] += 1
        if state["dispatch_calls"] == 1:
            assert request.headers["Authorization"] == "jwt-1"
            return httpx.Response(
                200,
                json={
                    "errors": [
                        {
                            "message": "Signature of the JWT has expired.",
                            "path": ["plannedDispatches"],
                            "extensions": {
                                "errorType": "APPLICATION",
                                "errorCode": "KT-CT-1124",
                            },
                        }
                    ]
                },
            )
        assert request.headers["Authorization"] == "jwt-2"
        return httpx.Response(200, json={"data": {"plannedDispatches": []}})

    client = OctopusClient("apikey", "A-1")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=15)

    assert await client.planned_dispatches() == []
    assert state["token_calls"] == 2
    assert state["dispatch_calls"] == 2

    await client.aclose()


@pytest.mark.asyncio
async def test_persistent_expired_jwt_propagates_after_one_retry():
    state = {"token_calls": 0, "dispatch_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "obtainKrakenToken" in body["query"]:
            state["token_calls"] += 1
            return _token_response(f"jwt-{state['token_calls']}")
        state["dispatch_calls"] += 1
        return httpx.Response(
            200,
            json={
                "errors": [
                    {
                        "message": "Signature of the JWT has expired.",
                        "extensions": {"errorCode": "KT-CT-1124"},
                    }
                ]
            },
        )

    client = OctopusClient("k", "A-1")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=15)

    with pytest.raises(RuntimeError, match="octopus query error"):
        await client.planned_dispatches()
    # one refresh attempt, then give up
    assert state["token_calls"] == 2
    assert state["dispatch_calls"] == 2

    await client.aclose()


@pytest.mark.asyncio
async def test_graphql_errors_raise():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "obtainKrakenToken" in body["query"]:
            return _token_response("jwt")
        return httpx.Response(200, json={"errors": [{"message": "bad query"}]})

    client = OctopusClient("k", "A-1")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=15)

    with pytest.raises(RuntimeError, match="octopus query error"):
        await client.planned_dispatches()

    await client.aclose()
