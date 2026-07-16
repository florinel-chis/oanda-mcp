"""Tests for the market domain: candles, order book, position book."""

import pytest
import respx
from fastmcp import Client
from fastmcp.exceptions import ToolError
from httpx import Response

from conftest import make_server
from oanda_mcp.tools import market

BASE = "https://api-fxpractice.oanda.com"


@respx.mock
async def test_get_candles_count(settings):
    route = respx.get(f"{BASE}/v3/instruments/EUR_USD/candles").mock(
        return_value=Response(
            200,
            json={
                "instrument": "EUR_USD",
                "granularity": "H4",
                "candles": [
                    {
                        "time": "2026-07-15T08:00:00.000000000Z",
                        "mid": {"o": "1.1000", "h": "1.1050", "l": "1.0990", "c": "1.1040"},
                        "volume": 4321,
                        "complete": True,
                    },
                    {
                        "time": "2026-07-15T12:00:00.000000000Z",
                        "mid": {"o": "1.1040", "h": "1.1060", "l": "1.1020", "c": "1.1030"},
                        "volume": 1234,
                        "complete": False,
                    },
                ],
            },
        )
    )
    server = make_server(settings, market.register)
    async with Client(server) as c:
        result = await c.call_tool(
            "get_candles", {"instrument": "EUR_USD", "granularity": "H4", "count": 2}
        )

    assert result.data["instrument"] == "EUR_USD"
    assert result.data["granularity"] == "H4"
    first, second = result.data["candles"]
    assert first == {
        "time": "2026-07-15T08:00:00.000000000Z",
        "volume": 4321,
        "complete": True,
        "o": "1.1000",
        "h": "1.1050",
        "l": "1.0990",
        "c": "1.1040",
    }
    assert second["complete"] is False

    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == "/v3/instruments/EUR_USD/candles"
    assert request.url.params["granularity"] == "H4"
    assert request.url.params["count"] == "2"
    assert request.url.params["price"] == "M"
    assert request.url.params["smooth"] == "false"
    assert "from" not in request.url.params
    assert "to" not in request.url.params
    assert "includeFirst" not in request.url.params
    assert request.headers["Authorization"] == "Bearer test-token"
    assert request.headers["Accept-Datetime-Format"] == "RFC3339"


@respx.mock
async def test_get_candles_range_with_bid_ask(settings):
    route = respx.get(f"{BASE}/v3/instruments/EUR_USD/candles").mock(
        return_value=Response(
            200,
            json={
                "instrument": "EUR_USD",
                "granularity": "M10",
                "candles": [
                    {
                        "time": "2026-07-15T08:00:00.000000000Z",
                        "bid": {"o": "1.0999", "h": "1.1049", "l": "1.0989", "c": "1.1039"},
                        "ask": {"o": "1.1001", "h": "1.1051", "l": "1.0991", "c": "1.1041"},
                        "volume": 55,
                        "complete": True,
                    },
                ],
            },
        )
    )
    server = make_server(settings, market.register)
    async with Client(server) as c:
        result = await c.call_tool(
            "get_candles",
            {
                "instrument": "EUR_USD",
                "granularity": "M10",
                "from_time": "2026-07-15T08:00:00Z",
                "price": "BA",
                "include_first": False,
            },
        )

    candle = result.data["candles"][0]
    assert candle["bid"] == {"o": "1.0999", "h": "1.1049", "l": "1.0989", "c": "1.1039"}
    assert candle["ask"]["c"] == "1.1041"
    assert "o" not in candle

    request = route.calls.last.request
    assert request.url.params["from"] == "2026-07-15T08:00:00Z"
    assert request.url.params["price"] == "BA"
    assert request.url.params["includeFirst"] == "false"
    assert "count" not in request.url.params


@respx.mock
async def test_get_candles_rejects_count_with_full_range(settings):
    server = make_server(settings, market.register)
    async with Client(server) as c:
        with pytest.raises(ToolError, match="count cannot be combined"):
            await c.call_tool(
                "get_candles",
                {
                    "instrument": "EUR_USD",
                    "count": 100,
                    "from_time": "2026-07-01T00:00:00Z",
                    "to_time": "2026-07-15T00:00:00Z",
                },
            )


