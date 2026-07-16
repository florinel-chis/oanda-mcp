"""Order-domain tool tests: request shapes, response trimming, and gating."""

import json

import pytest
import respx
from fastmcp import Client
from fastmcp.exceptions import ToolError
from httpx import Response

from conftest import TEST_ACCOUNT_ID, make_server
from oanda_mcp.tools import orders

BASE = f"https://api-fxpractice.oanda.com/v3/accounts/{TEST_ACCOUNT_ID}"

READ_TOOLS = {"list_orders", "list_pending_orders", "get_order"}
WRITE_TOOLS = {"create_order", "replace_order", "cancel_order"}


def _request_body(route: respx.Route) -> dict:
    return json.loads(route.calls.last.request.content)


@respx.mock
async def test_list_orders_passes_filters(settings):
    route = respx.get(f"{BASE}/orders").mock(
        return_value=Response(
            200,
            json={
                "orders": [
                    {
                        "id": "6372",
                        "type": "LIMIT",
                        "instrument": "EUR_USD",
                        "units": "100",
                        "price": "1.09000",
                        "state": "FILLED",
                        "timeInForce": "GTC",
                        "createTime": "2026-07-01T10:00:00.000000000Z",
                        "clientExtensions": {"id": "noise", "comment": "dropped"},
                    }
                ],
                "lastTransactionID": "6375",
            },
        )
    )
    server = make_server(settings, orders.register)
    async with Client(server) as c:
        result = await c.call_tool(
            "list_orders",
            {"instrument": "EUR_USD", "state": "FILLED", "limit": 10, "before_id": "7000"},
        )

    order = result.data["orders"][0]
    assert order["id"] == "6372"
    assert order["price"] == "1.09000"
    assert "clientExtensions" not in order
    assert result.data["lastTransactionID"] == "6375"

    request = route.calls.last.request
    assert request.method == "GET"
    assert request.headers["Authorization"] == "Bearer test-token"
    assert request.headers["Accept-Datetime-Format"] == "RFC3339"
    assert request.url.params["instrument"] == "EUR_USD"
    assert request.url.params["state"] == "FILLED"
    assert request.url.params["count"] == "10"
    assert request.url.params["beforeID"] == "7000"


@respx.mock
async def test_list_orders_omits_unset_filters(settings):
    route = respx.get(f"{BASE}/orders").mock(
        return_value=Response(200, json={"orders": [], "lastTransactionID": "1"})
    )
    server = make_server(settings, orders.register)
    async with Client(server) as c:
        result = await c.call_tool("list_orders", {})

    assert result.data["orders"] == []
    params = route.calls.last.request.url.params
    assert "instrument" not in params
    assert "beforeID" not in params
    assert params["state"] == "PENDING"
    assert params["count"] == "50"


@respx.mock
async def test_list_pending_orders(settings):
    route = respx.get(f"{BASE}/pendingOrders").mock(
        return_value=Response(
            200,
            json={
                "orders": [
                    {"id": "10", "type": "STOP", "instrument": "USD_JPY", "state": "PENDING"},
                    {"id": "11", "type": "LIMIT", "instrument": "EUR_USD", "state": "PENDING"},
                ],
                "lastTransactionID": "12",
            },
        )
    )
    server = make_server(settings, orders.register)
    async with Client(server) as c:
        result = await c.call_tool("list_pending_orders", {"limit": 1})

    assert len(result.data["orders"]) == 1
    assert result.data["orders"][0]["id"] == "10"

    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == f"/v3/accounts/{TEST_ACCOUNT_ID}/pendingOrders"
    assert request.headers["Authorization"] == "Bearer test-token"


@respx.mock
async def test_get_order(settings):
    route = respx.get(f"{BASE}/orders/6372").mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": "6372",
                    "type": "MARKET_IF_TOUCHED",
                    "instrument": "EUR_USD",
                    "units": "-200",
                    "price": "1.12000",
                    "state": "PENDING",
                    "timeInForce": "GTC",
                },
                "lastTransactionID": "6380",
            },
        )
    )
    server = make_server(settings, orders.register)
    async with Client(server) as c:
        result = await c.call_tool("get_order", {"order_specifier": "6372"})

    assert result.data["order"]["units"] == "-200"
    assert result.data["order"]["state"] == "PENDING"
    assert result.data["lastTransactionID"] == "6380"

    request = route.calls.last.request
    assert request.method == "GET"
    assert request.headers["Authorization"] == "Bearer test-token"


