"""Tests for the transaction-history domain tools."""

import pytest
import respx
from fastmcp import Client
from fastmcp.exceptions import ToolError
from httpx import Response

from conftest import TEST_ACCOUNT_ID, make_server
from oanda_mcp.tools import transactions

BASE = f"https://api-fxpractice.oanda.com/v3/accounts/{TEST_ACCOUNT_ID}"
TRANSACTIONS_URL = f"{BASE}/transactions"
IDRANGE_URL = f"{BASE}/transactions/idrange"


@respx.mock
async def test_list_transactions(settings):
    route = respx.get(TRANSACTIONS_URL).mock(
        return_value=Response(
            200,
            json={
                "from": "2026-07-01T00:00:00.000000000Z",
                "to": "2026-07-16T00:00:00.000000000Z",
                "pageSize": 50,
                "type": ["ORDER_FILL", "DAILY_FINANCING"],
                "count": 120,
                "pages": [
                    f"{TRANSACTIONS_URL}/idrange?from=100&to=149",
                    f"{TRANSACTIONS_URL}/idrange?from=150&to=199",
                    f"{TRANSACTIONS_URL}/idrange?from=200&to=219",
                ],
                "lastTransactionID": "219",
            },
        )
    )
    server = make_server(settings, transactions.register)
    async with Client(server) as c:
        result = await c.call_tool(
            "list_transactions",
            {
                "from_time": "2026-07-01T00:00:00Z",
                "to_time": "2026-07-16T00:00:00Z",
                "page_size": 50,
                "transaction_types": ["ORDER_FILL", "DAILY_FINANCING"],
                "limit": 2,
            },
        )

    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == f"/v3/accounts/{TEST_ACCOUNT_ID}/transactions"
    assert request.url.params["from"] == "2026-07-01T00:00:00Z"
    assert request.url.params["to"] == "2026-07-16T00:00:00Z"
    assert request.url.params["pageSize"] == "50"
    assert request.url.params["type"] == "ORDER_FILL,DAILY_FINANCING"
    assert request.headers["Authorization"] == "Bearer test-token"
    assert request.headers["Accept-Datetime-Format"] == "RFC3339"

    assert result.data["count"] == 120
    assert result.data["pageSize"] == 50
    assert result.data["lastTransactionID"] == "219"
    # limit=2 caps the page links returned.
    assert result.data["pages"] == [
        f"{TRANSACTIONS_URL}/idrange?from=100&to=149",
        f"{TRANSACTIONS_URL}/idrange?from=150&to=199",
    ]


@respx.mock
async def test_list_transactions_omits_unset_params(settings):
    route = respx.get(TRANSACTIONS_URL).mock(
        return_value=Response(
            200,
            json={"count": 0, "pages": [], "pageSize": 100, "lastTransactionID": "7"},
        )
    )
    server = make_server(settings, transactions.register)
    async with Client(server) as c:
        result = await c.call_tool("list_transactions", {})

    params = route.calls.last.request.url.params
    assert "from" not in params
    assert "to" not in params
    assert "type" not in params
    assert params["pageSize"] == "100"
    assert result.data["pages"] == []


@respx.mock
async def test_get_transaction(settings):
    route = respx.get(f"{TRANSACTIONS_URL}/6410").mock(
        return_value=Response(
            200,
            json={
                "transaction": {
                    "id": "6410",
                    "time": "2026-07-15T14:30:00.000000000Z",
                    "type": "ORDER_FILL",
                    "instrument": "EUR_USD",
                    "units": "100",
                    "price": "1.10345",
                    "pl": "0.0000",
                    "financing": "0.0000",
                    "commission": "0.0000",
                    "accountBalance": "10000.4567",
                    "orderID": "6409",
                    "reason": "MARKET_ORDER",
                    "tradeOpened": {"tradeID": "6410", "units": "100", "price": "1.10345"},
                    "accountID": TEST_ACCOUNT_ID,
                    "userID": 1234567,
                    "batchID": "6409",
                    "requestID": "42412538687963602",
                    "gainQuoteHomeConversionFactor": "1.0",
                    "lossQuoteHomeConversionFactor": "1.0",
                    "fullVWAP": "1.10345",
                    "fullPrice": {"bids": [], "asks": []},
                },
                "lastTransactionID": "6410",
            },
        )
    )
    server = make_server(settings, transactions.register)
    async with Client(server) as c:
        result = await c.call_tool("get_transaction", {"transaction_id": "6410"})

    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == f"/v3/accounts/{TEST_ACCOUNT_ID}/transactions/6410"
    assert request.headers["Authorization"] == "Bearer test-token"
    assert request.headers["Accept-Datetime-Format"] == "RFC3339"

    transaction = result.data["transaction"]
    assert transaction["id"] == "6410"
    assert transaction["type"] == "ORDER_FILL"
    assert transaction["price"] == "1.10345"
    assert transaction["accountBalance"] == "10000.4567"
    assert transaction["tradeOpened"]["tradeID"] == "6410"
    # Internal bookkeeping is trimmed away.
    for dropped in (
        "accountID",
        "userID",
        "batchID",
        "requestID",
        "gainQuoteHomeConversionFactor",
        "lossQuoteHomeConversionFactor",
        "fullVWAP",
        "fullPrice",
    ):
        assert dropped not in transaction
    assert result.data["lastTransactionID"] == "6410"


