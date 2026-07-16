# oanda-mcp

MCP server for the [Oanda v20 REST API](https://developer.oanda.com/rest-live-v20/introduction/)
(forex/CFD trading): account state, candles, live pricing, order books, orders, trades, positions,
and transaction history. It defaults to the practice environment, and the trading (write) tools are
not registered at all unless explicitly enabled — a client connected to the default configuration
cannot see or call them.

## Features

- **Read-first by design** — 22 read tools cover accounts, market data, pricing, orders, trades,
  positions, and transaction history; the 7 write tools exist only when
  `OANDA_MCP_ENABLE_TRADING` is set.
- **Practice environment by default** — the live host is opt-in via `OANDA_ENV=live`.
- **Trimmed responses** — tools return the fields a trader acts on (prices, units, P/L,
  timestamps), not raw API dumps; list tools accept a `limit` parameter.
- **v20 conventions preserved** — prices and monetary values are decimal strings, timestamps are
  RFC3339, units are signed (positive = long, negative = short).
- **Credentials stay in the environment** — the token travels only in the `Authorization` header,
  and error messages never echo header or credential values.
- **Rate-limit aware** — HTTP 429 responses are retried once, honouring `Retry-After`.

## Tools

### Read tools (always registered)

| Tool | Description |
|---|---|
| `list_accounts` | List the accounts authorized for the configured API token |
| `get_account` | Full state of the configured account, trimmed to summary level |
| `get_account_summary` | Lightweight account summary: balance, NAV, margin headroom |
| `list_account_instruments` | Instruments tradeable on the account, with pip location, display precision, trade size limits, and margin rate |
| `get_account_changes` | Poll for order/trade/position changes since a transaction ID |
| `get_candles` | OHLC candles for an instrument at any granularity (S5 through monthly) |
| `get_order_book` | Aggregate pending-order book snapshot around the current price |
| `get_position_book` | Aggregate open-position book snapshot around the current price |
| `get_pricing` | Current bid/ask/spread for one or more instruments |
| `get_latest_candles` | Most recent candle per instrument/granularity specification |
| `list_orders` | Orders on the account, filterable by instrument and state |
| `list_pending_orders` | Every pending order on the account |
| `get_order` | A single order by ID or client-assigned ID |
| `list_trades` | Trades on the account, filterable by instrument and state |
| `list_open_trades` | Every open trade, with attached take-profit/stop-loss orders |
| `get_trade` | A single trade by ID or client-assigned ID |
| `list_positions` | Every position ever held, one per instrument, with lifetime P/L |
| `list_open_positions` | Positions with at least one open trade |
| `get_position` | The account's position for one instrument |
| `list_transactions` | Transaction page links for a time range (fetch pages with `get_transactions_range`) |
| `get_transaction` | A single transaction by ID |
| `get_transactions_range` | The transactions in an inclusive ID range |

### Write tools (registered only when `OANDA_MCP_ENABLE_TRADING` is set)

| Tool | Description |
|---|---|
| `configure_account` | Change the account alias and/or account-wide margin rate |
| `create_order` | Create a MARKET/LIMIT/STOP/MARKET_IF_TOUCHED order with optional take profit / stop loss / trailing stop on fill |
| `replace_order` | Replace a pending order atomically (cancel + create) |
| `cancel_order` | Cancel a pending order |
| `close_trade` | Close an open trade, fully or partially, with a market order |
| `set_trade_orders` | Create, replace, or cancel a trade's take profit, stop loss, and trailing stop |
| `close_position` | Close the long and/or short side of an instrument's position |

## Configuration

All configuration comes from environment variables — no config files, no flags carrying secrets.

| Variable | Default | Purpose |
|---|---|---|
| `OANDA_API_TOKEN` | — (required) | Personal access token, sent as `Authorization: Bearer ...` |
| `OANDA_ACCOUNT_ID` | first authorized account | Account to operate on |
| `OANDA_ENV` | `practice` | `practice` (api-fxpractice) or `live` (api-fxtrade) |
| `OANDA_MCP_ENABLE_TRADING` | off | `true`/`1`/`yes` registers the write tools |

## Getting started

Run straight from the repository with [uv](https://docs.astral.sh/uv/):

```sh
uvx --from git+https://github.com/florinel-chis/oanda-mcp oanda-mcp
```

The server speaks stdio by default; pass `--transport http --host 127.0.0.1 --port 8000` to serve
streamable HTTP at `/mcp` instead.

> **Warning:** the HTTP endpoint has **no authentication** — anyone who can reach the port can
> call every registered tool with your `OANDA_API_TOKEN`'s privileges. Bind it to `127.0.0.1`
> (never `0.0.0.0` on a reachable machine) and put an authenticated reverse proxy in front of it
> if it must be exposed beyond localhost.

### MCP client configuration

Add the server to your MCP client's configuration (the exact file location depends on the client):

```json
{
  "mcpServers": {
    "oanda": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/florinel-chis/oanda-mcp", "oanda-mcp"],
      "env": {
        "OANDA_API_TOKEN": "your-token",
        "OANDA_ENV": "practice"
      }
    }
  }
}
```

Or run the Docker image (build it first, see below):

```json
{
  "mcpServers": {
    "oanda": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-e", "OANDA_API_TOKEN", "-e", "OANDA_ENV", "oanda-mcp"],
      "env": {
        "OANDA_API_TOKEN": "your-token",
        "OANDA_ENV": "practice"
      }
    }
  }
}
```

## Docker

```sh
docker build -t oanda-mcp .

# stdio (for MCP client configs)
docker run -i --rm -e OANDA_API_TOKEN oanda-mcp

# streamable HTTP at http://127.0.0.1:8000/mcp
# (-p 127.0.0.1:8000:8000 keeps the unauthenticated endpoint off external interfaces;
#  --host 0.0.0.0 refers to interfaces inside the container and is required for the
#  port mapping to work)
docker run --rm -p 127.0.0.1:8000:8000 -e OANDA_API_TOKEN \
  oanda-mcp --transport http --host 0.0.0.0 --port 8000
```

## Safety

- The server talks to the **practice** environment unless `OANDA_ENV=live` is set.
- Trading tools are hidden unless `OANDA_MCP_ENABLE_TRADING` is set — they are never registered,
  not merely rejected, so MCP clients cannot discover or call them.
- The HTTP transport is **unauthenticated**: anyone who can reach the port can call every
  registered tool — read balances and positions always, and place or close orders when trading
  is enabled — using your API token's privileges. Keep it bound to `127.0.0.1` (the stdio
  transport has no such exposure), or front it with an authenticated proxy if remote access is
  genuinely needed.
- Order placement has a sharp edge: the API can accept an order and cancel it in the same
  response — a FOK market order, for example, is cancelled for `INSUFFICIENT_MARGIN` or
  `MARKET_HALTED`. The order tools surface the cancel transaction so this is visible, but always
  check it before treating an order as filled.
- Trading leveraged products is risky. Use at your own risk, and test everything against a
  practice account first.

## Development

```sh
uv sync
uv run pytest
uv run ruff check .
```

Tests are hermetic: all HTTP is mocked with respx and the server is exercised through the
in-memory FastMCP client. No credentials or network access are needed to run them. A manual,
read-only smoke check against a real practice account lives at `scripts/smoke.py`.

## License

MIT — see [LICENSE](LICENSE).
