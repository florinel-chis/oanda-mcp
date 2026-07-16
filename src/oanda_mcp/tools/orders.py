"""Order-domain tools: listing, inspection, and (gated) create/replace/cancel.

Read tools are always registered; ``create_order``, ``replace_order``, and
``cancel_order`` exist only when ``settings.enable_trading`` is true.
"""

from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from oanda_mcp.client import ApiClient, quote_path_segment
from oanda_mcp.config import Settings

_MARKET_TIME_IN_FORCE = frozenset({"FOK", "IOC"})
_PENDING_TIME_IN_FORCE = frozenset({"GTC", "GFD", "GTD"})

# Order fields worth returning from list/get; the raw Order object also carries
# full client-extension and trigger bookkeeping we deliberately drop.
_ORDER_FIELDS: tuple[str, ...] = (
    "id",
    "type",
    "instrument",
    "units",
    "price",
    "priceBound",
    "distance",
    "state",
    "timeInForce",
    "gtdTime",
    "positionFill",
    "triggerCondition",
    "tradeID",
    "createTime",
    "filledTime",
    "cancelledTime",
    "fillingTransactionID",
    "cancellingTransactionID",
    "tradeOpenedID",
    "tradeReducedID",
    "replacesOrderID",
    "replacedByOrderID",
    "takeProfitOnFill",
    "stopLossOnFill",
    "trailingStopLossOnFill",
    "guaranteedStopLossOnFill",
)

_CREATE_TX_FIELDS: tuple[str, ...] = (
    "id",
    "type",
    "instrument",
    "units",
    "price",
    "timeInForce",
    "gtdTime",
    "positionFill",
    "reason",
    "time",
    "takeProfitOnFill",
    "stopLossOnFill",
    "trailingStopLossOnFill",
)

_FILL_TX_FIELDS: tuple[str, ...] = (
    "id",
    "orderID",
    "instrument",
    "units",
    "price",
    "pl",
    "financing",
    "commission",
    "accountBalance",
    "reason",
    "time",
    "tradeOpened",
    "tradeReduced",
    "tradesClosed",
)

_CANCEL_TX_FIELDS: tuple[str, ...] = (
    "id",
    "orderID",
    "replacedByOrderID",
    "reason",
    "time",
)


def _trim(payload: dict[str, Any] | None, fields: tuple[str, ...]) -> dict[str, Any] | None:
    """Keep only ``fields`` of a transaction/order object; ``None`` stays ``None``."""
    if payload is None:
        return None
    return {key: payload[key] for key in fields if key in payload}


def _build_order_request(
    order_type: str,
    instrument: str,
    units: str,
    price: str | None,
    time_in_force: str | None,
    gtd_time: str | None,
    position_fill: str,
    take_profit_price: str | None,
    stop_loss_price: str | None,
    stop_loss_distance: str | None,
    trailing_stop_distance: str | None,
) -> dict[str, Any]:
    """Assemble the v20 OrderRequest body shared by create and replace.

    Validates the per-type ``timeInForce`` sets (MARKET: FOK/IOC, pending
    types: GTC/GFD/GTD), the price requirement of pending types, and the
    mutual exclusion of ``stop_loss_price``/``stop_loss_distance`` — the API
    would reject these anyway, but failing client-side gives a clearer error.
    """
    if order_type == "MARKET":
        if price is not None:
            raise ToolError("price applies only to LIMIT, STOP, and MARKET_IF_TOUCHED orders")
        resolved_tif = time_in_force or "FOK"
        if resolved_tif not in _MARKET_TIME_IN_FORCE:
            raise ToolError("MARKET orders accept time_in_force 'FOK' or 'IOC' only")
    else:
        if price is None:
            raise ToolError(f"{order_type} orders require a price")
        resolved_tif = time_in_force or "GTC"
        if resolved_tif not in _PENDING_TIME_IN_FORCE:
            raise ToolError(f"{order_type} orders accept time_in_force 'GTC', 'GFD', or 'GTD' only")
    if resolved_tif == "GTD" and gtd_time is None:
        raise ToolError("time_in_force 'GTD' requires gtd_time")
    if stop_loss_price is not None and stop_loss_distance is not None:
        raise ToolError("provide at most one of stop_loss_price and stop_loss_distance")

    order: dict[str, Any] = {
        "type": order_type,
        "instrument": instrument,
        "units": units,
        "timeInForce": resolved_tif,
        "positionFill": position_fill,
    }
    if price is not None:
        order["price"] = price
    if gtd_time is not None:
        order["gtdTime"] = gtd_time
    if take_profit_price is not None:
        order["takeProfitOnFill"] = {"price": take_profit_price, "timeInForce": "GTC"}
    if stop_loss_price is not None:
        order["stopLossOnFill"] = {"price": stop_loss_price, "timeInForce": "GTC"}
    elif stop_loss_distance is not None:
        order["stopLossOnFill"] = {"distance": stop_loss_distance, "timeInForce": "GTC"}
    if trailing_stop_distance is not None:
        order["trailingStopLossOnFill"] = {
            "distance": trailing_stop_distance,
            "timeInForce": "GTC",
        }
    return order


