"""Manual read-only smoke check against a real Oanda practice account.

Never run in CI — it needs real credentials and network access. Usage:

    OANDA_API_TOKEN=... [OANDA_ACCOUNT_ID=...] uv run python scripts/smoke.py

The check always talks to the practice environment (regardless of OANDA_ENV)
with trading disabled, calls a few read tools through the in-memory MCP
client, and prints only tool names and result field names — never field
values, so balances and credentials cannot end up in a terminal scrollback.
"""

import asyncio
import os
from typing import Any

from fastmcp import Client

from oanda_mcp.config import Settings
from oanda_mcp.server import build_server

CALLS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("get_account_summary", {}),
    ("get_pricing", {"instruments": ["EUR_USD"]}),
    ("get_candles", {"instrument": "EUR_USD", "granularity": "H1", "count": 5}),
)


async def main() -> None:
    token = os.environ.get("OANDA_API_TOKEN", "").strip()
    if not token:
        raise SystemExit("OANDA_API_TOKEN is not set")

    settings = Settings(
        api_token=token,
        account_id=os.environ.get("OANDA_ACCOUNT_ID", "").strip() or None,
        env="practice",  # smoke checks never touch the live environment
        enable_trading=False,
    )
    server = build_server(settings)

    async with Client(server) as client:
        for name, arguments in CALLS:
            result = await client.call_tool(name, arguments)
            fields = sorted(result.data) if isinstance(result.data, dict) else []
            print(f"{name}: ok, fields={fields}")


if __name__ == "__main__":
    asyncio.run(main())
