import json
import logging

import httpx
import pytest

from battery_automation.main import _setup_logging
from battery_automation.octopus import BACKEND_GRAPHQL_URL, GRAPHQL_URL, OctopusClient


def _token_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={"data": {"obtainKrakenToken": {"token": "jwt"}}},
    )


def _client(handler) -> OctopusClient:
    client = OctopusClient("api-key", "A-1")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=15)
    return client


@pytest.mark.asyncio
async def test_backend_success_log_is_suppressed_but_public_endpoint_remains_visible(
    caplog,
):
    def handler(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]
        if "obtainKrakenToken" in query:
            return _token_response()
        if str(request.url) == GRAPHQL_URL:
            return httpx.Response(200, json={"data": {"plannedDispatches": []}})
        return httpx.Response(
            200,
            json={"data": {"savingSessions": {"account": {"joinedEvents": []}}}},
        )

    _setup_logging("INFO")
    client = _client(handler)
    try:
        with caplog.at_level(logging.INFO, logger="httpx"):
            await client.planned_dispatches()
            await client.post_backend_authed("query HappyHours { joinedEvents }", {})

        request_logs = [record.getMessage() for record in caplog.records if record.name == "httpx"]
        assert any(GRAPHQL_URL in message for message in request_logs)
        assert not any(BACKEND_GRAPHQL_URL in message for message in request_logs)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_backend_http_failure_log_remains_visible(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        if "obtainKrakenToken" in json.loads(request.content)["query"]:
            return _token_response()
        return httpx.Response(502, text="upstream failure")

    _setup_logging("INFO")
    client = _client(handler)
    try:
        with (
            caplog.at_level(logging.INFO, logger="httpx"),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await client.post_backend_authed("query HappyHours { joinedEvents }", {})

        assert any(
            record.name == "httpx"
            and BACKEND_GRAPHQL_URL in record.getMessage()
            and "502" in record.getMessage()
            for record in caplog.records
        )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_graphql_error_on_http_200_still_propagates(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        if "obtainKrakenToken" in json.loads(request.content)["query"]:
            return _token_response()
        return httpx.Response(200, json={"errors": [{"message": "bad query"}]})

    _setup_logging("INFO")
    client = _client(handler)
    try:
        with (
            caplog.at_level(logging.INFO, logger="httpx"),
            pytest.raises(RuntimeError, match="octopus query error"),
        ):
            await client.post_backend_authed("query HappyHours { joinedEvents }", {})

        assert not any(
            record.name == "httpx" and BACKEND_GRAPHQL_URL in record.getMessage()
            for record in caplog.records
        )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_debug_logging_keeps_backend_success_log_visible(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        if "obtainKrakenToken" in json.loads(request.content)["query"]:
            return _token_response()
        return httpx.Response(
            200,
            json={"data": {"savingSessions": {"account": {"joinedEvents": []}}}},
        )

    _setup_logging("DEBUG")
    client = _client(handler)
    try:
        with caplog.at_level(logging.INFO, logger="httpx"):
            await client.post_backend_authed("query HappyHours { joinedEvents }", {})

        assert any(
            record.name == "httpx" and BACKEND_GRAPHQL_URL in record.getMessage()
            for record in caplog.records
        )
    finally:
        await client.aclose()
