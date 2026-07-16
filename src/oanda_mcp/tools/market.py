"""Instrument market-data tools: candles, order book, position book.

All three endpoints are instrument-scoped (``/v3/instruments/{instrument}/...``)
and read-only, so this domain registers no write tools and never consults
``settings.enable_trading``.
"""

import bisect
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from oanda_mcp.client import ApiClient, quote_path_segment
from oanda_mcp.config import Settings

Granularity = Literal[
    "S5",
    "S10",
    "S15",
    "S30",
    "M1",
    "M2",
    "M4",
    "M5",
    "M10",
    "M15",
    "M30",
    "H1",
    "H2",
    "H3",
    "H4",
    "H6",
    "H8",
    "H12",
    "D",
    "W",
    "M",
]

PriceComponent = Literal["M", "B", "A", "MB", "MA", "BA", "MBA"]

_COMPONENT_KEYS = ("mid", "bid", "ask")


def _map_candle(candle: dict[str, Any]) -> dict[str, Any]:
    """Trim one Candlestick to time/OHLC/volume/complete.

    With a single price component the o/h/l/c strings are flattened to the top
    level; with several components (e.g. ``price="BA"``) each present component
    keeps its own ``{o, h, l, c}`` dict under ``mid``/``bid``/``ask``.
    """
    out: dict[str, Any] = {
        "time": candle.get("time"),
        "volume": candle.get("volume"),
        "complete": candle.get("complete"),
    }
    components = {key: candle[key] for key in _COMPONENT_KEYS if key in candle}
    if len(components) == 1:
        ohlc = next(iter(components.values()))
        for field in ("o", "h", "l", "c"):
            out[field] = ohlc.get(field)
    else:
        for name, ohlc in components.items():
            out[name] = {field: ohlc.get(field) for field in ("o", "h", "l", "c")}
    return out


def _trim_buckets(book: dict[str, Any], depth: int) -> list[dict[str, Any]]:
    """Keep at most ``depth`` price buckets on each side of the current price.

    Buckets are sorted by price and sliced around the book's snapshot price
    (up to ``2 * depth + 1`` buckets). If any price fails to parse, the first
    ``2 * depth + 1`` buckets are returned unsorted instead of failing.
    """
    buckets = book.get("buckets") or []
    try:
        current = float(book["price"])
        parsed = sorted((float(bucket["price"]), bucket) for bucket in buckets)
    except (KeyError, TypeError, ValueError):
        selected = buckets[: 2 * depth + 1]
    else:
        prices = [price for price, _ in parsed]
        centre = bisect.bisect_left(prices, current)
        low = max(centre - depth, 0)
        selected = [bucket for _, bucket in parsed[low : centre + depth + 1]]
    return [
        {
            "price": bucket.get("price"),
            "longCountPercent": bucket.get("longCountPercent"),
            "shortCountPercent": bucket.get("shortCountPercent"),
        }
        for bucket in selected
    ]


def _map_book(book: dict[str, Any], depth: int) -> dict[str, Any]:
    """Trim an order/position book payload to the fields a trader needs."""
    return {
        "instrument": book.get("instrument"),
        "time": book.get("time"),
        "price": book.get("price"),
        "bucketWidth": book.get("bucketWidth"),
        "buckets": _trim_buckets(book, depth),
    }


