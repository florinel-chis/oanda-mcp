"""Transaction-history tools: paged listing, single lookup, and ID-range fetch.

Read-only domain — it registers no write tools, so nothing here is gated on
``settings.enable_trading``.

The v20 transaction model is polymorphic: every transaction carries ``id``,
``time``, and ``type``, and the remaining fields depend on the type
(``ORDER_FILL`` has ``pl`` and ``accountBalance``, ``DAILY_FINANCING`` has
``financing``, ``TRANSFER_FUNDS`` has ``amount``, and so on). Tools here trim
each transaction to the fields a trader acts on and drop internal bookkeeping
(request IDs, batch IDs, conversion factors, full price ladders).
"""

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from oanda_mcp.client import ApiClient, quote_path_segment
from oanda_mcp.config import Settings

_TYPE_FILTER_DESCRIPTION = (
    "Optional transaction type filters, e.g. ['ORDER_FILL', 'DAILY_FINANCING']. "
    "Accepts TransactionFilter values such as ORDER, FUNDING, ADMIN, CREATE, "
    "CLOSE, TRANSFER_FUNDS, CLIENT_CONFIGURE, MARKET_ORDER, LIMIT_ORDER, "
    "STOP_ORDER, MARKET_IF_TOUCHED_ORDER, TAKE_PROFIT_ORDER, STOP_LOSS_ORDER, "
    "TRAILING_STOP_LOSS_ORDER, ORDER_FILL, ORDER_CANCEL, DAILY_FINANCING, "
    "MARGIN_CALL_ENTER, MARGIN_CALL_EXIT. Omit for all types."
)

# Fields kept per transaction, covering the common types (order create/fill/
# cancel, trade closes, funding, financing). Everything else — request IDs,
# batch IDs, home-conversion factors, full price ladders — is dropped.
_TRANSACTION_FIELDS: tuple[str, ...] = (
    "id",
    "time",
    "type",
    "instrument",
    "units",
    "requestedUnits",
    "price",
    "priceBound",
    "distance",
    "timeInForce",
    "gtdTime",
    "reason",
    "rejectReason",
    "pl",
    "financing",
    "commission",
    "halfSpreadCost",
    "accountBalance",
    "amount",
    "fundingReason",
    "orderID",
    "replacesOrderID",
    "replacedByOrderID",
    "tradeID",
    "clientTradeID",
    "tradeOpened",
    "tradeReduced",
    "tradesClosed",
    "takeProfitOnFill",
    "stopLossOnFill",
    "trailingStopLossOnFill",
    "alias",
    "marginRate",
)


def _trim_transaction(transaction: dict[str, Any]) -> dict[str, Any]:
    """Keep the trader-relevant fields of a (polymorphic) transaction."""
    return {key: transaction[key] for key in _TRANSACTION_FIELDS if key in transaction}