@respx.mock
async def test_get_candles_api_error_becomes_tool_error(settings):
    respx.get(f"{BASE}/v3/instruments/XXX_YYY/candles").mock(
        return_value=Response(
            400, json={"errorMessage": "Invalid value specified for 'instrument'"}
        )
    )
    server = make_server(settings, market.register)
    async with Client(server) as c:
        with pytest.raises(ToolError, match="HTTP 400: Invalid value specified for 'instrument'"):
            await c.call_tool("get_candles", {"instrument": "XXX_YYY"})


@respx.mock
async def test_get_order_book(settings):
    route = respx.get(f"{BASE}/v3/instruments/EUR_USD/orderBook").mock(
        return_value=Response(
            200,
            json={
                "orderBook": {
                    "instrument": "EUR_USD",
                    "time": "2026-07-15T12:00:00Z",
                    "unixTime": "1784548800",
                    "price": "1.1000",
                    "bucketWidth": "0.0050",
                    "buckets": [
                        {"price": "1.0900", "longCountPercent": "0.1", "shortCountPercent": "0.2"},
                        {"price": "1.0950", "longCountPercent": "0.3", "shortCountPercent": "0.4"},
                        {"price": "1.1000", "longCountPercent": "0.5", "shortCountPercent": "0.6"},
                        {"price": "1.1050", "longCountPercent": "0.7", "shortCountPercent": "0.8"},
                        {"price": "1.1100", "longCountPercent": "0.9", "shortCountPercent": "1.0"},
                    ],
                },
            },
        )
    )
    server = make_server(settings, market.register)
    async with Client(server) as c:
        result = await c.call_tool(
            "get_order_book",
            {"instrument": "EUR_USD", "time": "2026-07-15T12:00:00Z", "depth": 1},
        )

    assert result.data["instrument"] == "EUR_USD"
    assert result.data["price"] == "1.1000"
    assert result.data["bucketWidth"] == "0.0050"
    assert "unixTime" not in result.data
    # depth=1 keeps one bucket either side of the current price.
    assert [bucket["price"] for bucket in result.data["buckets"]] == [
        "1.0950",
        "1.1000",
        "1.1050",
    ]
    assert result.data["buckets"][1]["longCountPercent"] == "0.5"
    assert result.data["buckets"][1]["shortCountPercent"] == "0.6"

    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == "/v3/instruments/EUR_USD/orderBook"
    assert request.url.params["time"] == "2026-07-15T12:00:00Z"
    assert request.headers["Authorization"] == "Bearer test-token"
    assert request.headers["Accept-Datetime-Format"] == "RFC3339"


@respx.mock
async def test_get_position_book_defaults(settings):
    route = respx.get(f"{BASE}/v3/instruments/EUR_USD/positionBook").mock(
        return_value=Response(
            200,
            json={
                "positionBook": {
                    "instrument": "EUR_USD",
                    "time": "2026-07-15T12:00:00Z",
                    "price": "1.1000",
                    "bucketWidth": "0.0050",
                    "buckets": [
                        {"price": "1.0950", "longCountPercent": "0.3", "shortCountPercent": "0.4"},
                        {"price": "1.1000", "longCountPercent": "0.5", "shortCountPercent": "0.6"},
                    ],
                },
            },
        )
    )
    server = make_server(settings, market.register)
    async with Client(server) as c:
        result = await c.call_tool("get_position_book", {"instrument": "EUR_USD"})

    assert result.data["instrument"] == "EUR_USD"
    assert [bucket["price"] for bucket in result.data["buckets"]] == ["1.0950", "1.1000"]

    request = route.calls.last.request
    assert request.url.path == "/v3/instruments/EUR_USD/positionBook"
    assert "time" not in request.url.params
    assert request.headers["Authorization"] == "Bearer test-token"


async def test_all_tools_are_reads_and_present_without_trading(settings_no_trading):
    server = make_server(settings_no_trading, market.register)
    async with Client(server) as c:
        names = {tool.name for tool in await c.list_tools()}
    assert names == {"get_candles", "get_order_book", "get_position_book"}
