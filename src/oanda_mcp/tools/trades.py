"""Trade-domain tools: trade listing and inspection, plus (gated) trade
closing and dependent-order management (take profit, stop loss, trailing
stop loss).

Read tools are always registered; ``close_trade`` and ``set_trade_orders``
exist only when ``settings.enable_trading`` is true.
"""

from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from oanda_mcp.client import ApiClient, quote_path_segment
from oanda_mcp.config import Settings

_TRADE_FIELDS: tuple[str, ...] = (
    "id",
    "instrument",
    "price",
    "openTime",
    "state",
    "initialUnits",
    "currentUnits",
    "realizedPL",
    "unrealizedPL",
    "marginUsed",
    "financing",
    "averageClosePrice",
    "closeTime",
    "clientExtensions",
)

_DEPENDENT_ORDER_KEYS: tuple[str, ...] = (
    "takeProfitOrder",
    "stopLossOrder",
    "trailingStopLossOrder",
)

_DEPENDENT_ORDER_FIELDS: tuple[str, ...] = (
    "id",
    "type",
    "state",
    "price",
    "distance",
    "trailingStopValue",
    "timeInForce",
    "gtdTime",
)

_TRANSACTION_FIELDS: tuple[str, ...] = (
    "id",
    "type",
    "time",
    "orderID",
    "tradeID",
    "price",
    "distance",
    "timeInForce",
    "reason",
)

_FILL_FIELDS: tuple[str, ...] = (
    "id",
    "type",
    "time",
    "orderID",
    "instrument",
    "units",
    "price",
    "pl",
    "financing",
    "reason",
    "tradesClosed",
    "tradeReduced",
)


