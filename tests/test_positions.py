"""Tests for the position-domain tools.

Every tool gets at least one respx-mocked test asserting the upstream request
(method, path, query/body, auth header) and the trimmed response mapping, plus
an error-path test and the trading-enabled gating of ``close_position``.
"""

import json

import pytest
import respx
from fastmcp import Client
from fastmcp.exceptions import ToolError
from httpx import Response

from conftest import TEST_ACCOUNT_ID, make_server
from oanda_mcp.tools import positions

BASE = "https://api-fxpractice.oanda.com"
ACCOUNT_BASE = f"{BASE}/v3/accounts/{TEST_ACCOUNT_ID}"

EUR_USD_POSITION = {
    "instrument": "EUR_USD",
    "pl": "127.5000",
    "unrealizedPL": "3.2100",
    "marginUsed": "22.0000",
    "financing": "-1.2500",
    "commission": "0.0000",
    "guaranteedExecutionFees": "0.0000",
    "resettablePL": "127.5000",
    "long": {
        "units": "1000",
        "averagePrice": "1.10250",
        "tradeIDs": ["42", "43"],
        "pl": "127.5000",
        "unrealizedPL": "3.2100",
        "resettablePL": "127.5000",
        "financing": "-1.2500",
        "guaranteedExecutionFees": "0.0000",
    },
    "short": {
        "units": "0",
        "pl": "0.0000",
        "unrealizedPL": "0.0000",
        "resettablePL": "0.0000",
        "financing": "0.0000",
        "guaranteedExecutionFees": "0.0000",
    },
}

FLAT_POSITION = {
    "instrument": "EU50_EUR",
    "pl": "-14.0000",
    "unrealizedPL": "0.0000",
    "marginUsed": "0.0000",
    "financing": "-0.3000",
    "long": {"units": "0", "pl": "-14.0000", "unrealizedPL": "0.0000", "tradeIDs": []},
    "short": {"units": "0", "pl": "0.0000", "unrealizedPL": "0.0000"},
}


@respx.mock
async def test_list_positions(settings):
    route = respx.get(f"{ACCOUNT_BASE}/positions").mock(
        return_value=Response(
            200,
            json={
                "positions": [EUR_USD_POSITION, FLAT_POSITION],
                "lastTransactionID": "99",
            },
        )
    )
    server = make_server(settings, positions.register)
    async with Client(server) as c:
        result = await c.call_tool("list_positions", {})
    rows = result.data["positions"]
    assert len(rows) == 2
    assert rows[0]["instrument"] == "EUR_USD"
    assert rows[0]["pl"] == "127.5000"
    assert rows[0]["long"] == {
        "units": "1000",
        "averagePrice": "1.10250",
        "tradeIDs": ["42", "43"],
        "pl": "127.5000",
        "unrealizedPL": "3.2100",
    }
    assert rows[0]["short"]["units"] == "0"
    assert "guaranteedExecutionFees" not in rows[0]
    assert "resettablePL" not in rows[0]["long"]
    assert rows[1]["instrument"] == "EU50_EUR"
    assert result.data["lastTransactionID"] == "99"
    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == f"/v3/accounts/{TEST_ACCOUNT_ID}/positions"
    assert request.headers["Authorization"] == "Bearer test-token"
    assert request.headers["Accept-Datetime-Format"] == "RFC3339"


@respx.mock
async def test_list_positions_applies_limit(settings):
    respx.get(f"{ACCOUNT_BASE}/positions").mock(
        return_value=Response(
            200,
            json={
                "positions": [dict(FLAT_POSITION, instrument=f"PAIR_{i}") for i in range(5)],
                "lastTransactionID": "1",
            },
        )
    )
    server = make_server(settings, positions.register)
    async with Client(server) as c:
        result = await c.call_tool("list_positions", {"limit": 2})
    assert len(result.data["positions"]) == 2


@respx.mock
async def test_list_open_positions(settings):
    route = respx.get(f"{ACCOUNT_BASE}/openPositions").mock(
        return_value=Response(
            200,
            json={"positions": [EUR_USD_POSITION], "lastTransactionID": "100"},
        )
    )
    server = make_server(settings, positions.register)
    async with Client(server) as c:
        result = await c.call_tool("list_open_positions", {})
    rows = result.data["positions"]
    assert len(rows) == 1
    assert rows[0]["instrument"] == "EUR_USD"
    assert rows[0]["long"]["averagePrice"] == "1.10250"
    assert rows[0]["marginUsed"] == "22.0000"
    assert result.data["lastTransactionID"] == "100"
    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == f"/v3/accounts/{TEST_ACCOUNT_ID}/openPositions"
    assert request.headers["Authorization"] == "Bearer test-token"
    assert request.headers["Accept-Datetime-Format"] == "RFC3339"


@respx.mock
async def test_get_position(settings):
    route = respx.get(f"{ACCOUNT_BASE}/positions/EUR_USD").mock(
        return_value=Response(
            200,
            json={"position": EUR_USD_POSITION, "lastTransactionID": "101"},
        )
    )
    server = make_server(settings, positions.register)
    async with Client(server) as c:
        result = await c.call_tool("get_position", {"instrument": "EUR_USD"})
    position = result.data["position"]
    assert position["instrument"] == "EUR_USD"
    assert position["unrealizedPL"] == "3.2100"
    assert position["financing"] == "-1.2500"
    assert position["long"]["tradeIDs"] == ["42", "43"]
    assert "commission" not in position
    assert result.data["lastTransactionID"] == "101"
    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == f"/v3/accounts/{TEST_ACCOUNT_ID}/positions/EUR_USD"
    assert request.headers["Authorization"] == "Bearer test-token"


