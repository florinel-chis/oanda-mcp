"""Tests for the async Oanda API client."""

import json

import httpx
import pytest
import respx
from fastmcp.exceptions import ToolError
from httpx import Response

from conftest import TEST_ACCOUNT_ID
from oanda_mcp.client import ApiClient, _retry_after_seconds
from oanda_mcp.config import Settings

BASE = "https://api-fxpractice.oanda.com"


@pytest.fixture
async def client(settings: Settings) -> ApiClient:
    api = ApiClient(settings)
    yield api
    await api.aclose()


@respx.mock
async def test_request_success_sends_auth_and_datetime_headers(client: ApiClient) -> None:
    route = respx.get(f"{BASE}/v3/accounts/{TEST_ACCOUNT_ID}/summary").mock(
        return_value=Response(200, json={"account": {"balance": "1000.0"}})
    )

    result = await client.request("GET", f"/v3/accounts/{TEST_ACCOUNT_ID}/summary")

    assert result == {"account": {"balance": "1000.0"}}
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer test-token"
    assert request.headers["Accept-Datetime-Format"] == "RFC3339"


@respx.mock
async def test_request_strips_none_params(client: ApiClient) -> None:
    route = respx.get(f"{BASE}/v3/accounts/{TEST_ACCOUNT_ID}/orders").mock(
        return_value=Response(200, json={"orders": []})
    )

    await client.request(
        "GET",
        f"/v3/accounts/{TEST_ACCOUNT_ID}/orders",
        params={"count": 50, "state": None, "instrument": None},
    )

    sent = route.calls.last.request.url.params
    assert sent["count"] == "50"
    assert "state" not in sent
    assert "instrument" not in sent


@respx.mock
async def test_request_sends_json_body(client: ApiClient) -> None:
    route = respx.put(f"{BASE}/v3/accounts/{TEST_ACCOUNT_ID}/trades/42/close").mock(
        return_value=Response(200, json={"lastTransactionID": "9"})
    )

    await client.request(
        "PUT",
        f"/v3/accounts/{TEST_ACCOUNT_ID}/trades/42/close",
        json_body={"units": "ALL"},
    )

    assert json.loads(route.calls.last.request.content) == {"units": "ALL"}


@respx.mock
async def test_request_returns_none_for_empty_body(client: ApiClient) -> None:
    respx.get(f"{BASE}/v3/accounts").mock(return_value=Response(204))

    assert await client.request("GET", "/v3/accounts") is None


@respx.mock
async def test_201_is_success(client: ApiClient) -> None:
    respx.post(f"{BASE}/v3/accounts/{TEST_ACCOUNT_ID}/orders").mock(
        return_value=Response(201, json={"orderCreateTransaction": {"id": "3"}})
    )

    result = await client.request(
        "POST",
        f"/v3/accounts/{TEST_ACCOUNT_ID}/orders",
        json_body={"order": {"type": "MARKET", "instrument": "EUR_USD", "units": "100"}},
    )

    assert result["orderCreateTransaction"]["id"] == "3"


@respx.mock
async def test_error_maps_api_message_to_tool_error(client: ApiClient) -> None:
    respx.get(f"{BASE}/v3/instruments/BOGUS/candles").mock(
        return_value=Response(
            400, json={"errorMessage": "Invalid value specified for 'instrument'"}
        )
    )

    with pytest.raises(ToolError, match="HTTP 400: Invalid value specified for 'instrument'"):
        await client.request("GET", "/v3/instruments/BOGUS/candles")


@respx.mock
async def test_error_without_json_body_falls_back_to_generic_text(client: ApiClient) -> None:
    respx.get(f"{BASE}/v3/accounts").mock(return_value=Response(502, text="<html>bad</html>"))

    with pytest.raises(ToolError) as excinfo:
        await client.request("GET", "/v3/accounts")

    message = str(excinfo.value)
    assert message.startswith("HTTP 502:")
    assert "test-token" not in message
    assert "Authorization" not in message


@respx.mock
async def test_429_retries_once_and_succeeds(client: ApiClient) -> None:
    route = respx.get(f"{BASE}/v3/accounts").mock(
        side_effect=[
            Response(429, headers={"Retry-After": "0"}),
            Response(200, json={"accounts": []}),
        ]
    )

    result = await client.request("GET", "/v3/accounts")

    assert result == {"accounts": []}
    assert route.call_count == 2


@respx.mock
async def test_429_twice_raises_after_single_retry(client: ApiClient) -> None:
    route = respx.get(f"{BASE}/v3/accounts").mock(
        side_effect=[
            Response(429, headers={"Retry-After": "0"}),
            Response(429, headers={"Retry-After": "0"}, json={"errorMessage": "rate limited"}),
        ]
    )

    with pytest.raises(ToolError, match="HTTP 429: rate limited"):
        await client.request("GET", "/v3/accounts")

    assert route.call_count == 2


def test_retry_after_parsing() -> None:
    assert _retry_after_seconds(httpx.Response(429)) == 2.0
    assert _retry_after_seconds(httpx.Response(429, headers={"Retry-After": "5"})) == 5.0
    assert _retry_after_seconds(httpx.Response(429, headers={"Retry-After": "0"})) == 0.0
    assert _retry_after_seconds(httpx.Response(429, headers={"Retry-After": "soon"})) == 2.0
    assert _retry_after_seconds(httpx.Response(429, headers={"Retry-After": "-3"})) == 2.0


def test_retry_after_rejects_non_finite_and_huge_values() -> None:
    # A header from a misbehaving proxy/CDN must never hang the tool call:
    # float() accepts "inf"/"nan", and "1e309" overflows to inf.
    for raw in ("inf", "Infinity", "nan", "1e309"):
        assert _retry_after_seconds(httpx.Response(429, headers={"Retry-After": raw})) == 2.0
    assert _retry_after_seconds(httpx.Response(429, headers={"Retry-After": "9999"})) == 30.0


async def test_account_id_returns_configured_value_without_requests(client: ApiClient) -> None:
    # No respx mock is active: any HTTP request would raise immediately.
    assert await client.account_id() == TEST_ACCOUNT_ID


@respx.mock
async def test_account_id_discovers_first_account_and_caches() -> None:
    api = ApiClient(Settings(api_token="test-token"))
    route = respx.get(f"{BASE}/v3/accounts").mock(
        return_value=Response(200, json={"accounts": [{"id": "101-004-0000042-001"}]})
    )

    assert await api.account_id() == "101-004-0000042-001"
    assert await api.account_id() == "101-004-0000042-001"
    assert route.call_count == 1
    await api.aclose()


@respx.mock
async def test_account_id_with_no_accounts_raises() -> None:
    api = ApiClient(Settings(api_token="test-token"))
    respx.get(f"{BASE}/v3/accounts").mock(return_value=Response(200, json={"accounts": []}))

    with pytest.raises(ToolError, match="no accounts"):
        await api.account_id()
    await api.aclose()