@respx.mock
async def test_get_order_percent_encodes_specifier_path_segment(settings):
    """Reserved characters in a specifier must not retarget the request path."""
    route = respx.get(url__regex=r".*/v3/accounts/.*/orders/6372%2Fcancel$").mock(
        return_value=Response(
            200,
            json={"order": {"id": "6372", "state": "PENDING"}, "lastTransactionID": "1"},
        )
    )
    server = make_server(settings, orders.register)
    async with Client(server) as c:
        result = await c.call_tool("get_order", {"order_specifier": "6372/cancel"})
    assert route.called
    assert result.data["order"]["state"] == "PENDING"
    assert route.calls.last.request.url.raw_path.endswith(b"/orders/6372%2Fcancel")


@respx.mock
async def test_get_order_api_error_surfaces_message(settings):
    respx.get(f"{BASE}/orders/9999").mock(
        return_value=Response(404, json={"errorMessage": "The Order specified does not exist"})
    )
    server = make_server(settings, orders.register)
    async with Client(server) as c:
        with pytest.raises(ToolError, match="HTTP 404: The Order specified does not exist"):
            await c.call_tool("get_order", {"order_specifier": "9999"})


@respx.mock
async def test_create_market_order_returns_fill(settings):
    route = respx.post(f"{BASE}/orders").mock(
        return_value=Response(
            201,
            json={
                "orderCreateTransaction": {
                    "id": "6383",
                    "type": "MARKET_ORDER",
                    "instrument": "EUR_USD",
                    "units": "100",
                    "timeInForce": "FOK",
                    "reason": "CLIENT_ORDER",
                },
                "orderFillTransaction": {
                    "id": "6384",
                    "orderID": "6383",
                    "instrument": "EUR_USD",
                    "units": "100",
                    "price": "1.10012",
                    "tradeOpened": {"tradeID": "6384", "units": "100", "price": "1.10012"},
                },
                "relatedTransactionIDs": ["6383", "6384"],
                "lastTransactionID": "6384",
            },
        )
    )
    server = make_server(settings, orders.register)
    async with Client(server) as c:
        result = await c.call_tool(
            "create_order",
            {
                "order_type": "MARKET",
                "instrument": "EUR_USD",
                "units": "100",
                "take_profit_price": "1.12000",
                "trailing_stop_distance": "0.00500",
            },
        )

    assert result.data["order_fill"]["tradeOpened"]["tradeID"] == "6384"
    assert result.data["order_cancel"] is None
    assert result.data["lastTransactionID"] == "6384"

    request = route.calls.last.request
    assert request.method == "POST"
    assert request.headers["Authorization"] == "Bearer test-token"
    assert _request_body(route) == {
        "order": {
            "type": "MARKET",
            "instrument": "EUR_USD",
            "units": "100",
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            "takeProfitOnFill": {"price": "1.12000", "timeInForce": "GTC"},
            "trailingStopLossOnFill": {"distance": "0.00500", "timeInForce": "GTC"},
        }
    }


@respx.mock
async def test_create_limit_order_body(settings):
    route = respx.post(f"{BASE}/orders").mock(
        return_value=Response(
            201,
            json={
                "orderCreateTransaction": {
                    "id": "6390",
                    "type": "LIMIT_ORDER",
                    "instrument": "EUR_USD",
                    "units": "-500",
                    "price": "1.11500",
                },
                "lastTransactionID": "6390",
            },
        )
    )
    server = make_server(settings, orders.register)
    async with Client(server) as c:
        result = await c.call_tool(
            "create_order",
            {
                "order_type": "LIMIT",
                "instrument": "EUR_USD",
                "units": "-500",
                "price": "1.11500",
                "time_in_force": "GTD",
                "gtd_time": "2026-08-01T12:00:00Z",
                "stop_loss_distance": "0.00300",
            },
        )

    assert result.data["order_create"]["id"] == "6390"
    assert result.data["order_fill"] is None
    assert _request_body(route) == {
        "order": {
            "type": "LIMIT",
            "instrument": "EUR_USD",
            "units": "-500",
            "price": "1.11500",
            "timeInForce": "GTD",
            "gtdTime": "2026-08-01T12:00:00Z",
            "positionFill": "DEFAULT",
            "stopLossOnFill": {"distance": "0.00300", "timeInForce": "GTC"},
        }
    }


