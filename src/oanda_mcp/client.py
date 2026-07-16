"""Async HTTP client for the Oanda v20 REST API.

Behaviours every tool relies on:

* The token travels only in the ``Authorization`` header — never in URLs — and
  errors raised here never contain header or credential material.
* ``Accept-Datetime-Format: RFC3339`` is always sent, so every timestamp in a
  response is an RFC3339 string rather than a Unix fractional-seconds string.
* Any 2xx status is success: reads and cancels return 200, order create and
  replace return 201. Note that a 201 on order create can still carry an
  ``orderCancelTransaction`` (e.g. a FOK market order cancelled for
  ``INSUFFICIENT_MARGIN``) — order tools must inspect it before reading the
  fill.
* On HTTP 429 the request is retried exactly once, honouring the
  ``Retry-After`` header when it is a finite, non-negative number of seconds
  (capped at 30; defaulting to 2 seconds when absent or unparseable).
"""

import asyncio
import math
from typing import Any
from urllib.parse import quote

import httpx
from fastmcp.exceptions import ToolError

from oanda_mcp.config import Settings

_DEFAULT_RETRY_AFTER = 2.0
_MAX_RETRY_AFTER = 30.0
_TIMEOUT_SECONDS = 30.0


def quote_path_segment(value: str) -> str:
    """Percent-encode a caller-supplied value used as a single URL path segment.

    Reserved characters such as ``/`` and ``?`` are escaped so a specifier can
    never retarget the request to a different endpoint; ``@`` stays literal
    because client-assigned order/trade IDs are passed as ``@<id>``.
    """
    return quote(value, safe="@")


def _retry_after_seconds(response: httpx.Response) -> float:
    """Delay before retrying a rate-limited request, from ``Retry-After``.

    Honoured only when the header is a finite, non-negative number of
    seconds, capped at :data:`_MAX_RETRY_AFTER` so a hostile or buggy
    intermediary cannot stall a tool call indefinitely (``float`` accepts
    ``inf``/``nan``, and huge exponents overflow to ``inf``); anything else
    falls back to :data:`_DEFAULT_RETRY_AFTER`.
    """
    raw = response.headers.get("Retry-After")
    if raw is None:
        return _DEFAULT_RETRY_AFTER
    try:
        seconds = float(raw)
    except ValueError:
        return _DEFAULT_RETRY_AFTER
    if not math.isfinite(seconds) or seconds < 0:
        return _DEFAULT_RETRY_AFTER
    return min(seconds, _MAX_RETRY_AFTER)


def _error_message(response: httpx.Response) -> str:
    """Extract the API's ``errorMessage``, falling back to generic text.

    The fallback deliberately avoids the response body: error text must never
    leak anything sensitive, and the status line is enough to act on.
    """
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        message = payload.get("errorMessage")
        if isinstance(message, str) and message:
            return message
    return response.reason_phrase or "request failed"


class ApiClient:
    """Thin async wrapper over ``httpx.AsyncClient`` for the v20 API."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cached_account_id: str | None = settings.account_id
        self._http = httpx.AsyncClient(
            base_url=settings.base_url,
            headers={
                "Authorization": f"Bearer {settings.api_token}",
                "Accept-Datetime-Format": "RFC3339",
            },
            timeout=_TIMEOUT_SECONDS,
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """Send one API request and return the decoded JSON body.

        ``None``-valued entries in ``params`` are dropped, so callers can pass
        optional tool arguments straight through. Returns ``None`` for empty
        bodies (e.g. 204). Raises ``ToolError`` with ``"HTTP <status>:
        <message>"`` on any error status, where the message is the API's
        ``errorMessage`` when present.
        """
        if params is not None:
            params = {key: value for key, value in params.items() if value is not None}

        response = await self._http.request(method, path, params=params, json=json_body)
        if response.status_code == 429:
            await asyncio.sleep(_retry_after_seconds(response))
            response = await self._http.request(method, path, params=params, json=json_body)

        if response.is_success:
            if not response.content:
                return None
            return response.json()
        raise ToolError(f"HTTP {response.status_code}: {_error_message(response)}")

    async def account_id(self) -> str:
        """Account ID to operate on.

        Returns the configured ``OANDA_ACCOUNT_ID`` when set; otherwise fetches
        ``GET /v3/accounts`` once, caches the first account's ID, and returns it
        on every subsequent call without further requests.
        """
        if self._cached_account_id:
            return self._cached_account_id
        payload = await self.request("GET", "/v3/accounts")
        accounts = (payload or {}).get("accounts") or []
        if not accounts:
            raise ToolError("no accounts are authorized for this token")
        self._cached_account_id = str(accounts[0]["id"])
        return self._cached_account_id

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._http.aclose()
