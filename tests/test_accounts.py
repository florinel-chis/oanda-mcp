"""Tests for the account-domain tools.

Every tool gets at least one respx-mocked test asserting the upstream request
(method, path, query/body, auth header) and the trimmed response mapping, plus
an error-path test and the trading-enabled gating of ``configure_account``.
"""

import json

import pytest
import respx
from fastmcp import Client
from fastmcp.exceptions import ToolError
from httpx import Response

from conftest import TEST_ACCOUNT_ID, make_server
from oanda_mcp.tools import accounts

BASE = "https://api-fxpractice.oanda.com"
ACCOUNT_BASE = f"{BASE}/v3/accounts/{TEST_ACCOUNT_ID}"

SUMMARY_FIELDS = {
    "id": TEST_ACCOUNT_ID,
    "alias": "Primary",
    "currency": "EUR",
    "balance": "10000.0000",
    "NAV": "10012.3400",
    "pl": "250.1200",
    "unrealizedPL": "12.3400",
    "marginUsed": "200.0000",
    "marginAvailable": "9812.3400",
    "marginRate": "0.02",
    "openTradeCount": 2,
    "openPositionCount": 1,
    "pendingOrderCount": 3,
    "createdTime": "2020-01-02T03:04:05.000000000Z",
    "lastTransactionID": "6789",
}


@respx.mock
async def test_list_accounts(settings):
    route = respx.get(f"{BASE}/v3/accounts").mock(
        return_value=Response(
            200,
            json={
                "accounts": [
                    {"id": TEST_ACCOUNT_ID, "tags": []},
                    {"id": "001-001-0000001-002", "tags": ["secondary"]},
                ]
            },
        )
    )
    server = make_server(settings, accounts.register)
    async with Client(server) as c:
        result = await c.call_tool("list_accounts", {})
    assert result.data["accounts"][0]["id"] == TEST_ACCOUNT_ID
    assert result.data["accounts"][1]["tags"] == ["secondary"]
    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == "/v3/accounts"
    assert request.headers["Authorization"] == "Bearer test-token"
    assert request.headers["Accept-Datetime-Format"] == "RFC3339"


@respx.mock
async def test_list_accounts_applies_limit(settings):
    respx.get(f"{BASE}/v3/accounts").mock(
        return_value=Response(
            200,
            json={"accounts": [{"id": f"001-001-000000{i}-001", "tags": []} for i in range(5)]},
        )
    )
    server = make_server(settings, accounts.register)
    async with Client(server) as c:
        result = await c.call_tool("list_accounts", {"limit": 2})
    assert len(result.data["accounts"]) == 2


@respx.mock
async def test_get_account_trims_embedded_lists(settings):
    full = dict(SUMMARY_FIELDS)
    full["orders"] = [{"id": "1", "type": "LIMIT"}] * 3
    full["trades"] = [{"id": "2", "instrument": "EUR_USD"}] * 2
    full["positions"] = [{"instrument": "EUR_USD"}]
    route = respx.get(ACCOUNT_BASE).mock(
        return_value=Response(200, json={"account": full, "lastTransactionID": "6789"})
    )
    server = make_server(settings, accounts.register)
    async with Client(server) as c:
        result = await c.call_tool("get_account", {})
    account = result.data["account"]
    assert account["balance"] == "10000.0000"
    assert account["marginRate"] == "0.02"
    assert account["openTradeCount"] == 2
    assert account["pendingOrderCount"] == 3
    assert "orders" not in account
    assert "trades" not in account
    assert "positions" not in account
    assert result.data["lastTransactionID"] == "6789"
    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == f"/v3/accounts/{TEST_ACCOUNT_ID}"
    assert request.headers["Authorization"] == "Bearer test-token"


@respx.mock
async def test_get_account_summary(settings):
    route = respx.get(f"{ACCOUNT_BASE}/summary").mock(
        return_value=Response(
            200, json={"account": dict(SUMMARY_FIELDS), "lastTransactionID": "6789"}
        )
    )
    server = make_server(settings, accounts.register)
    async with Client(server) as c:
        result = await c.call_tool("get_account_summary", {})
    account = result.data["account"]
    assert account["NAV"] == "10012.3400"
    assert account["marginAvailable"] == "9812.3400"
    assert account["alias"] == "Primary"
    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == f"/v3/accounts/{TEST_ACCOUNT_ID}/summary"
    assert request.headers["Authorization"] == "Bearer test-token"
    assert request.headers["Accept-Datetime-Format"] == "RFC3339"