def _trim(mapping: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {key: mapping[key] for key in fields if key in mapping}


def _trim_trade(trade: dict[str, Any]) -> dict[str, Any]:
    """Keep the trade fields a trader acts on, plus trimmed dependent orders."""
    trimmed = _trim(trade, _TRADE_FIELDS)
    for key in _DEPENDENT_ORDER_KEYS:
        order = trade.get(key)
        if isinstance(order, dict):
            trimmed[key] = _trim(order, _DEPENDENT_ORDER_FIELDS)
    return trimmed


def _trim_transaction(payload: dict[str, Any], key: str) -> dict[str, Any] | None:
    transaction = payload.get(key)
    if isinstance(transaction, dict):
        return _trim(transaction, _TRANSACTION_FIELDS)
    return None


def register(mcp: FastMCP, client: ApiClient, settings: Settings) -> None:
    """Register the trade tools on ``mcp``.

    All tools operate on the configured account (or the token's first account
    when none is configured). A trade specifier is either the Oanda-assigned
    trade ID (e.g. ``"42"``) or ``@`` followed by the client-assigned ID
    (e.g. ``"@my_trade"``).
    """

    async def list_trades(
        instrument: Annotated[
            str | None,
            Field(description="Only return trades for this instrument, e.g. 'EUR_USD'."),
        ] = None,
        state: Annotated[
            Literal["OPEN", "CLOSED", "CLOSE_WHEN_TRADEABLE", "ALL"],
            Field(description="Trade state to filter by."),
        ] = "OPEN",
        before_id: Annotated[
            str | None,
            Field(
                description=(
                    "Maximum trade ID to return (pagination cursor). Omit to "
                    "start from the most recent trades; pass the smallest ID "
                    "from the previous page to fetch older trades."
                )
            ),
        ] = None,
        limit: Annotated[
            int,
            Field(
                description="Maximum number of trades to return (API maximum 500).", ge=1, le=500
            ),
        ] = 50,
    ) -> dict[str, Any]:
        """List trades on the configured account, most recent first.

        Returns ``{"trades": [...], "lastTransactionID": "..."}``. Each trade
        has ``id``, ``instrument``, ``price`` (entry fill, decimal string),
        ``openTime`` (RFC3339), ``state``, ``initialUnits`` and
        ``currentUnits`` (decimal strings whose sign gives direction:
        positive = long, negative = short), ``realizedPL``, ``unrealizedPL``,
        ``marginUsed`` (decimal strings in the account's home currency), and —
        when attached — the dependent orders ``takeProfitOrder``,
        ``stopLossOrder``, and ``trailingStopLossOrder`` (each with ``id`` and
        ``price`` or ``distance``). Closed trades additionally carry
        ``averageClosePrice`` and ``closeTime``.
        """
        account_id = await client.account_id()
        payload = await client.request(
            "GET",
            f"/v3/accounts/{account_id}/trades",
            params={
                "instrument": instrument,
                "state": state,
                "count": limit,
                "beforeID": before_id,
            },
        )
        payload = payload or {}
        trades = payload.get("trades") or []
        return {
            "trades": [_trim_trade(trade) for trade in trades[:limit]],
            "lastTransactionID": payload.get("lastTransactionID"),
        }

    async def list_open_trades(
        limit: Annotated[int, Field(description="Maximum number of trades to return.", ge=1)] = 50,
    ) -> dict[str, Any]:
        """List every open trade on the configured account.

        Returns ``{"trades": [...], "lastTransactionID": "..."}`` with the
        same trade shape as ``list_trades``: ``id``, ``instrument``, ``price``
        (entry fill, decimal string), ``openTime`` (RFC3339),
        ``currentUnits`` (decimal string; positive = long, negative = short),
        ``unrealizedPL`` and ``marginUsed`` (decimal strings in the home
        currency), and any attached ``takeProfitOrder``, ``stopLossOrder``,
        or ``trailingStopLossOrder``.
        """
        account_id = await client.account_id()
        payload = await client.request("GET", f"/v3/accounts/{account_id}/openTrades")
        payload = payload or {}
        trades = payload.get("trades") or []
        return {
            "trades": [_trim_trade(trade) for trade in trades[:limit]],
            "lastTransactionID": payload.get("lastTransactionID"),
        }

    async def get_trade(
        trade_specifier: Annotated[
            str,
            Field(
                description=(
                    "Trade ID (e.g. '42') or '@' followed by the "
                    "client-assigned ID (e.g. '@my_trade')."
                )
            ),
        ],
    ) -> dict[str, Any]:
        """Get the details of a single trade on the configured account.

        Returns ``{"trade": {...}, "lastTransactionID": "..."}`` where the
        trade has ``id``, ``instrument``, ``price`` (entry fill, decimal
        string), ``openTime`` (RFC3339), ``state`` (``OPEN``, ``CLOSED``, or
        ``CLOSE_WHEN_TRADEABLE``), ``initialUnits`` / ``currentUnits``
        (decimal strings; positive = long, negative = short), ``realizedPL``,
        ``unrealizedPL``, ``marginUsed``, ``financing`` (decimal strings in
        the home currency), any attached ``takeProfitOrder`` /
        ``stopLossOrder`` / ``trailingStopLossOrder``, and — for closed
        trades — ``averageClosePrice`` and ``closeTime``.
        """
        account_id = await client.account_id()
        payload = await client.request(
            "GET",
            f"/v3/accounts/{account_id}/trades/{quote_path_segment(trade_specifier)}",
        )
        payload = payload or {}
        trade = payload.get("trade") or {}
        return {
            "trade": _trim_trade(trade),
            "lastTransactionID": payload.get("lastTransactionID"),
        }

    mcp.tool(list_trades, annotations={"readOnlyHint": True})
    mcp.tool(list_open_trades, annotations={"readOnlyHint": True})
    mcp.tool(get_trade, annotations={"readOnlyHint": True})

    if settings.enable_trading:

        async def close_trade(
            trade_specifier: Annotated[
                str,
                Field(
                    description=(
                        "Trade ID (e.g. '42') or '@' followed by the "
                        "client-assigned ID (e.g. '@my_trade')."
                    )
                ),
            ],
            units: Annotated[
                str,
                Field(
                    description=(
                        "'ALL' to close the whole trade, or a positive decimal "
                        "string (regardless of trade direction) no greater than "
                        "the trade's open units for a partial close."
                    )
                ),
            ] = "ALL",
        ) -> dict[str, Any]:
            """Close (fully or partially) an open trade with a market order.

            Returns ``{"orderFillTransaction": {...}, "orderCancelTransaction":
            {...} | null, "lastTransactionID": "..."}``. The fill carries
            ``price`` and ``units`` (decimal strings) and the realized ``pl``
            in the account's home currency; ``tradesClosed`` /
            ``tradeReduced`` detail how the trade was affected. A successful
            response can still carry an ``orderCancelTransaction`` when the
            closing market order was cancelled (e.g. reason
            ``MARKET_HALTED``) — check it before trusting the fill. A
            rejected close (e.g. units exceeding the open amount) raises an
            error with the API's message.
            """
            account_id = await client.account_id()
            payload = await client.request(
                "PUT",
                f"/v3/accounts/{account_id}/trades/{quote_path_segment(trade_specifier)}/close",
                json_body={"units": units},
            )
            payload = payload or {}
            fill = payload.get("orderFillTransaction")
            return {
                "orderFillTransaction": _trim(fill, _FILL_FIELDS)
                if isinstance(fill, dict)
                else None,
                "orderCancelTransaction": _trim_transaction(payload, "orderCancelTransaction"),
                "lastTransactionID": payload.get("lastTransactionID"),
            }

        async def set_trade_orders(
            trade_specifier: Annotated[
                str,
                Field(
                    description=(
                        "Trade ID (e.g. '42') or '@' followed by the "
                        "client-assigned ID (e.g. '@my_trade')."
                    )
                ),
            ],
            take_profit_price: Annotated[
                str | None,
                Field(
                    description=(
                        "Absolute take-profit price as a decimal string, e.g. "
                        "'1.10500'. The API accepts only an absolute price "
                        "here, not a distance."
                    )
                ),
            ] = None,
            cancel_take_profit: Annotated[
                bool,
                Field(description="Cancel the trade's existing take-profit order."),
            ] = False,
            stop_loss_price: Annotated[
                str | None,
                Field(
                    description=(
                        "Absolute stop-loss price as a decimal string. "
                        "Mutually exclusive with stop_loss_distance."
                    )
                ),
            ] = None,
            stop_loss_distance: Annotated[
                str | None,
                Field(
                    description=(
                        "Stop-loss distance in price units from the current "
                        "price, as a positive decimal string, e.g. '0.0050'. "
                        "Mutually exclusive with stop_loss_price."
                    )
                ),
            ] = None,
            cancel_stop_loss: Annotated[
                bool,
                Field(description="Cancel the trade's existing stop-loss order."),
            ] = False,
            trailing_stop_loss_distance: Annotated[
                str | None,
                Field(
                    description=(
                        "Trailing stop-loss distance in price units, as a "
                        "positive decimal string, e.g. '0.0050'. Trailing "
                        "stops accept only a distance, never a price."
                    )
                ),
            ] = None,
            cancel_trailing_stop_loss: Annotated[
                bool,
                Field(description="Cancel the trade's existing trailing stop-loss order."),
            ] = False,
        ) -> dict[str, Any]:
            """Create, replace, or cancel a trade's dependent orders in one call.

            Each of the three order types — take profit, stop loss, trailing
            stop loss — is handled independently: pass a price/distance to
            create it or replace the existing one, set the matching cancel
            flag to remove it, or leave both unset to keep it unchanged. All
            prices and distances are decimal strings; created orders are
            good-til-cancelled. At least one change must be requested.

            Returns the resulting transactions, keyed
            ``takeProfitOrderTransaction`` /
            ``takeProfitOrderCancelTransaction`` (and likewise for
            ``stopLoss...`` and ``trailingStopLoss...``) — a replacement
            produces both a cancel and a create for that type — plus
            ``lastTransactionID``. Each transaction carries ``id``, ``time``
            (RFC3339), ``tradeID``, and its ``price`` or ``distance``.
            Important: a take profit or stop loss whose price is already
            crossed by the market is created and filled immediately — the
            response then also carries ``takeProfitOrderFillTransaction`` /
            ``stopLossOrderFillTransaction`` (with the realized ``pl`` and
            ``tradesClosed``), meaning the trade just closed or was reduced.
            Check for a fill before treating the new order as pending.
            """
            body: dict[str, Any] = {}

            if cancel_take_profit:
                if take_profit_price is not None:
                    raise ToolError("take_profit_price cannot be combined with cancel_take_profit")
                body["takeProfit"] = None
            elif take_profit_price is not None:
                body["takeProfit"] = {"price": take_profit_price}

            if cancel_stop_loss:
                if stop_loss_price is not None or stop_loss_distance is not None:
                    raise ToolError(
                        "stop_loss_price/stop_loss_distance cannot be combined "
                        "with cancel_stop_loss"
                    )
                body["stopLoss"] = None
            elif stop_loss_price is not None and stop_loss_distance is not None:
                raise ToolError("provide exactly one of stop_loss_price and stop_loss_distance")
            elif stop_loss_price is not None:
                body["stopLoss"] = {"price": stop_loss_price}
            elif stop_loss_distance is not None:
                body["stopLoss"] = {"distance": stop_loss_distance}

            if cancel_trailing_stop_loss:
                if trailing_stop_loss_distance is not None:
                    raise ToolError(
                        "trailing_stop_loss_distance cannot be combined with "
                        "cancel_trailing_stop_loss"
                    )
                body["trailingStopLoss"] = None
            elif trailing_stop_loss_distance is not None:
                body["trailingStopLoss"] = {"distance": trailing_stop_loss_distance}

            if not body:
                raise ToolError(
                    "no changes requested: set a price/distance or a cancel "
                    "flag for at least one order type"
                )

            account_id = await client.account_id()
            payload = await client.request(
                "PUT",
                f"/v3/accounts/{account_id}/trades/{quote_path_segment(trade_specifier)}/orders",
                json_body=body,
            )
            payload = payload or {}
            result: dict[str, Any] = {}
            for key in (
                "takeProfitOrderCancelTransaction",
                "takeProfitOrderTransaction",
                "takeProfitOrderCreatedCancelTransaction",
                "stopLossOrderCancelTransaction",
                "stopLossOrderTransaction",
                "stopLossOrderCreatedCancelTransaction",
                "trailingStopLossOrderCancelTransaction",
                "trailingStopLossOrderTransaction",
            ):
                transaction = _trim_transaction(payload, key)
                if transaction is not None:
                    result[key] = transaction
            for key in (
                "takeProfitOrderFillTransaction",
                "stopLossOrderFillTransaction",
            ):
                fill = payload.get(key)
                if isinstance(fill, dict):
                    result[key] = _trim(fill, _FILL_FIELDS)
            result["lastTransactionID"] = payload.get("lastTransactionID")
            return result

        mcp.tool(close_trade)
        mcp.tool(set_trade_orders)