# Shared parameter types for the create/replace body. Values follow the v20
# conventions: every number is a decimal string, every timestamp is RFC3339.
_OrderTypeParam = Annotated[
    Literal["MARKET", "LIMIT", "STOP", "MARKET_IF_TOUCHED"],
    Field(
        description=(
            "Order type. MARKET executes immediately; LIMIT fills at the price "
            "or better; STOP triggers at the price or worse; MARKET_IF_TOUCHED "
            "fires a market order when the price is first touched."
        )
    ),
]
_InstrumentParam = Annotated[
    str,
    Field(description="Instrument name in Oanda underscore format, e.g. 'EUR_USD' or 'EU50_EUR'."),
]
_UnitsParam = Annotated[
    str,
    Field(
        description=(
            "Signed units as a decimal string: positive buys (long), negative "
            "sells (short), e.g. '100' or '-2500'."
        )
    ),
]
_PriceParam = Annotated[
    str | None,
    Field(
        description=(
            "Order price as a decimal string, e.g. '1.10345'. Required for "
            "LIMIT, STOP, and MARKET_IF_TOUCHED; must be omitted for MARKET. "
            "Format it to the instrument's displayPrecision (from "
            "list_account_instruments) or the API rejects the order with a "
            "PRICE_PRECISION error."
        )
    ),
]
_TimeInForceParam = Annotated[
    Literal["FOK", "IOC", "GTC", "GFD", "GTD"] | None,
    Field(
        description=(
            "Time in force. MARKET accepts 'FOK' (default) or 'IOC'; LIMIT, "
            "STOP, and MARKET_IF_TOUCHED accept 'GTC' (default), 'GFD', or "
            "'GTD'. 'GTD' additionally requires gtd_time."
        )
    ),
]
_GtdTimeParam = Annotated[
    str | None,
    Field(
        description=(
            "RFC3339 expiry timestamp, e.g. '2026-08-01T12:00:00Z'. Required "
            "when time_in_force is 'GTD', ignored otherwise."
        )
    ),
]
_PositionFillParam = Annotated[
    Literal["DEFAULT", "OPEN_ONLY", "REDUCE_FIRST", "REDUCE_ONLY"],
    Field(description="How the fill may affect the existing position."),
]
_TakeProfitPriceParam = Annotated[
    str | None,
    Field(
        description=(
            "Absolute take-profit price (decimal string) attached to the trade "
            "on fill. The API accepts absolute prices only; to set a "
            "fill-relative target, place the order first and attach the take "
            "profit at the actual fill price."
        )
    ),
]
_StopLossPriceParam = Annotated[
    str | None,
    Field(
        description=(
            "Absolute stop-loss price (decimal string) attached to the trade "
            "on fill. Mutually exclusive with stop_loss_distance."
        )
    ),
]
_StopLossDistanceParam = Annotated[
    str | None,
    Field(
        description=(
            "Stop-loss distance in price units from the fill price (positive "
            "decimal string). Mutually exclusive with stop_loss_price."
        )
    ),
]
_TrailingStopDistanceParam = Annotated[
    str | None,
    Field(
        description=(
            "Trailing stop-loss distance in price units from the fill price "
            "(positive decimal string), attached to the trade on fill."
        )
    ),
]
_OrderSpecifierParam = Annotated[
    str,
    Field(
        description=(
            "Order identifier: the Oanda-assigned order ID (e.g. '6372') or a "
            "client-assigned ID prefixed with '@' (e.g. '@my_order_1')."
        )
    ),
]


