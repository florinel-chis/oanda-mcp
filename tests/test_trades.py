"""Tests for the trade-domain tools.

Every tool gets at least one respx-mocked test asserting the upstream request
(method, path, query/body, auth header) and the trimmed response mapping, plus
an error-path test and the trading-enabled gating of ``close_trade`` and
``set_trade_orders``.
"""

import json

import pytest
import respx
from fastmcp import Client
from fastmcp.exceptions import ToolError
from httpx import Response

from conftest import TEST_ACCOUNT_ID, make_server
from oanda_mcp.tools import trades

BASE = "https://api-fxpractice.oanda.com"
ACCOUNT_BASE = f"{BASE}/v3/accounts/{TEST_ACCOUNT_ID}"

OPEN_TRADE = {
    "id": "42",
    "instrument": "EUR_USD",
    "price": "1.10250",
    "openTime": "2026-07-15T08:00:00.000000000Z",
    "state": "OPEN",
    "initialUnits": "100",
    "currentUnits": "100",
    "realizedPL": "0.0000",
    "unrealizedPL": "1.2345",
    "marginUsed": "2.2050",
    "financing": "-0.0100",
    "takeProfitOrder": {
        "id": "43",
        "type": "TAKE_PROFIT",
        "state": "PENDING",
        "price": "1.11000",
        "timeInForce": "GTC",
        "createTime": "2026-07-15T08:00:01.000000000Z",
    },
    "clientExtensions": {"id": "my_trade", "tag": "bot"},
    "dividendAdjustment": "0.0000",
}


@respx.mock
async def test_list_trades(settings):
    route = respx.get(f"{ACCOUNT_BASE}/trades").mock(
        return_value=Response(
            200,
            json={
                "trades": [
                    dict(
                        OPEN_TRADE,
                        id="40",
                        state="CLOSED",
                        currentUnits="0",
                        realizedPL="5.0000",
                        averageClosePrice="1.10750",
                        closeTime="2026-07-15T12:00:00.000000000Z",
                    )
                ],
                "lastTransactionID": "99",
            },
        )
    )
    server = make_server(settings, trades.register)
    async with Client(server) as c:
        result = await c.call_tool(
            "list_trades",
            {
                "instrument": "EUR_USD",
                "state": "CLOSED",
                "before_id": "41",
                "limit": 10,
            },
        )
    trade = result.data["trades"][0]
    assert trade["id"] == "40"
    assert trade["averageClosePrice"] == "1.10750"
    assert trade["closeTime"] == "2026-07-15T12:00:00.000000000Z"
    assert trade["takeProfitOrder"] == {
        "id": "43",
        "type": "TAKE_PROFIT",
        "state": "PENDING",
        "price": "1.11000",
        "timeInForce": "GTC",
    }
    assert "dividendAdjustment" not in trade
    assert result.data["lastTransactionID"] == "99"
    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == f"/v3/accounts/{TEST_ACCOUNT_ID}/trades"
    assert request.url.params["instrument"] == "EUR_USD"
    assert request.url.params["state"] == "CLOSED"
    assert request.url.params["beforeID"] == "41"
    assert request.url.params["count"] == "10"
    assert request.headers["Authorization"] == "Bearer test-token"
    assert request.headers["Accept-Datetime-Format"] == "RFC3339"


@respx.mock
async def test_list_trades_defaults(settings):
    route = respx.get(f"{ACCOUNT_BASE}/trades").mock(
        return_value=Response(200, json={"trades": [], "lastTransactionID": "1"})
    )
    server = make_server(settings, trades.register)
    async with Client(server) as c:
        result = await c.call_tool("list_trades", {})
    assert result.data["trades"] == []
    params = route.calls.last.request.url.params
    assert params["state"] == "OPEN"
    assert params["count"] == "50"
    assert "instrument" not in params
    assert "beforeID" not in params


