"""Tests for the pricing domain tools."""

import pytest
import respx
from fastmcp import Client
from fastmcp.exceptions import ToolError
from httpx import Response

from conftest import TEST_ACCOUNT_ID, make_server
from oanda_mcp.tools import pricing

PRICING_URL = f"https://api-fxpractice.oanda.com/v3/accounts/{TEST_ACCOUNT_ID}/pricing"
LATEST_CANDLES_URL = (
    f"https://api-fxpractice.oanda.com/v3/accounts/{TEST_ACCOUNT_ID}/candles/latest"
)


@respx.mock
async def test_get_pricing_tolerates_empty_body(settings):
    """An empty-bodied 2xx response maps to an empty result, not a crash."""
    respx.get(PRICING_URL).mock(return_value=Response(200))
    server = make_server(settings, pricing.register)
    async with Client(server) as c:
        result = await c.call_tool("get_pricing", {"instruments": ["EUR_USD"]})
    assert result.data == {"time": None, "prices": []}


@respx.mock
async def test_get_latest_candles_tolerates_empty_body(settings):
    respx.get(LATEST_CANDLES_URL).mock(return_value=Response(200))
    server = make_server(settings, pricing.register)
    async with Client(server) as c:
        result = await c.call_tool(
            "get_latest_candles", {"candle_specifications": ["EUR_USD:M10:M"]}
        )
    assert result.data == {"latest_candles": []}


@respx.mock
async def test_get_pricing(settings):
    route = respx.get(PRICING_URL).mock(
        return_value=Response(
            200,
            json={
                "prices": [
                    {
                        "instrument": "EUR_USD",
                        "time": "2026-07-16T08:00:00.000000000Z",
                        "tradeable": True,
                        "bids": [{"price": "1.10000", "liquidity": 1000000}],
                        "asks": [{"price": "1.10010", "liquidity": 1000000}],
                        "closeoutBid": "1.09990",
                        "closeoutAsk": "1.10020",
                    },
                    {
                        "instrument": "USD_JPY",
                        "time": "2026-07-16T08:00:00.000000000Z",
                        "tradeable": False,
                        "bids": [],
                        "asks": [],
                    },
                ],
                "time": "2026-07-16T08:00:01.000000000Z",
            },
        )
    )
    server = make_server(settings, pricing.register)
    async with Client(server) as c:
        result = await c.call_tool("get_pricing", {"instruments": ["EUR_USD", "USD_JPY"]})

    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == f"/v3/accounts/{TEST_ACCOUNT_ID}/pricing"
    assert request.url.params["instruments"] == "EUR_USD,USD_JPY"
    assert request.headers["Authorization"] == "Bearer test-token"
    assert request.headers["Accept-Datetime-Format"] == "RFC3339"

    assert result.data["time"] == "2026-07-16T08:00:01.000000000Z"
    eur_usd, usd_jpy = result.data["prices"]
    assert eur_usd == {
        "instrument": "EUR_USD",
        "time": "2026-07-16T08:00:00.000000000Z",
        "tradeable": True,
        "bid": "1.10000",
        "ask": "1.10010",
        "spread": pytest.approx(0.0001),
    }
    assert usd_jpy["tradeable"] is False
    assert usd_jpy["bid"] is None
    assert usd_jpy["ask"] is None
    assert usd_jpy["spread"] is None


@respx.mock
async def test_get_latest_candles(settings):
    route = respx.get(LATEST_CANDLES_URL).mock(
        return_value=Response(
            200,
            json={
                "latestCandles": [
                    {
                        "instrument": "EUR_USD",
                        "granularity": "M10",
                        "candles": [
                            {
                                "time": "2026-07-16T07:50:00.000000000Z",
                                "complete": False,
                                "volume": 42,
                                "bid": {"o": "1.0999", "h": "1.1001", "l": "1.0998", "c": "1.1000"},
                                "mid": {"o": "1.1000", "h": "1.1002", "l": "1.0999", "c": "1.1001"},
                            }
                        ],
                    }
                ]
            },
        )
    )
    server = make_server(settings, pricing.register)
    async with Client(server) as c:
        result = await c.call_tool(
            "get_latest_candles",
            {"candle_specifications": ["EUR_USD:M10:BM"]},
        )

    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == f"/v3/accounts/{TEST_ACCOUNT_ID}/candles/latest"
    assert request.url.params["candleSpecifications"] == "EUR_USD:M10:BM"
    assert request.headers["Authorization"] == "Bearer test-token"
    assert request.headers["Accept-Datetime-Format"] == "RFC3339"

    entry = result.data["latest_candles"][0]
    assert entry["instrument"] == "EUR_USD"
    assert entry["granularity"] == "M10"
    candle = entry["candles"][0]
    assert candle == {
        "time": "2026-07-16T07:50:00.000000000Z",
        "complete": False,
        "volume": 42,
        "bid": {"o": "1.0999", "h": "1.1001", "l": "1.0998", "c": "1.1000"},
        "mid": {"o": "1.1000", "h": "1.1002", "l": "1.0999", "c": "1.1001"},
    }
    assert "ask" not in candle


@respx.mock
async def test_get_pricing_api_error_becomes_tool_error(settings):
    respx.get(PRICING_URL).mock(
        return_value=Response(
            400,
            json={"errorMessage": "Invalid value specified for 'instruments'"},
        )
    )
    server = make_server(settings, pricing.register)
    async with Client(server) as c:
        with pytest.raises(ToolError, match="HTTP 400: Invalid value specified for 'instruments'"):
            await c.call_tool("get_pricing", {"instruments": ["BOGUS"]})


async def test_read_tools_present_without_trading(settings_no_trading):
    server = make_server(settings_no_trading, pricing.register)
    async with Client(server) as c:
        names = {tool.name for tool in await c.list_tools()}
    assert names == {"get_pricing", "get_latest_candles"}
