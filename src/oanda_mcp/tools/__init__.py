"""Domain tool modules for the Oanda MCP server.

Every module in this package follows the same contract:

    def register(mcp: FastMCP, client: ApiClient, settings: Settings) -> None

* Tools are async closures over ``client``, defined inside ``register()``.
* Read tools are registered unconditionally via ``mcp.tool(fn)``.
* Write tools are registered only under ``if settings.enable_trading:`` —
  with trading disabled they are absent from the server entirely, not merely
  rejected at call time.
* Tool docstrings are the descriptions an MCP client sees: they state units,
  formats (e.g. RFC3339 timestamps, decimal-as-string prices), and accepted
  enum values, and are self-contained.
* Tools return trimmed, JSON-serializable dicts/lists — the fields a trader
  acts on, not raw payload dumps. Every list tool accepts a ``limit``
  parameter.
"""