@respx.mock
async def test_get_transactions_range(settings):
    route = respx.get(IDRANGE_URL).mock(
        return_value=Response(
            200,
            json={
                "transactions": [
                    {
                        "id": "100",
                        "time": "2026-07-14T09:00:00.000000000Z",
                        "type": "MARKET_ORDER",
                        "instrument": "EUR_USD",
                        "units": "-250",
                        "timeInForce": "FOK",
                        "reason": "CLIENT_ORDER",
                        "batchID": "100",
                    },
                    {
                        "id": "101",
                        "time": "2026-07-14T09:00:00.000000000Z",
                        "type": "ORDER_FILL",
                        "instrument": "EUR_USD",
                        "units": "-250",
                        "price": "1.10200",
                        "pl": "0.0000",
                        "accountBalance": "9999.1234",
                        "orderID": "100",
                        "requestID": "42412538687963603",
                    },
                    {
                        "id": "102",
                        "time": "2026-07-15T21:00:00.000000000Z",
                        "type": "DAILY_FINANCING",
                        "financing": "-0.1234",
                        "accountBalance": "9999.0000",
                    },
                ],
                "lastTransactionID": "102",
            },
        )
    )
    server = make_server(settings, transactions.register)
    async with Client(server) as c:
        result = await c.call_tool(
            "get_transactions_range",
            {
                "from_id": "100",
                "to_id": "199",
                "transaction_types": ["MARKET_ORDER", "ORDER_FILL", "DAILY_FINANCING"],
                "limit": 2,
            },
        )

    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == f"/v3/accounts/{TEST_ACCOUNT_ID}/transactions/idrange"
    assert request.url.params["from"] == "100"
    assert request.url.params["to"] == "199"
    assert request.url.params["type"] == "MARKET_ORDER,ORDER_FILL,DAILY_FINANCING"
    assert request.headers["Authorization"] == "Bearer test-token"
    assert request.headers["Accept-Datetime-Format"] == "RFC3339"

    # limit=2 caps the transactions returned.
    assert [t["id"] for t in result.data["transactions"]] == ["100", "101"]
    market_order, order_fill = result.data["transactions"]
    assert market_order["units"] == "-250"
    assert "batchID" not in market_order
    assert order_fill["price"] == "1.10200"
    assert "requestID" not in order_fill
    assert result.data["lastTransactionID"] == "102"


@respx.mock
async def test_get_transaction_api_error_becomes_tool_error(settings):
    respx.get(f"{TRANSACTIONS_URL}/999999").mock(
        return_value=Response(
            404,
            json={"errorMessage": "The transaction ID specified does not exist"},
        )
    )
    server = make_server(settings, transactions.register)
    async with Client(server) as c:
        with pytest.raises(
            ToolError, match="HTTP 404: The transaction ID specified does not exist"
        ):
            await c.call_tool("get_transaction", {"transaction_id": "999999"})


async def test_read_tools_present_without_trading(settings_no_trading):
    server = make_server(settings_no_trading, transactions.register)
    async with Client(server) as c:
        names = {tool.name for tool in await c.list_tools()}
    assert names == {"list_transactions", "get_transaction", "get_transactions_range"}
