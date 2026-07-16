"""Tests for server assembly and the command-line entry point.

These verify the full tool roster produced by ``build_server`` — most
importantly that every write tool is gated on ``enable_trading`` and that no
write tool exists when trading is disabled.
"""

import pytest
from fastmcp import Client

from oanda_mcp.server import build_server, main

READ_TOOLS = {
    # accounts
    "list_accounts",
    "get_account",
    "get_account_summary",
    "list_account_instruments",
    "get_account_changes",
    # market
    "get_candles",
    "get_order_book",
    "get_position_book",
    # pricing
    "get_pricing",
    "get_latest_candles",
    # orders
    "list_orders",
    "list_pending_orders",
    "get_order",
    # trades
    "list_trades",
    "list_open_trades",
    "get_trade",
    # positions
    "list_positions",
    "list_open_positions",
    "get_position",
    # transactions
    "list_transactions",
    "get_transaction",
    "get_transactions_range",
}

WRITE_TOOLS = {
    "configure_account",
    "create_order",
    "replace_order",
    "cancel_order",
    "close_trade",
    "set_trade_orders",
    "close_position",
}


async def test_build_server_registers_all_tools_with_trading(settings):
    server = build_server(settings)
    async with Client(server) as c:
        names = {tool.name for tool in await c.list_tools()}
    assert names == READ_TOOLS | WRITE_TOOLS
    assert names & WRITE_TOOLS == WRITE_TOOLS


async def test_build_server_without_trading_has_no_write_tools(settings_no_trading):
    server = build_server(settings_no_trading)
    async with Client(server) as c:
        names = {tool.name for tool in await c.list_tools()}
    assert names == READ_TOOLS
    assert names & WRITE_TOOLS == set()


async def test_read_tools_carry_read_only_annotation(settings):
    """Every read tool advertises readOnlyHint; no write tool does."""
    server = build_server(settings)
    async with Client(server) as c:
        tools = {tool.name: tool for tool in await c.list_tools()}
    for name in READ_TOOLS:
        annotations = tools[name].annotations
        assert annotations is not None and annotations.readOnlyHint is True, name
    for name in WRITE_TOOLS:
        annotations = tools[name].annotations
        assert annotations is None or annotations.readOnlyHint is not True, name


def test_main_help_exits_zero_without_credentials(monkeypatch, capsys):
    """--help must work with no OANDA_* variables set (parse before config)."""
    monkeypatch.setattr("sys.argv", ["oanda-mcp", "--help"])
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 0
    assert "usage: oanda-mcp" in capsys.readouterr().out
