"""Pricing tools: live quotes and the most recent candle per instrument.

Read-only domain — it registers no write tools, so nothing here is gated on
``settings.enable_trading``.
"""

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from oanda_mcp.client import ApiClient
from oanda_mcp.config import Settings


def _best_price(quotes: list[dict[str, Any]] | None) -> str | None:
    """First (best) price of a bids/asks ladder, or ``None`` when empty."""
    if not quotes:
        return None
    price = quotes[0].get("price")
    return str(price) if price is not None else None


def _trim_price(price: dict[str, Any]) -> dict[str, Any]:
    """Reduce a ClientPrice to the fields a trader acts on."""
    bid = _best_price(price.get("bids"))
    ask = _best_price(price.get("asks"))
    spread = round(float(ask) - float(bid), 6) if bid is not None and ask is not None else None
    return {
        "instrument": price.get("instrument"),
        "time": price.get("time"),
        "tradeable": price.get("tradeable"),
        "bid": bid,
        "ask": ask,
        "spread": spread,
    }


def _trim_candle(candle: dict[str, Any]) -> dict[str, Any]:
    """Keep a candle's time, completeness, volume, and OHLC per price side."""
    trimmed: dict[str, Any] = {
        "time": candle.get("time"),
        "complete": candle.get("complete"),
        "volume": candle.get("volume"),
    }
    for side in ("bid", "mid", "ask"):
        if side in candle:
            trimmed[side] = candle[side]
    return trimmed


def register(mcp: FastMCP, client: ApiClient, settings: Settings) -> None:
    """Register the pricing tools on ``mcp``. This domain is read-only."""

    async def get_pricing(
        instruments: Annotated[
            list[str],
            Field(
                description=(
                    "Instrument names in Oanda underscore format, e.g. ['EUR_USD', 'USD_JPY']."
                ),
                min_length=1,
            ),
        ],
    ) -> dict[str, Any]:
        """Get the current price for one or more instruments.

        Returns ``{"time": ..., "prices": [...]}`` with one entry per requested
        instrument: ``instrument``, ``time`` (RFC3339 timestamp of the quote),
        ``tradeable`` (false outside trading hours or when the market is
        halted), ``bid`` and ``ask`` (best available prices, decimal strings;
        null when that side has no liquidity), and ``spread`` (ask minus bid as
        a number in price units; null when either side is missing).
        """
        account = await client.account_id()
        payload = await client.request(
            "GET",
            f"/v3/accounts/{account}/pricing",
            params={"instruments": ",".join(instruments)},
        )
        payload = payload or {}
        return {
            "time": payload.get("time"),
            "prices": [_trim_price(price) for price in payload.get("prices") or []],
        }

    async def get_latest_candles(
        candle_specifications: Annotated[
            list[str],
            Field(
                description=(
                    "Candle specifications, each formatted INSTRUMENT:GRANULARITY:PRICE, "
                    "e.g. 'EUR_USD:M10:BM'. Granularity is one of S5, S10, S15, S30, M1, "
                    "M2, M4, M5, M10, M15, M30, H1, H2, H3, H4, H6, H8, H12, D, W, M. "
                    "Price is a combination of M (mid), B (bid), A (ask)."
                ),
                min_length=1,
            ),
        ],
    ) -> dict[str, Any]:
        """Get the most recent candle for each requested instrument/granularity.

        Returns ``{"latest_candles": [...]}`` with one entry per specification:
        ``instrument``, ``granularity``, and ``candles`` — each candle carries
        ``time`` (RFC3339), ``complete`` (false means the candle is still
        forming), ``volume`` (number of price ticks), and an ``o``/``h``/``l``/
        ``c`` object per requested price side (``bid``/``mid``/``ask``) with
        prices as decimal strings.
        """
        account = await client.account_id()
        payload = await client.request(
            "GET",
            f"/v3/accounts/{account}/candles/latest",
            params={"candleSpecifications": ",".join(candle_specifications)},
        )
        payload = payload or {}
        return {
            "latest_candles": [
                {
                    "instrument": entry.get("instrument"),
                    "granularity": entry.get("granularity"),
                    "candles": [_trim_candle(candle) for candle in entry.get("candles") or []],
                }
                for entry in payload.get("latestCandles") or []
            ]
        }

    mcp.tool(get_pricing, annotations={"readOnlyHint": True})
    mcp.tool(get_latest_candles, annotations={"readOnlyHint": True})