def register(mcp: FastMCP, client: ApiClient, settings: Settings) -> None:
    """Register the order tools on ``mcp``.

    All tools operate on the configured account (or the token's first account
    when none is configured).
    """

    async def list_orders(
        instrument: Annotated[
            str | None,
            Field(description="Only orders for this instrument, e.g. 'EUR_USD'."),
        ] = None,
        state: Annotated[
            Literal["PENDING", "FILLED", "TRIGGERED", "CANCELLED", "ALL"],
            Field(description="Order state to filter by."),
        ] = "PENDING",
        limit: Annotated[
            int, Field(description="Maximum number of orders to return.", ge=1, le=500)
        ] = 50,
        before_id: Annotated[
            str | None,
            Field(
                description=(
                    "Return only orders with an ID before this order ID; omit "
                    "for the most recent. Use for paging backwards through "
                    "history."
                )
            ),
        ] = None,
    ) -> dict[str, Any]:
        """List orders on the configured account, most recent first.

        Returns ``{"orders": [...], "lastTransactionID": "..."}``. Each order
        carries ``id``, ``type`` (e.g. ``MARKET``, ``LIMIT``, ``STOP``,
        ``MARKET_IF_TOUCHED``, ``TAKE_PROFIT``, ``STOP_LOSS``,
        ``TRAILING_STOP_LOSS``), ``instrument``, ``units`` (signed decimal
        string: positive = long, negative = short), ``price`` (decimal
        string), ``state`` (``PENDING``, ``FILLED``, ``TRIGGERED``, or
        ``CANCELLED``), ``timeInForce``, ``createTime`` (RFC3339), and — where
        applicable — fill/cancel bookkeeping and attached
        ``takeProfitOnFill``/``stopLossOnFill``/``trailingStopLossOnFill``
        details.
        """
        account_id = await client.account_id()
        payload = await client.request(
            "GET",
            f"/v3/accounts/{account_id}/orders",
            params={
                "instrument": instrument,
                "state": state,
                "count": limit,
                "beforeID": before_id,
            },
        )
        payload = payload or {}
        orders = payload.get("orders") or []
        return {
            "orders": [_trim(order, _ORDER_FIELDS) for order in orders[:limit]],
            "lastTransactionID": payload.get("lastTransactionID"),
        }

    async def list_pending_orders(
        limit: Annotated[int, Field(description="Maximum number of orders to return.", ge=1)] = 50,
    ) -> dict[str, Any]:
        """List every pending (not yet filled or cancelled) order on the account.

        Returns ``{"orders": [...], "lastTransactionID": "..."}`` with the
        same per-order fields as ``list_orders``: ``id``, ``type``,
        ``instrument``, ``units`` (signed decimal string), ``price`` (decimal
        string), ``timeInForce``, ``createTime`` (RFC3339), and any attached
        on-fill details.
        """
        account_id = await client.account_id()
        payload = await client.request("GET", f"/v3/accounts/{account_id}/pendingOrders")
        payload = payload or {}
        orders = payload.get("orders") or []
        return {
            "orders": [_trim(order, _ORDER_FIELDS) for order in orders[:limit]],
            "lastTransactionID": payload.get("lastTransactionID"),
        }

    async def get_order(order_specifier: _OrderSpecifierParam) -> dict[str, Any]:
        """Get a single order by ID or client-assigned ID.

        Returns ``{"order": {...}, "lastTransactionID": "..."}`` with the same
        fields as ``list_orders`` entries: ``id``, ``type``, ``instrument``,
        ``units`` (signed decimal string), ``price`` (decimal string),
        ``state`` (``PENDING``, ``FILLED``, ``TRIGGERED``, ``CANCELLED``),
        ``timeInForce``, RFC3339 timestamps, and fill/cancel bookkeeping such
        as ``tradeOpenedID`` or ``cancellingTransactionID`` when present.
        """
        account_id = await client.account_id()
        payload = await client.request(
            "GET",
            f"/v3/accounts/{account_id}/orders/{quote_path_segment(order_specifier)}",
        )
        payload = payload or {}
        return {
            "order": _trim(payload.get("order"), _ORDER_FIELDS),
            "lastTransactionID": payload.get("lastTransactionID"),
        }

    mcp.tool(list_orders, annotations={"readOnlyHint": True})
    mcp.tool(list_pending_orders, annotations={"readOnlyHint": True})
    mcp.tool(get_order, annotations={"readOnlyHint": True})

    if settings.enable_trading:

        async def create_order(
            order_type: _OrderTypeParam,
            instrument: _InstrumentParam,
            units: _UnitsParam,
            price: _PriceParam = None,
            time_in_force: _TimeInForceParam = None,
            gtd_time: _GtdTimeParam = None,
            position_fill: _PositionFillParam = "DEFAULT",
            take_profit_price: _TakeProfitPriceParam = None,
            stop_loss_price: _StopLossPriceParam = None,
            stop_loss_distance: _StopLossDistanceParam = None,
            trailing_stop_distance: _TrailingStopDistanceParam = None,
        ) -> dict[str, Any]:
            """Create an order on the configured account. WRITE operation.

            Returns ``{"order_create": {...}, "order_fill": {...} | null,
            "order_cancel": {...} | null, "lastTransactionID": "..."}``.
            ``order_fill`` is present when the order filled immediately (its
            ``tradeOpened`` carries the new trade's ``tradeID``, fill ``price``
            and ``units``; ``tradeReduced``/``tradesClosed`` appear when the
            fill reduced or closed existing trades). Important quirk: the API
            can accept the order and still cancel it in the same response —
            e.g. a FOK MARKET order cancelled for ``INSUFFICIENT_MARGIN`` or
            ``MARKET_HALTED`` — so always check ``order_cancel`` (and its
            ``reason``) before treating the order as filled or working.
            Prices and units are decimal strings; timestamps are RFC3339.
            """
            order = _build_order_request(
                order_type,
                instrument,
                units,
                price,
                time_in_force,
                gtd_time,
                position_fill,
                take_profit_price,
                stop_loss_price,
                stop_loss_distance,
                trailing_stop_distance,
            )
            account_id = await client.account_id()
            payload = await client.request(
                "POST",
                f"/v3/accounts/{account_id}/orders",
                json_body={"order": order},
            )
            payload = payload or {}
            return {
                "order_create": _trim(payload.get("orderCreateTransaction"), _CREATE_TX_FIELDS),
                "order_fill": _trim(payload.get("orderFillTransaction"), _FILL_TX_FIELDS),
                "order_cancel": _trim(payload.get("orderCancelTransaction"), _CANCEL_TX_FIELDS),
                "lastTransactionID": payload.get("lastTransactionID"),
            }

        async def replace_order(
            order_specifier: _OrderSpecifierParam,
            order_type: _OrderTypeParam,
            instrument: _InstrumentParam,
            units: _UnitsParam,
            price: _PriceParam = None,
            time_in_force: _TimeInForceParam = None,
            gtd_time: _GtdTimeParam = None,
            position_fill: _PositionFillParam = "DEFAULT",
            take_profit_price: _TakeProfitPriceParam = None,
            stop_loss_price: _StopLossPriceParam = None,
            stop_loss_distance: _StopLossDistanceParam = None,
            trailing_stop_distance: _TrailingStopDistanceParam = None,
        ) -> dict[str, Any]:
            """Replace a pending order atomically (cancel + create). WRITE operation.

            The replacement is a full order specification, not a partial
            update — supply every field the new order should have. Returns
            ``{"replaced_order_cancel": {...}, "order_create": {...},
            "order_fill": {...} | null, "order_cancel": {...} | null,
            "lastTransactionID": "..."}``. ``replaced_order_cancel`` is the
            cancellation of the old order; ``order_cancel`` (from the API's
            ``replacingOrderCancelTransaction``) is non-null when the NEW
            order was itself cancelled immediately — check it before treating
            the replacement as working. Prices and units are decimal strings;
            timestamps are RFC3339.
            """
            order = _build_order_request(
                order_type,
                instrument,
                units,
                price,
                time_in_force,
                gtd_time,
                position_fill,
                take_profit_price,
                stop_loss_price,
                stop_loss_distance,
                trailing_stop_distance,
            )
            account_id = await client.account_id()
            payload = await client.request(
                "PUT",
                f"/v3/accounts/{account_id}/orders/{quote_path_segment(order_specifier)}",
                json_body={"order": order},
            )
            payload = payload or {}
            return {
                "replaced_order_cancel": _trim(
                    payload.get("orderCancelTransaction"), _CANCEL_TX_FIELDS
                ),
                "order_create": _trim(payload.get("orderCreateTransaction"), _CREATE_TX_FIELDS),
                "order_fill": _trim(payload.get("orderFillTransaction"), _FILL_TX_FIELDS),
                "order_cancel": _trim(
                    payload.get("replacingOrderCancelTransaction"), _CANCEL_TX_FIELDS
                ),
                "lastTransactionID": payload.get("lastTransactionID"),
            }

        async def cancel_order(order_specifier: _OrderSpecifierParam) -> dict[str, Any]:
            """Cancel a pending order. WRITE operation.

            Returns ``{"order_cancel": {...}, "lastTransactionID": "..."}``
            where ``order_cancel`` is the resulting OrderCancelTransaction
            (``id``, ``orderID``, ``reason`` — ``CLIENT_REQUEST`` on success —
            and ``time`` as an RFC3339 timestamp). Fails with HTTP 404 when
            the order does not exist or is no longer pending.
            """
            account_id = await client.account_id()
            payload = await client.request(
                "PUT",
                f"/v3/accounts/{account_id}/orders/{quote_path_segment(order_specifier)}/cancel",
            )
            payload = payload or {}
            return {
                "order_cancel": _trim(payload.get("orderCancelTransaction"), _CANCEL_TX_FIELDS),
                "lastTransactionID": payload.get("lastTransactionID"),
            }

        mcp.tool(create_order)
        mcp.tool(replace_order)
        mcp.tool(cancel_order)