@respx.mock
async def test_list_account_instruments_filters_and_trims(settings):
    route = respx.get(f"{ACCOUNT_BASE}/instruments").mock(
        return_value=Response(
            200,
            json={
                "instruments": [
                    {
                        "name": "EUR_USD",
                        "type": "CURRENCY",
                        "displayName": "EUR/USD",
                        "pipLocation": -4,
                        "displayPrecision": 5,
                        "tradeUnitsPrecision": 0,
                        "minimumTradeSize": "1",
                        "maximumOrderUnits": "100000000",
                        "marginRate": "0.02",
                        "financing": {"longRate": "-0.01", "shortRate": "-0.005"},
                        "tags": [{"type": "ASSET_CLASS", "name": "CURRENCY"}],
                    },
                    {
                        "name": "DE30_EUR",
                        "type": "CFD",
                        "displayName": "Germany 30",
                        "pipLocation": 0,
                        "maximumOrderUnits": "2500",
                        "marginRate": "0.05",
                    },
                ],
                "lastTransactionID": "6789",
            },
        )
    )
    server = make_server(settings, accounts.register)
    async with Client(server) as c:
        result = await c.call_tool(
            "list_account_instruments", {"instruments": ["EUR_USD", "DE30_EUR"]}
        )
    rows = result.data["instruments"]
    assert rows[0] == {
        "name": "EUR_USD",
        "type": "CURRENCY",
        "displayName": "EUR/USD",
        "pipLocation": -4,
        "displayPrecision": 5,
        "tradeUnitsPrecision": 0,
        "minimumTradeSize": "1",
        "marginRate": "0.02",
        "maximumOrderUnits": "100000000",
    }
    assert rows[1]["name"] == "DE30_EUR"
    assert "financing" not in rows[0]
    assert "tags" not in rows[0]
    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == f"/v3/accounts/{TEST_ACCOUNT_ID}/instruments"
    assert request.url.params["instruments"] == "EUR_USD,DE30_EUR"
    assert request.headers["Authorization"] == "Bearer test-token"


@respx.mock
async def test_list_account_instruments_omits_filter_by_default(settings):
    route = respx.get(f"{ACCOUNT_BASE}/instruments").mock(
        return_value=Response(200, json={"instruments": [], "lastTransactionID": "1"})
    )
    server = make_server(settings, accounts.register)
    async with Client(server) as c:
        result = await c.call_tool("list_account_instruments", {})
    assert result.data["instruments"] == []
    assert "instruments" not in route.calls.last.request.url.params


@respx.mock
async def test_get_account_changes(settings):
    route = respx.get(f"{ACCOUNT_BASE}/changes").mock(
        return_value=Response(
            200,
            json={
                "changes": {
                    "ordersFilled": [{"id": "10"}],
                    "tradesOpened": [{"id": "11", "instrument": "EUR_USD"}],
                },
                "state": {"unrealizedPL": "1.2345", "NAV": "10001.2345"},
                "lastTransactionID": "12",
            },
        )
    )
    server = make_server(settings, accounts.register)
    async with Client(server) as c:
        result = await c.call_tool("get_account_changes", {"since_transaction_id": "9"})
    assert result.data["changes"]["tradesOpened"][0]["id"] == "11"
    assert result.data["state"]["NAV"] == "10001.2345"
    assert result.data["lastTransactionID"] == "12"
    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == f"/v3/accounts/{TEST_ACCOUNT_ID}/changes"
    assert request.url.params["sinceTransactionID"] == "9"
    assert request.headers["Authorization"] == "Bearer test-token"


@respx.mock
async def test_configure_account(settings):
    route = respx.patch(f"{ACCOUNT_BASE}/configuration").mock(
        return_value=Response(
            200,
            json={
                "clientConfigureTransaction": {
                    "id": "13",
                    "type": "CLIENT_CONFIGURE",
                    "alias": "Research",
                    "marginRate": "0.05",
                },
                "lastTransactionID": "13",
            },
        )
    )
    server = make_server(settings, accounts.register)
    async with Client(server) as c:
        result = await c.call_tool(
            "configure_account", {"alias": "Research", "margin_rate": "0.05"}
        )
    assert result.data["configuration"]["alias"] == "Research"
    assert result.data["configuration"]["marginRate"] == "0.05"
    assert result.data["lastTransactionID"] == "13"
    request = route.calls.last.request
    assert request.method == "PATCH"
    assert request.url.path == f"/v3/accounts/{TEST_ACCOUNT_ID}/configuration"
    assert json.loads(request.content) == {"alias": "Research", "marginRate": "0.05"}
    assert request.headers["Authorization"] == "Bearer test-token"


@respx.mock
async def test_configure_account_requires_a_field(settings):
    server = make_server(settings, accounts.register)
    async with Client(server) as c:
        with pytest.raises(ToolError, match="at least one of"):
            await c.call_tool("configure_account", {})


@respx.mock
async def test_get_account_summary_api_error_surfaces_message(settings):
    respx.get(f"{ACCOUNT_BASE}/summary").mock(
        return_value=Response(
            403, json={"errorMessage": "Insufficient authorization to perform request."}
        )
    )
    server = make_server(settings, accounts.register)
    async with Client(server) as c:
        with pytest.raises(
            ToolError, match="HTTP 403: Insufficient authorization to perform request."
        ):
            await c.call_tool("get_account_summary", {})


async def test_configure_account_gated_by_enable_trading(settings, settings_no_trading):
    read_tools = {
        "list_accounts",
        "get_account",
        "get_account_summary",
        "list_account_instruments",
        "get_account_changes",
    }

    server = make_server(settings_no_trading, accounts.register)
    async with Client(server) as c:
        names = {tool.name for tool in await c.list_tools()}
    assert "configure_account" not in names
    assert read_tools <= names

    server = make_server(settings, accounts.register)
    async with Client(server) as c:
        names = {tool.name for tool in await c.list_tools()}
    assert "configure_account" in names
    assert read_tools <= names