def register(mcp: FastMCP, client: ApiClient, settings: Settings) -> None:
    """Register the market domain's tools (all read-only, no write tools)."""

    async def get_candles(
        instrument: Annotated[
            str, Field(description="Instrument name in Oanda format, e.g. EUR_USD or DE30_EUR.")
        ],
        granularity: Annotated[
            Granularity,
            Field(
                description=(
                    "Candle granularity: S/M/H prefixes are seconds/minutes/hours "
                    "(e.g. M15, H4), D=day, W=week, M=month."
                )
            ),
        ] = "H1",
        count: Annotated[
            int | None,
            Field(
                ge=1,
                le=5000,
                description=(
                    "Number of most-recent candles to return (max 5000; API default 500). "
                    "Must not be combined with both from_time and to_time."
                ),
            ),
        ] = None,
        from_time: Annotated[
            str | None,
            Field(description="Range start, RFC3339 timestamp (sent as the 'from' parameter)."),
        ] = None,
        to_time: Annotated[
            str | None,
            Field(description="Range end, RFC3339 timestamp (sent as the 'to' parameter)."),
        ] = None,
        price: Annotated[
            PriceComponent,
            Field(
                description=(
                    "Price components to include: M=mid, B=bid, A=ask, or a combination "
                    "such as BA or MBA."
                )
            ),
        ] = "M",
        smooth: Annotated[
            bool,
            Field(description="When true, each candle's open equals the previous candle's close."),
        ] = False,
        include_first: Annotated[
            bool,
            Field(
                description=(
                    "Only used with from_time: include the candle covering from_time "
                    "(which may be timestamped before it). Set false when paginating "
                    "with from_time equal to the last candle already received."
                )
            ),
        ] = True,
    ) -> dict[str, Any]:
        """Fetch OHLC candles for an instrument.

        Specify either ``count`` (most recent N candles) or an RFC3339
        ``from_time``/``to_time`` range; ``count`` cannot be combined with both
        range bounds. A single request returns at most 5000 candles — for
        longer ranges, paginate by setting ``from_time`` to the exact ``time``
        of the last candle received (not past it, or one candle is silently
        skipped) and ``include_first`` to false (note the excluded first
        candle still counts against ``count``, so a full follow-up page holds
        one candle fewer).

        Returns ``instrument``, ``granularity`` and ``candles``: each candle
        has an RFC3339 ``time``, ``volume`` (number of price ticks, an
        integer), ``complete`` (false means the candle is still forming — skip
        it for analysis), and o/h/l/c prices as decimal strings (flattened for
        a single price component, nested under ``mid``/``bid``/``ask`` when
        several components are requested).
        """
        if count is not None and from_time is not None and to_time is not None:
            raise ToolError(
                "count cannot be combined with both from_time and to_time; "
                "use count alone, or a time range"
            )
        params: dict[str, Any] = {
            "granularity": granularity,
            "price": price,
            "count": count,
            "from": from_time,
            "to": to_time,
            "smooth": smooth,
            "includeFirst": include_first if from_time is not None else None,
        }
        payload = await client.request(
            "GET",
            f"/v3/instruments/{quote_path_segment(instrument)}/candles",
            params=params,
        )
        payload = payload or {}
        return {
            "instrument": payload.get("instrument"),
            "granularity": payload.get("granularity"),
            "candles": [_map_candle(candle) for candle in payload.get("candles") or []],
        }

    async def get_order_book(
        instrument: Annotated[
            str, Field(description="Instrument name in Oanda format, e.g. EUR_USD.")
        ],
        time: Annotated[
            str | None,
            Field(
                description=(
                    "RFC3339 timestamp of the snapshot to fetch; omit for the most recent snapshot."
                )
            ),
        ] = None,
        depth: Annotated[
            int,
            Field(
                ge=1,
                le=500,
                description="Price buckets to keep on each side of the current price.",
            ),
        ] = 20,
    ) -> dict[str, Any]:
        """Fetch the aggregate pending-order book for an instrument.

        Snapshots are produced periodically, not per-tick. Returns ``time``
        (RFC3339), the snapshot ``price`` and ``bucketWidth`` (decimal
        strings), and ``buckets`` trimmed to at most ``depth`` buckets on each
        side of the current price. Each bucket has a ``price`` and
        ``longCountPercent``/``shortCountPercent`` — the percentage of pending
        long/short orders at that price, as decimal strings (e.g. ``"0.2543"``
        means 0.2543%).
        """
        payload = await client.request(
            "GET",
            f"/v3/instruments/{quote_path_segment(instrument)}/orderBook",
            params={"time": time},
        )
        return _map_book((payload or {}).get("orderBook") or {}, depth)

    async def get_position_book(
        instrument: Annotated[
            str, Field(description="Instrument name in Oanda format, e.g. EUR_USD.")
        ],
        time: Annotated[
            str | None,
            Field(
                description=(
                    "RFC3339 timestamp of the snapshot to fetch; omit for the most recent snapshot."
                )
            ),
        ] = None,
        depth: Annotated[
            int,
            Field(
                ge=1,
                le=500,
                description="Price buckets to keep on each side of the current price.",
            ),
        ] = 20,
    ) -> dict[str, Any]:
        """Fetch the aggregate open-position book for an instrument.

        Same shape as the order book, but each bucket's
        ``longCountPercent``/``shortCountPercent`` (decimal strings, e.g.
        ``"0.2543"`` means 0.2543%) describe open positions held at that price
        rather than pending orders. Returns ``time`` (RFC3339), the snapshot
        ``price`` and ``bucketWidth`` (decimal strings), and ``buckets``
        trimmed to at most ``depth`` buckets on each side of the current price.
        """
        payload = await client.request(
            "GET",
            f"/v3/instruments/{quote_path_segment(instrument)}/positionBook",
            params={"time": time},
        )
        return _map_book((payload or {}).get("positionBook") or {}, depth)

    mcp.tool(get_candles, annotations={"readOnlyHint": True})
    mcp.tool(get_order_book, annotations={"readOnlyHint": True})
    mcp.tool(get_position_book, annotations={"readOnlyHint": True})
