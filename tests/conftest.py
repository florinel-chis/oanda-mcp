"""Shared fixtures for the oanda-mcp test suite.

All HTTP is mocked with respx; no test touches the network or needs real
credentials. The canonical per-tool test mocks the exact upstream URL, builds
a one-domain server with ``make_server``, and drives the tool through the
in-memory FastMCP client, asserting on both the tool result and the captured
upstream request:

    import respx
    from httpx import Response
    from fastmcp import Client

    from conftest import TEST_ACCOUNT_ID, make_server
    from oanda_mcp.tools import trades


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
"""

from collections.abc import Callable

import pytest
from fastmcp import FastMCP

from oanda_mcp.client import ApiClient
from oanda_mcp.config import Settings

TEST_ACCOUNT_ID = "001-001-0000001-001"


@pytest.fixture(autouse=True)
def _clean_oanda_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests hermetic: no ambient OANDA_* variables leak in."""
    for name in (
        "OANDA_API_TOKEN",
        "OANDA_ACCOUNT_ID",
        "OANDA_ENV",
        "OANDA_MCP_ENABLE_TRADING",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings() -> Settings:
    """Dummy credentials, practice environment, trading enabled."""
    return Settings(
        api_token="test-token",
        account_id=TEST_ACCOUNT_ID,
        env="practice",
        enable_trading=True,
    )


@pytest.fixture
def settings_no_trading() -> Settings:
    """Dummy credentials, practice environment, trading disabled."""
    return Settings(
        api_token="test-token",
        account_id=TEST_ACCOUNT_ID,
        env="practice",
        enable_trading=False,
    )


def make_server(
    settings: Settings,
    register_fn: Callable[[FastMCP, ApiClient, Settings], None],
) -> FastMCP:
    """Build a bare server with a single domain's tools registered."""
    mcp = FastMCP("oanda-mcp-test")
    client = ApiClient(settings)
    register_fn(mcp, client, settings)
    return mcp