@respx.mock
async def test_get_position_api_error_surfaces_message(settings):
    respx.get(f"{ACCOUNT_BASE}/positions/XX_YY").mock(
        return_value=Response(404, json={"errorMessage": "The position specified does not exist"})
    )
    server = make_server(settings, positions.register)
    async with Client(server) as c:
        with pytest.raises(ToolError, match="HTTP 404: The position specified does not exist"):
            await c.call_tool("get_position", {"instrument": "XX_YY"})


@respx.mock
async def test_close_position_long_only_protects_short_side(settings):
    route = respx.put(f"{ACCOUNT_BASE}/positions/EUR_USD/close").mock(
        return_value=Response(
            200,
            json={
                "longOrderCreateTransaction": {"id": "102", "type": "MARKET_ORDER"},
                "longOrderFillTransaction": {
                    "id": "103",
                    "orderID": "102",
                    "instrument": "EUR_USD",
                    "units": "-1000",
                    "price": "1.10300",
                    "pl": "0.5000",
                    "financing": "-0.0100",
                    "reason": "MARKET_ORDER_POSITION_CLOSEOUT",
                    "time": "2026-07-16T12:00:00.000000000Z",
                    "fullVWAP": "1.10300",
                    "accountBalance": "10000.5000",
                    "tradesClosed": [{"tradeID": "42", "units": "-1000"}],
                },
                "relatedTransactionIDs": ["102", "103"],
                "lastTransactionID": "103",
            },
        )
    )
    server = make_server(settings, positions.register)
    async with Client(server) as c:
        result = await c.call_tool("close_position", {"instrument": "EUR_USD", "long_units": "ALL"})
    assert result.data["longOrderFill"] == {
        "id": "103",
        "orderID": "102",
        "instrument": "EUR_USD",
        "units": "-1000",
        "price": "1.10300",
        "pl": "0.5000",
        "financing": "-0.0100",
        "reason": "MARKET_ORDER_POSITION_CLOSEOUT",
        "time": "2026-07-16T12:00:00.000000000Z",
    }
    assert result.data["longOrderCancel"] is None
    assert result.data["shortOrderFill"] is None
    assert result.data["lastTransactionID"] == "103"
    request = route.calls.last.request
    assert request.method == "PUT"
    assert request.url.path == f"/v3/accounts/{TEST_ACCOUNT_ID}/positions/EUR_USD/close"
    # The unspecified side must be pinned to NONE: the API would otherwise
    # default it to ALL and close it too.
    assert json.loads(request.content) == {"longUnits": "ALL", "shortUnits": "NONE"}
    assert request.headers["Authorization"] == "Bearer test-token"


@respx.mock
async def test_close_position_partial_short(settings):
    route = respx.put(f"{ACCOUNT_BASE}/positions/EU50_EUR/close").mock(
        return_value=Response(
            200,
            json={
                "shortOrderFillTransaction": {
                    "id": "105",
                    "orderID": "104",
                    "instrument": "EU50_EUR",
                    "units": "50",
                    "price": "5210.0",
                    "pl": "-3.0000",
                    "reason": "MARKET_ORDER_POSITION_CLOSEOUT",
                    "time": "2026-07-16T12:05:00.000000000Z",
                },
                "lastTransactionID": "105",
            },
        )
    )
    server = make_server(settings, positions.register)
    async with Client(server) as c:
        result = await c.call_tool(
            "close_position", {"instrument": "EU50_EUR", "short_units": "50"}
        )
    assert result.data["shortOrderFill"]["units"] == "50"
    assert result.data["shortOrderFill"]["pl"] == "-3.0000"
    assert result.data["longOrderFill"] is None
    assert json.loads(route.calls.last.request.content) == {
        "longUnits": "NONE",
        "shortUnits": "50",
    }


@respx.mock
async def test_close_position_requires_a_side(settings):
    server = make_server(settings, positions.register)
    async with Client(server) as c:
        with pytest.raises(ToolError, match="at least one of"):
            await c.call_tool("close_position", {"instrument": "EUR_USD"})


@respx.mock
async def test_close_position_rejects_invalid_units(settings):
    server = make_server(settings, positions.register)
    async with Client(server) as c:
        with pytest.raises(ToolError, match="long_units must be"):
            await c.call_tool(
                "close_position", {"instrument": "EUR_USD", "long_units": "everything"}
            )
        with pytest.raises(ToolError, match="short_units must be"):
            await c.call_tool("close_position", {"instrument": "EUR_USD", "short_units": "-100"})


@respx.mock
async def test_close_position_api_reject_surfaces_message(settings):
    respx.put(f"{ACCOUNT_BASE}/positions/EUR_USD/close").mock(
        return_value=Response(
            400,
            json={
                "errorCode": "CLOSEOUT_POSITION_DOESNT_EXIST",
                "errorMessage": "The Position requested to be closed out does not exist",
                "longOrderRejectTransaction": {"id": "106", "type": "MARKET_ORDER_REJECT"},
            },
        )
    )
    server = make_server(settings, positions.register)
    async with Client(server) as c:
        with pytest.raises(
            ToolError, match="HTTP 400: The Position requested to be closed out does not exist"
        ):
            await c.call_tool("close_position", {"instrument": "EUR_USD", "long_units": "ALL"})


async def test_close_position_gated_by_enable_trading(settings, settings_no_trading):
    read_tools = {"list_positions", "list_open_positions", "get_position"}

    server = make_server(settings_no_trading, positions.register)
    async with Client(server) as c:
        names = {tool.name for tool in await c.list_tools()}
    assert "close_position" not in names
    assert read_tools <= names

    server = make_server(settings, positions.register)
    async with Client(server) as c:
        names = {tool.name for tool in await c.list_tools()}
    assert "close_position" in names
    assert read_tools <= names
