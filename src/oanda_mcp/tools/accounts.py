"""Account-domain tools: discovery, state, tradeable instruments, change polling,
and (gated) account configuration.

Read tools are always registered; ``configure_account`` exists only when
``settings.enable_trading`` is true.
"""

from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from oanda_mcp.client import ApiClient
from oanda_mcp.config import Settings

# Summary-level Account fields worth returning; the full GET /v3/accounts/{id}
# response additionally embeds every pending order, open trade, and open
# position, which we deliberately drop (the counts below cover them).
_ACCOUNT_FIELDS: tuple[str, ...] = (
    "id",
    "alias",
    "currency",
    "balance",
    "NAV",
    "pl",
    "unrealizedPL",
    "resettablePL",
    "financing",
    "commission",
    "marginUsed",
    "marginAvailable",
    "marginRate",
    "marginCloseoutPercent",
    "withdrawalLimit",
    "positionValue",
    "openTradeCount",
    "openPositionCount",
    "pendingOrderCount",
    "hedgingEnabled",
    "createdTime",
    "lastTransactionID",
)

_INSTRUMENT_FIELDS: tuple[str, ...] = (
    "name",
    "type",
    "displayName",
    "pipLocation",
    "displayPrecision",
    "tradeUnitsPrecision",
    "minimumTradeSize",
    "marginRate",
    "maximumOrderUnits",
)


def _trim_account(account: dict[str, Any]) -> dict[str, Any]:
    """Keep summary fields and counts; drop embedded order/trade/position lists."""
    return {key: account[key] for key in _ACCOUNT_FIELDS if key in account}


def _trim_instrument(instrument: dict[str, Any]) -> dict[str, Any]:
    return {key: instrument[key] for key in _INSTRUMENT_FIELDS if key in instrument}


