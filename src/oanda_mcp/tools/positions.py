"""Position-domain tools: per-instrument aggregate exposure and (gated) closes.

A position is the account's net aggregate per instrument, split into a ``long``
and a ``short`` side (both can be open at once on hedging accounts). Read
tools are always registered; ``close_position`` exists only when
``settings.enable_trading`` is true.
"""

import math
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from oanda_mcp.client import ApiClient, quote_path_segment
from oanda_mcp.config import Settings

_POSITION_FIELDS: tuple[str, ...] = (
    "instrument",
    "pl",
    "unrealizedPL",
    "marginUsed",
    "financing",
)

_SIDE_FIELDS: tuple[str, ...] = (
    "units",
    "averagePrice",
    "tradeIDs",
    "pl",
    "unrealizedPL",
)

# MarketOrderTransaction fields a trader acts on after a position close fill.
_FILL_FIELDS: tuple[str, ...] = (
    "id",
    "orderID",
    "instrument",
    "units",
    "price",
    "pl",
    "financing",
    "reason",
    "time",
)

_CANCEL_FIELDS: tuple[str, ...] = (
    "id",
    "orderID",
    "reason",
    "time",
)


def _trim_side(side: dict[str, Any]) -> dict[str, Any]:
    return {key: side[key] for key in _SIDE_FIELDS if key in side}


def _trim_position(position: dict[str, Any]) -> dict[str, Any]:
    """Keep the aggregate P/L fields plus a trimmed long and short side."""
    trimmed = {key: position[key] for key in _POSITION_FIELDS if key in position}
    for side in ("long", "short"):
        if isinstance(position.get(side), dict):
            trimmed[side] = _trim_side(position[side])
    return trimmed


def _trim_transaction(
    transaction: dict[str, Any] | None, fields: tuple[str, ...]
) -> dict[str, Any] | None:
    if not isinstance(transaction, dict):
        return None
    return {key: transaction[key] for key in fields if key in transaction}


def _validate_close_units(value: str, param: str) -> None:
    """Accept ``ALL``, ``NONE``, or a positive decimal number of units."""
    if value in ("ALL", "NONE"):
        return
    try:
        units = float(value)
    except ValueError:
        units = math.nan
    if not math.isfinite(units) or units <= 0:
        raise ToolError(f"{param} must be 'ALL', 'NONE', or a positive number of units")