@respx.mock
async def test_list_open_trades(settings):
    route = respx.get(
        f"https://api-fxpractice.oanda.com/v3/accounts/{TEST_ACCOUNT_ID}/openTrades"
    ).mock(
        return_value=Response(
            200,
            json={
                "trades": [{"id": "42", "instrument": "EUR_USD", "currentUnits": "100"}],
                "lastTransactionID": "7",
            },
        )
    )
    server = make_server(settings, trades.register)
    async with Client(server) as c:
        result = await c.call_tool("list_open_trades", {})
    assert result.data["trades"][0]["id"] == "42"
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer test-token"
    assert request.headers["Accept-Datetime-Format"] == "RFC3339"


@respx.mock
async def test_list_open_trades_trims_and_limits(settings):
    respx.get(f"{ACCOUNT_BASE}/openTrades").mock(
        return_value=Response(
            200,
            json={
                "trades": [dict(OPEN_TRADE, id=str(i)) for i in range(5)],
                "lastTransactionID": "7",
            },
        )
    )
    server = make_server(settings, trades.register)
    async with Client(server) as c:
        result = await c.call_tool("list_open_trades", {"limit": 2})
    assert len(result.data["trades"]) == 2
    trade = result.data["trades"][0]
    assert trade["currentUnits"] == "100"
    assert trade["unrealizedPL"] == "1.2345"
    assert trade["clientExtensions"] == {"id": "my_trade", "tag": "bot"}
    assert "dividendAdjustment" not in trade
    assert "createTime" not in trade["takeProfitOrder"]


@respx.mock
async def test_get_trade(settings):
    route = respx.get(f"{ACCOUNT_BASE}/trades/42").mock(
        return_value=Response(200, json={"trade": OPEN_TRADE, "lastTransactionID": "44"})
    )
    server = make_server(settings, trades.register)
    async with Client(server) as c:
        result = await c.call_tool("get_trade", {"trade_specifier": "42"})
    trade = result.data["trade"]
    assert trade["id"] == "42"
    assert trade["price"] == "1.10250"
    assert trade["initialUnits"] == "100"
    assert trade["takeProfitOrder"]["price"] == "1.11000"
    assert "dividendAdjustment" not in trade
    assert result.data["lastTransactionID"] == "44"
    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == f"/v3/accounts/{TEST_ACCOUNT_ID}/trades/42"
    assert request.headers["Authorization"] == "Bearer test-token"


@respx.mock
async def test_close_trade_all(settings):
    route = respx.put(f"{ACCOUNT_BASE}/trades/42/close").mock(
        return_value=Response(
            200,
            json={
                "orderCreateTransaction": {"id": "100", "type": "MARKET_ORDER"},
                "orderFillTransaction": {
                    "id": "101",
                    "type": "ORDER_FILL",
                    "time": "2026-07-16T09:00:00.000000000Z",
                    "orderID": "100",
                    "instrument": "EUR_USD",
                    "units": "-100",
                    "price": "1.10500",
                    "pl": "2.5000",
                    "financing": "-0.0100",
                    "reason": "MARKET_ORDER_TRADE_CLOSE",
                    "tradesClosed": [{"tradeID": "42", "units": "-100", "realizedPL": "2.5000"}],
                    "fullVWAP": "1.10500",
                    "requestedUnits": "-100",
                },
                "relatedTransactionIDs": ["100", "101"],
                "lastTransactionID": "101",
            },
        )
    )
    server = make_server(settings, trades.register)
    async with Client(server) as c:
        result = await c.call_tool("close_trade", {"trade_specifier": "42"})
    fill = result.data["orderFillTransaction"]
    assert fill["price"] == "1.10500"
    assert fill["pl"] == "2.5000"
    assert fill["tradesClosed"][0]["tradeID"] == "42"
    assert "fullVWAP" not in fill
    assert result.data["orderCancelTransaction"] is None
    assert result.data["lastTransactionID"] == "101"
    request = route.calls.last.request
    assert request.method == "PUT"
    assert request.url.path == f"/v3/accounts/{TEST_ACCOUNT_ID}/trades/42/close"
    assert json.loads(request.content) == {"units": "ALL"}
    assert request.headers["Authorization"] == "Bearer test-token"


