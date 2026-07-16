"""Server assembly and command-line entry point.

Tools live in one module per API domain under ``oanda_mcp.tools`` and are wired
in through ``DOMAIN_MODULES``. The full intended set is:

    accounts, market, pricing, orders, trades, positions, transactions

Each module exposes ``register(mcp, client, settings)``; the registration
convention is documented in ``oanda_mcp.tools``. A module listed in
``DOMAIN_MODULES`` that is missing or lacks ``register`` makes ``build_server``
raise immediately — the server never starts with a partial tool set.
"""

import argparse
import importlib

from fastmcp import FastMCP

from oanda_mcp.client import ApiClient
from oanda_mcp.config import Settings

DOMAIN_MODULES: tuple[str, ...] = (
    "accounts",
    "market",
    "pricing",
    "orders",
    "trades",
    "positions",
    "transactions",
)


def build_server(settings: Settings) -> FastMCP:
    """Build the FastMCP server with every domain module registered.

    One ``ApiClient`` is shared by all tools; write tools are registered only
    when ``settings.enable_trading`` is true (each domain module enforces
    this itself).
    """
    mcp = FastMCP("oanda-mcp")
    client = ApiClient(settings)
    for name in DOMAIN_MODULES:
        module = importlib.import_module(f"oanda_mcp.tools.{name}")
        module.register(mcp, client, settings)
    return mcp


def main() -> None:
    """Console-script entry point: parse arguments, then run the server."""
    parser = argparse.ArgumentParser(
        prog="oanda-mcp",
        description="MCP server for the Oanda v20 REST API.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="transport to serve on (default: stdio)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind host for --transport http")
    parser.add_argument("--port", type=int, default=8000, help="bind port for --transport http")
    # Parse before touching the environment so --help works without credentials.
    args = parser.parse_args()

    mcp = build_server(Settings.from_env())
    if args.transport == "http":
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