def register(mcp: FastMCP, client: ApiClient, settings: Settings) -> None:
    """Register the transaction tools on ``mcp``. This domain is read-only."""

    async def list_transactions(
        from_time: Annotated[
            str | None,
            Field(
                description=(
                    "Start of the time range as an RFC3339 timestamp, e.g. "
                    "'2026-07-01T00:00:00Z'. Defaults to the account's "
                    "creation time."
                )
            ),
        ] = None,
        to_time: Annotated[
            str | None,
            Field(
                description=(
                    "End of the time range as an RFC3339 timestamp. Defaults to the request time."
                )
            ),
        ] = None,
        page_size: Annotated[
            int,
            Field(description="Transactions per page link.", ge=1, le=1000),
        ] = 100,
        transaction_types: Annotated[
            list[str] | None, Field(description=_TYPE_FILTER_DESCRIPTION)
        ] = None,
        limit: Annotated[
            int, Field(description="Maximum number of page links to return.", ge=1)
        ] = 20,
    ) -> dict[str, Any]:
        """List the account's transaction pages for a time range.

        Note the API quirk: this endpoint returns **page links, not the
        transactions themselves**. The response is ``{"from": ..., "to": ...,
        "pageSize": ..., "count": ..., "pages": [...],
        "lastTransactionID": "..."}`` where ``count`` is the total number of
        matching transactions and each entry in ``pages`` is a URL of the form
        ``.../transactions/idrange?from=<id>&to=<id>`` covering at most
        ``page_size`` transactions. To fetch the actual transactions, take the
        ``from``/``to`` IDs out of a page URL and call
        ``get_transactions_range``. Timestamps are RFC3339.
        """
        account = await client.account_id()
        payload = await client.request(
            "GET",
            f"/v3/accounts/{account}/transactions",
            params={
                "from": from_time,
                "to": to_time,
                "pageSize": page_size,
                "type": ",".join(transaction_types) if transaction_types else None,
            },
        )
        payload = payload or {}
        return {
            "from": payload.get("from"),
            "to": payload.get("to"),
            "pageSize": payload.get("pageSize"),
            "count": payload.get("count"),
            "pages": (payload.get("pages") or [])[:limit],
            "lastTransactionID": payload.get("lastTransactionID"),
        }

    async def get_transaction(
        transaction_id: Annotated[
            str,
            Field(description="Transaction ID to look up (numeric string, e.g. '6410')."),
        ],
    ) -> dict[str, Any]:
        """Get the details of a single transaction.

        Returns ``{"transaction": {...}, "lastTransactionID": "..."}``. Every
        transaction has ``id``, ``time`` (RFC3339), and ``type``
        (e.g. ``ORDER_FILL``, ``MARKET_ORDER``, ``ORDER_CANCEL``,
        ``DAILY_FINANCING``, ``TRANSFER_FUNDS``); the remaining fields depend
        on the type. Monetary values (``pl``, ``financing``, ``commission``,
        ``accountBalance``, ``amount``) and ``units``/``price`` are decimal
        strings; ``units`` is signed (positive = long, negative = short).
        Internal bookkeeping fields (request/batch IDs, conversion factors)
        are dropped.
        """
        account = await client.account_id()
        payload = await client.request(
            "GET",
            f"/v3/accounts/{account}/transactions/{quote_path_segment(transaction_id)}",
        )
        payload = payload or {}
        transaction = payload.get("transaction") or {}
        return {
            "transaction": _trim_transaction(transaction),
            "lastTransactionID": payload.get("lastTransactionID"),
        }

    async def get_transactions_range(
        from_id: Annotated[
            str,
            Field(description="First transaction ID of the range (inclusive)."),
        ],
        to_id: Annotated[
            str,
            Field(description="Last transaction ID of the range (inclusive)."),
        ],
        transaction_types: Annotated[
            list[str] | None, Field(description=_TYPE_FILTER_DESCRIPTION)
        ] = None,
        limit: Annotated[
            int, Field(description="Maximum number of transactions to return.", ge=1)
        ] = 100,
    ) -> dict[str, Any]:
        """Get the transactions in an inclusive ID range.

        This is the endpoint that returns actual transactions — use it with
        the ID ranges from ``list_transactions`` page links, or directly when
        you know the IDs. Returns ``{"transactions": [...],
        "lastTransactionID": "..."}``; each transaction carries ``id``,
        ``time`` (RFC3339), ``type``, and its type-specific fields (see
        ``get_transaction``). Monetary values and ``units``/``price`` are
        decimal strings; ``units`` is signed (positive = long, negative =
        short). Keep ranges modest — the API serves at most 1000 transactions
        per request.
        """
        account = await client.account_id()
        payload = await client.request(
            "GET",
            f"/v3/accounts/{account}/transactions/idrange",
            params={
                "from": from_id,
                "to": to_id,
                "type": ",".join(transaction_types) if transaction_types else None,
            },
        )
        payload = payload or {}
        rows = payload.get("transactions") or []
        return {
            "transactions": [_trim_transaction(row) for row in rows[:limit]],
            "lastTransactionID": payload.get("lastTransactionID"),
        }

    mcp.tool(list_transactions, annotations={"readOnlyHint": True})
    mcp.tool(get_transaction, annotations={"readOnlyHint": True})
    mcp.tool(get_transactions_range, annotations={"readOnlyHint": True})