def register(mcp: FastMCP, client: ApiClient, settings: Settings) -> None:
    """Register the position tools on ``mcp``.

    All tools operate on the configured account (or the token's first account
    when none is configured).
    """

    async def list_positions(
        limit: Annotated[
            int, Field(description="Maximum number of positions to return.", ge=1)
        ] = 100,
    ) -> dict[str, Any]:
        """List every position the account has ever held, one per instrument.

        Includes flat positions kept for their lifetime P/L; use
        ``list_open_positions`` for current exposure only. Returns
        ``{"positions": [...], "lastTransactionID": "..."}`` where each
        position has ``instrument``, lifetime realized ``pl``,
        ``unrealizedPL``, ``marginUsed``, ``financing`` (all decimal strings
        in the account's home currency), and a ``long`` and ``short`` side.
        Each side has ``units`` (decimal string; positive on the long side,
        negative on the short side, ``"0"`` when flat), ``averagePrice``
        (only while open), ``tradeIDs`` (the open trades composing the side),
        ``pl``, and ``unrealizedPL``.
        """
        account_id = await client.account_id()
        payload = await client.request("GET", f"/v3/accounts/{account_id}/positions")
        payload = payload or {}
        rows = payload.get("positions") or []
        return {
            "positions": [_trim_position(row) for row in rows[:limit]],
            "lastTransactionID": payload.get("lastTransactionID"),
        }

    async def list_open_positions(
        limit: Annotated[
            int, Field(description="Maximum number of positions to return.", ge=1)
        ] = 100,
    ) -> dict[str, Any]:
        """List the account's open positions — instruments with at least one open trade.

        Returns ``{"positions": [...], "lastTransactionID": "..."}`` with the
        same shape as ``list_positions``: per instrument, realized ``pl``,
        ``unrealizedPL``, ``marginUsed``, ``financing`` (decimal strings in
        the home currency), and ``long``/``short`` sides carrying ``units``
        (decimal string; negative on the short side), ``averagePrice``,
        ``tradeIDs``, ``pl``, and ``unrealizedPL``.
        """
        account_id = await client.account_id()
        payload = await client.request("GET", f"/v3/accounts/{account_id}/openPositions")
        payload = payload or {}
        rows = payload.get("positions") or []
        return {
            "positions": [_trim_position(row) for row in rows[:limit]],
            "lastTransactionID": payload.get("lastTransactionID"),
        }

    async def get_position(
        instrument: Annotated[
            str, Field(description="Instrument name, e.g. 'EUR_USD' or 'EU50_EUR'.")
        ],
    ) -> dict[str, Any]:
        """Get the account's position for one instrument (open or flat).

        Returns ``{"position": {...}, "lastTransactionID": "..."}``. The
        position has ``instrument``, lifetime realized ``pl``,
        ``unrealizedPL``, ``marginUsed``, ``financing`` (decimal strings in
        the home currency), and ``long``/``short`` sides with ``units``
        (decimal string; negative on the short side, ``"0"`` when flat),
        ``averagePrice`` (only while open), ``tradeIDs``, ``pl``, and
        ``unrealizedPL``. Requesting an instrument the account has never
        traded fails with HTTP 404.
        """
        account_id = await client.account_id()
        payload = await client.request(
            "GET",
            f"/v3/accounts/{account_id}/positions/{quote_path_segment(instrument)}",
        )
        payload = payload or {}
        return {
            "position": _trim_position(payload.get("position") or {}),
            "lastTransactionID": payload.get("lastTransactionID"),
        }

    mcp.tool(list_positions, annotations={"readOnlyHint": True})
    mcp.tool(list_open_positions, annotations={"readOnlyHint": True})
    mcp.tool(get_position, annotations={"readOnlyHint": True})

    if settings.enable_trading:

        async def close_position(
            instrument: Annotated[
                str, Field(description="Instrument name, e.g. 'EUR_USD' or 'EU50_EUR'.")
            ],
            long_units: Annotated[
                str | None,
                Field(
                    description=(
                        "How much of the long side to close: 'ALL', 'NONE', or a "
                        "positive number of units as a decimal string, e.g. '100'. "
                        "Omit to leave the long side untouched."
                    )
                ),
            ] = None,
            short_units: Annotated[
                str | None,
                Field(
                    description=(
                        "How much of the short side to close: 'ALL', 'NONE', or a "
                        "positive number of units as a decimal string, e.g. '100' "
                        "(positive even though short units are held as negative). "
                        "Omit to leave the short side untouched."
                    )
                ),
            ] = None,
        ) -> dict[str, Any]:
            """Close out the long and/or short side of an instrument's position.

            At least one of ``long_units`` and ``short_units`` must be given;
            each accepts ``'ALL'`` (close the side entirely), ``'NONE'``
            (leave it untouched), or a positive decimal string for a partial
            close. The API defaults an omitted side to ``'ALL'``, so a side
            the caller does not specify is sent as ``'NONE'`` explicitly —
            this tool never closes a side that was not asked for. Each closed
            side is exited with a market order. Returns per side the trimmed
            fill transaction (``longOrderFill``/``shortOrderFill``: ``id``,
            ``orderID``, ``instrument``, ``units``, ``price``, realized
            ``pl``, ``financing``, ``reason``, ``time`` as RFC3339) and, when
            the close order was cancelled instead of filled (e.g. market
            halted), the cancel transaction
            (``longOrderCancel``/``shortOrderCancel`` with its ``reason``),
            plus ``lastTransactionID``. Closing a side with nothing open
            fails with HTTP 400 and a CLOSEOUT reject message.
            """
            if long_units is None and short_units is None:
                raise ToolError("provide at least one of: long_units, short_units")
            if long_units is not None:
                _validate_close_units(long_units, "long_units")
            if short_units is not None:
                _validate_close_units(short_units, "short_units")
            body = {
                "longUnits": long_units if long_units is not None else "NONE",
                "shortUnits": short_units if short_units is not None else "NONE",
            }
            account_id = await client.account_id()
            payload = await client.request(
                "PUT",
                f"/v3/accounts/{account_id}/positions/{quote_path_segment(instrument)}/close",
                json_body=body,
            )
            payload = payload or {}
            return {
                "longOrderFill": _trim_transaction(
                    payload.get("longOrderFillTransaction"), _FILL_FIELDS
                ),
                "longOrderCancel": _trim_transaction(
                    payload.get("longOrderCancelTransaction"), _CANCEL_FIELDS
                ),
                "shortOrderFill": _trim_transaction(
                    payload.get("shortOrderFillTransaction"), _FILL_FIELDS
                ),
                "shortOrderCancel": _trim_transaction(
                    payload.get("shortOrderCancelTransaction"), _CANCEL_FIELDS
                ),
                "lastTransactionID": payload.get("lastTransactionID"),
            }

        mcp.tool(close_position)