@respx.mock
async def test_close_trade_partial_units(settings):
    route = respx.put(f"{ACCOUNT_BASE}/trades/42/close").mock(
        return_value=Response(
            200,
            json={
                "orderFillTransaction": {"id": "102", "units": "-50", "price": "1.10400"},
                "lastTransactionID": "102",
            },
        )
    )
    server = make_server(settings, trades.register)
    async with Client(server) as c:
        result = await c.call_tool("close_trade", {"trade_specifier": "42", "units": "50"})
    assert result.data["orderFillTransaction"]["units"] == "-50"
    assert json.loads(route.calls.last.request.content) == {"units": "50"}


@respx.mock
async def test_close_trade_rejected_surfaces_api_message(settings):
    respx.put(f"{ACCOUNT_BASE}/trades/42/close").mock(
        return_value=Response(
            400,
            json={
                "orderRejectTransaction": {"rejectReason": "CLOSE_TRADE_UNITS_EXCEED"},
                "errorCode": "CLOSE_TRADE_UNITS_EXCEED_TRADE_SIZE",
                "errorMessage": "The units specified exceed the size of the open Trade",
            },
        )
    )
    server = make_server(settings, trades.register)
    async with Client(server) as c:
        with pytest.raises(
            ToolError, match="HTTP 400: The units specified exceed the size of the open Trade"
        ):
            await c.call_tool("close_trade", {"trade_specifier": "42", "units": "500"})


@respx.mock
async def test_set_trade_orders_create_and_cancel(settings):
    route = respx.put(f"{ACCOUNT_BASE}/trades/42/orders").mock(
        return_value=Response(
            200,
            json={
                "takeProfitOrderTransaction": {
                    "id": "103",
                    "type": "TAKE_PROFIT_ORDER",
                    "time": "2026-07-16T09:05:00.000000000Z",
                    "tradeID": "42",
                    "price": "1.12000",
                    "timeInForce": "GTC",
                    "reason": "CLIENT_ORDER",
                    "triggerCondition": "DEFAULT",
                },
                "stopLossOrderCancelTransaction": {
                    "id": "104",
                    "type": "ORDER_CANCEL",
                    "orderID": "90",
                    "reason": "CLIENT_REQUEST",
                },
                "trailingStopLossOrderTransaction": {
                    "id": "105",
                    "type": "TRAILING_STOP_LOSS_ORDER",
                    "tradeID": "42",
                    "distance": "0.0050",
                },
                "relatedTransactionIDs": ["103", "104", "105"],
                "lastTransactionID": "105",
            },
        )
    )
    server = make_server(settings, trades.register)
    async with Client(server) as c:
        result = await c.call_tool(
            "set_trade_orders",
            {
                "trade_specifier": "42",
                "take_profit_price": "1.12000",
                "cancel_stop_loss": True,
                "trailing_stop_loss_distance": "0.0050",
            },
        )
    assert result.data["takeProfitOrderTransaction"]["price"] == "1.12000"
    assert "triggerCondition" not in result.data["takeProfitOrderTransaction"]
    assert result.data["stopLossOrderCancelTransaction"]["orderID"] == "90"
    assert result.data["trailingStopLossOrderTransaction"]["distance"] == "0.0050"
    assert result.data["lastTransactionID"] == "105"
    request = route.calls.last.request
    assert request.method == "PUT"
    assert request.url.path == f"/v3/accounts/{TEST_ACCOUNT_ID}/trades/42/orders"
    assert json.loads(request.content) == {
        "takeProfit": {"price": "1.12000"},
        "stopLoss": None,
        "trailingStopLoss": {"distance": "0.0050"},
    }
    assert request.headers["Authorization"] == "Bearer test-token"