def register(mcp: FastMCP, client: ApiClient, settings: Settings) -> None:
    """Register the account tools on ``mcp``.

    All tools operate on the configured account (or the token's first account
    when none is configured), except ``list_accounts`` which enumerates every
    account the token can see.
    """

    async def list_accounts(
        limit: Annotated[
            int, Field(description="Maximum number of accounts to return.", ge=1)
        ] = 50,
    ) -> dict[str, Any]:
        """List the accounts authorized for the configured API token.

        Returns ``{"accounts": [...]}`` where each entry has ``id`` (the
        account identifier every other tool operates on, e.g.
        ``001-001-1234567-001``), ``tags`` (list of strings), and
        ``mt4AccountID`` when the account is MT4-bridged.
        """
        payload = await client.request("GET", "/v3/accounts")
        accounts = (payload or {}).get("accounts") or []
        return {"accounts": accounts[:limit]}

    async def get_account() -> dict[str, Any]:
        """Get the full state of the configured account, trimmed to summary level.

        Returns ``{"account": {...}, "lastTransactionID": "..."}``. The account
        object carries monetary fields as decimal strings in the account's home
        currency (``balance``, ``NAV``, ``pl``, ``unrealizedPL``,
        ``marginUsed``, ``marginAvailable``, ...), the leverage setting
        ``marginRate`` (decimal string, e.g. ``"0.02"`` = 50:1), the open-item
        counts (``openTradeCount``, ``openPositionCount``,
        ``pendingOrderCount``), and ``createdTime`` as an RFC3339 timestamp.
        The per-order/trade/position detail lists the API embeds here are
        dropped; use the orders, trades, and positions tools for those.
        For a cheaper request that yields the same fields, prefer
        ``get_account_summary``.
        """
        account_id = await client.account_id()
        payload = await client.request("GET", f"/v3/accounts/{account_id}")
        payload = payload or {}
        account = payload.get("account") or {}
        return {
            "account": _trim_account(account),
            "lastTransactionID": payload.get("lastTransactionID"),
        }

    async def get_account_summary() -> dict[str, Any]:
        """Get a summary of the configured account (no order/trade/position lists).

        Returns ``{"account": {...}, "lastTransactionID": "..."}`` with the
        same summary fields as ``get_account`` — monetary values as decimal
        strings in the home currency, ``marginRate`` as a decimal string
        (e.g. ``"0.02"`` = 50:1 leverage), open-item counts, and RFC3339
        timestamps. This is the lightest way to check balance, NAV, and margin
        headroom.
        """
        account_id = await client.account_id()
        payload = await client.request("GET", f"/v3/accounts/{account_id}/summary")
        payload = payload or {}
        account = payload.get("account") or {}
        return {
            "account": _trim_account(account),
            "lastTransactionID": payload.get("lastTransactionID"),
        }

    async def list_account_instruments(
        instruments: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Optional instrument names to look up, e.g. "
                    "['EUR_USD', 'DE30_EUR']. Omit to list every tradeable "
                    "instrument on the account."
                )
            ),
        ] = None,
        limit: Annotated[
            int, Field(description="Maximum number of instruments to return.", ge=1)
        ] = 100,
    ) -> dict[str, Any]:
        """List instruments tradeable on the configured account.

        Returns ``{"instruments": [...]}`` where each entry has ``name``
        (e.g. ``EUR_USD``), ``type`` (``CURRENCY``, ``CFD``, or ``METAL``),
        ``displayName``, ``pipLocation`` (power-of-ten exponent of one pip:
        ``-4`` means a pip is 0.0001), ``displayPrecision`` (the number of
        decimal places order prices must be formatted to — use it for the
        ``price`` of LIMIT/STOP orders or the API rejects them with a
        PRICE_PRECISION error), ``tradeUnitsPrecision`` (decimal places
        allowed in order units), ``minimumTradeSize`` (decimal string),
        ``marginRate`` (decimal string; the margin required per unit, e.g.
        ``"0.05"`` = 20:1 leverage), and ``maximumOrderUnits`` (decimal
        string).
        """
        account_id = await client.account_id()
        payload = await client.request(
            "GET",
            f"/v3/accounts/{account_id}/instruments",
            params={"instruments": ",".join(instruments) if instruments else None},
        )
        rows = (payload or {}).get("instruments") or []
        return {"instruments": [_trim_instrument(row) for row in rows[:limit]]}

    async def get_account_changes(
        since_transaction_id: Annotated[
            str | None,
            Field(
                description=(
                    "Transaction ID to compute changes since (exclusive). Use "
                    "the lastTransactionID from a previous call to poll "
                    "incrementally; omitting it returns changes since account "
                    "creation, which can be very large."
                )
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Poll the configured account for state changes since a transaction.

        Returns ``{"changes": {...}, "state": {...}, "lastTransactionID":
        "..."}``. ``changes`` groups what happened (``ordersCreated``,
        ``ordersCancelled``, ``ordersFilled``, ``ordersTriggered``,
        ``tradesOpened``, ``tradesReduced``, ``tradesClosed``, ``positions``,
        ``transactions``); ``state`` carries the price-dependent snapshot
        (account ``unrealizedPL``, ``NAV``, ``marginUsed``, plus per-trade,
        per-position, and per-order dynamic state). Feed the returned
        ``lastTransactionID`` back as ``since_transaction_id`` on the next
        call. Timestamps are RFC3339; monetary values are decimal strings.
        """
        account_id = await client.account_id()
        payload = await client.request(
            "GET",
            f"/v3/accounts/{account_id}/changes",
            params={"sinceTransactionID": since_transaction_id},
        )
        payload = payload or {}
        return {
            "changes": payload.get("changes"),
            "state": payload.get("state"),
            "lastTransactionID": payload.get("lastTransactionID"),
        }

    mcp.tool(list_accounts, annotations={"readOnlyHint": True})
    mcp.tool(get_account, annotations={"readOnlyHint": True})
    mcp.tool(get_account_summary, annotations={"readOnlyHint": True})
    mcp.tool(list_account_instruments, annotations={"readOnlyHint": True})
    mcp.tool(get_account_changes, annotations={"readOnlyHint": True})

    if settings.enable_trading:

        async def configure_account(
            alias: Annotated[
                str | None,
                Field(description="New human-readable name for the account."),
            ] = None,
            margin_rate: Annotated[
                str | None,
                Field(
                    description=(
                        "New account-wide margin rate as a decimal string, "
                        "e.g. '0.02' for 50:1 leverage or '0.05' for 20:1. "
                        "Lower values mean higher leverage."
                    )
                ),
            ] = None,
        ) -> dict[str, Any]:
            """Change the configured account's alias and/or margin rate.

            At least one of ``alias`` and ``margin_rate`` must be provided.
            Returns ``{"configuration": {...}, "lastTransactionID": "..."}``
            where ``configuration`` is the resulting
            ClientConfigureTransaction (``id``, ``time`` as RFC3339, ``alias``,
            ``marginRate``). Raising the margin rate (reducing leverage) can be
            rejected while it would put the account into margin closeout.
            """
            body: dict[str, Any] = {}
            if alias is not None:
                body["alias"] = alias
            if margin_rate is not None:
                body["marginRate"] = margin_rate
            if not body:
                raise ToolError("provide at least one of: alias, margin_rate")
            account_id = await client.account_id()
            payload = await client.request(
                "PATCH",
                f"/v3/accounts/{account_id}/configuration",
                json_body=body,
            )
            payload = payload or {}
            return {
                "configuration": payload.get("clientConfigureTransaction"),
                "lastTransactionID": payload.get("lastTransactionID"),
            }

        mcp.tool(configure_account)