@respx.mock
async def test_create_order_cancelled_on_201(settings):
    respx.post(f"{BASE}/orders").mock(
        return_value=Response(
            201,
            json={
                "orderCreateTransaction": {"id": "6400", "type": "MARKET_ORDER"},
                "orderCancelTransaction": {
                    "id": "6401",
                    "orderID": "6400",
                    "reason": "INSUFFICIENT_MARGIN",
                },
                "lastTransactionID": "6401",
            },
        )
    )
    server = make_server(settings, orders.register)
    async with Client(server) as c:
        result = await c.call_tool(
            "create_order",
            {"order_type": "MARKET", "instrument": "EUR_USD", "units": "1000000"},
        )

    assert result.data["order_fill"] is None
    assert result.data["order_cancel"]["reason"] == "INSUFFICIENT_MARGIN"


async def test_create_pending_order_requires_price(settings):
    server = make_server(settings, orders.register)
    async with Client(server) as c:
        with pytest.raises(ToolError, match="LIMIT orders require a price"):
            await c.call_tool(
                "create_order",
                {"order_type": "LIMIT", "instrument": "EUR_USD", "units": "100"},
            )


async def test_create_order_rejects_conflicting_stop_loss(settings):
    server = make_server(settings, orders.register)
    async with Client(server) as c:
        with pytest.raises(ToolError, match="at most one of stop_loss_price"):
            await c.call_tool(
                "create_order",
                {
                    "order_type": "MARKET",
                    "instrument": "EUR_USD",
                    "units": "100",
                    "stop_loss_price": "1.09000",
                    "stop_loss_distance": "0.00300",
                },
            )


async def test_create_market_order_rejects_pending_time_in_force(settings):
    server = make_server(settings, orders.register)
    async with Client(server) as c:
        with pytest.raises(ToolError, match="'FOK' or 'IOC'"):
            await c.call_tool(
                "create_order",
                {
                    "order_type": "MARKET",
                    "instrument": "EUR_USD",
                    "units": "100",
                    "time_in_force": "GTC",
                },
            )


@respx.mock
async def test_replace_order(settings):
    route = respx.put(f"{BASE}/orders/6372").mock(
        return_value=Response(
            201,
            json={
                "orderCancelTransaction": {
                    "id": "6410",
                    "orderID": "6372",
                    "replacedByOrderID": "6411",
                    "reason": "CLIENT_REQUEST_REPLACED",
                },
                "orderCreateTransaction": {
                    "id": "6411",
                    "type": "STOP_ORDER",
                    "instrument": "USD_JPY",
                    "units": "250",
                    "price": "151.500",
                },
                "lastTransactionID": "6411",
            },
        )
    )
    server = make_server(settings, orders.register)
    async with Client(server) as c:
        result = await c.call_tool(
            "replace_order",
            {
                "order_specifier": "6372",
                "order_type": "STOP",
                "instrument": "USD_JPY",
                "units": "250",
                "price": "151.500",
            },
        )

    assert result.data["replaced_order_cancel"]["reason"] == "CLIENT_REQUEST_REPLACED"
    assert result.data["order_create"]["id"] == "6411"
    assert result.data["order_cancel"] is None

    request = route.calls.last.request
    assert request.method == "PUT"
    assert request.headers["Authorization"] == "Bearer test-token"
    assert _request_body(route) == {
        "order": {
            "type": "STOP",
            "instrument": "USD_JPY",
            "units": "250",
            "price": "151.500",
            "timeInForce": "GTC",
            "positionFill": "DEFAULT",
        }
    }


@respx.mock
async def test_cancel_order(settings):
    route = respx.put(f"{BASE}/orders/6411/cancel").mock(
        return_value=Response(
            200,
            json={
                "orderCancelTransaction": {
                    "id": "6412",
                    "orderID": "6411",
                    "reason": "CLIENT_REQUEST",
                    "time": "2026-07-16T09:00:00.000000000Z",
                },
                "lastTransactionID": "6412",
            },
        )
    )
    server = make_server(settings, orders.register)
    async with Client(server) as c:
        result = await c.call_tool("cancel_order", {"order_specifier": "6411"})

    assert result.data["order_cancel"]["reason"] == "CLIENT_REQUEST"
    assert result.data["lastTransactionID"] == "6412"

    request = route.calls.last.request
    assert request.method == "PUT"
    assert request.url.path == f"/v3/accounts/{TEST_ACCOUNT_ID}/orders/6411/cancel"
    assert request.headers["Authorization"] == "Bearer test-token"


async def test_write_tools_absent_without_trading(settings_no_trading):
    server = make_server(settings_no_trading, orders.register)
    async with Client(server) as c:
        names = {tool.name for tool in await c.list_tools()}
    assert names == READ_TOOLS


async def test_write_tools_present_with_trading(settings):
    server = make_server(settings, orders.register)
    async with Client(server) as c:
        names = {tool.name for tool in await c.list_tools()}
    assert names == READ_TOOLS | WRITE_TOOLS