@respx.mock
async def test_set_trade_orders_omits_unspecified_types(settings):
    route = respx.put(f"{ACCOUNT_BASE}/trades/42/orders").mock(
        return_value=Response(
            200,
            json={
                "stopLossOrderTransaction": {"id": "106", "tradeID": "42", "distance": "0.0080"},
                "lastTransactionID": "106",
            },
        )
    )
    server = make_server(settings, trades.register)
    async with Client(server) as c:
        result = await c.call_tool(
            "set_trade_orders",
            {"trade_specifier": "42", "stop_loss_distance": "0.0080"},
        )
    assert result.data["stopLossOrderTransaction"]["distance"] == "0.0080"
    assert json.loads(route.calls.last.request.content) == {"stopLoss": {"distance": "0.0080"}}


@respx.mock
async def test_set_trade_orders_surfaces_immediate_fill(settings):
    """A TP whose price is already crossed fills in the same response and closes the trade."""
    respx.put(f"{ACCOUNT_BASE}/trades/42/orders").mock(
        return_value=Response(
            200,
            json={
                "takeProfitOrderTransaction": {
                    "id": "107",
                    "type": "TAKE_PROFIT_ORDER",
                    "tradeID": "42",
                    "price": "1.09500",
                },
                "takeProfitOrderFillTransaction": {
                    "id": "108",
                    "type": "ORDER_FILL",
                    "orderID": "107",
                    "instrument": "EUR_USD",
                    "units": "-100",
                    "price": "1.10000",
                    "pl": "5.0000",
                    "reason": "TAKE_PROFIT_ORDER",
                    "tradesClosed": [{"tradeID": "42", "units": "-100", "realizedPL": "5.0000"}],
                    "requestID": "internal-bookkeeping",
                },
                "lastTransactionID": "108",
            },
        )
    )
    server = make_server(settings, trades.register)
    async with Client(server) as c:
        result = await c.call_tool(
            "set_trade_orders",
            {"trade_specifier": "42", "take_profit_price": "1.09500"},
        )
    fill = result.data["takeProfitOrderFillTransaction"]
    assert fill["pl"] == "5.0000"
    assert fill["tradesClosed"][0]["tradeID"] == "42"
    assert "requestID" not in fill
    assert result.data["takeProfitOrderTransaction"]["price"] == "1.09500"
    assert result.data["lastTransactionID"] == "108"


@respx.mock
async def test_set_trade_orders_rejects_price_and_distance_together(settings):
    server = make_server(settings, trades.register)
    async with Client(server) as c:
        with pytest.raises(ToolError, match="exactly one of"):
            await c.call_tool(
                "set_trade_orders",
                {
                    "trade_specifier": "42",
                    "stop_loss_price": "1.09000",
                    "stop_loss_distance": "0.0050",
                },
            )


@respx.mock
async def test_set_trade_orders_rejects_value_with_cancel_flag(settings):
    server = make_server(settings, trades.register)
    async with Client(server) as c:
        with pytest.raises(ToolError, match="cannot be combined"):
            await c.call_tool(
                "set_trade_orders",
                {
                    "trade_specifier": "42",
                    "take_profit_price": "1.12000",
                    "cancel_take_profit": True,
                },
            )


@respx.mock
async def test_set_trade_orders_requires_a_change(settings):
    server = make_server(settings, trades.register)
    async with Client(server) as c:
        with pytest.raises(ToolError, match="no changes requested"):
            await c.call_tool("set_trade_orders", {"trade_specifier": "42"})


async def test_write_tools_gated_by_enable_trading(settings, settings_no_trading):
    read_tools = {"list_trades", "list_open_trades", "get_trade"}
    write_tools = {"close_trade", "set_trade_orders"}

    server = make_server(settings_no_trading, trades.register)
    async with Client(server) as c:
        names = {tool.name for tool in await c.list_tools()}
    assert not (write_tools & names)
    assert read_tools <= names

    server = make_server(settings, trades.register)
    async with Client(server) as c:
        names = {tool.name for tool in await c.list_tools()}
    assert write_tools <= names
    assert read_tools <= names
