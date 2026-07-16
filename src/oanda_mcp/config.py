"""Environment-driven configuration for the Oanda MCP server.

Everything is read from environment variables so that credentials live only in
the process environment (typically an MCP client's ``env`` block), never in
files or command-line arguments.
"""

import os
from dataclasses import dataclass, field

_HOSTS: dict[str, str] = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}

_TRUTHY = frozenset({"true", "1", "yes"})


@dataclass(frozen=True)
class Settings:
    """Immutable server configuration.

    Attributes:
        api_token: Oanda personal access token (``Authorization: Bearer ...``).
            Excluded from ``repr`` so tracebacks, debuggers, and logs that
            render a ``Settings`` instance never print the credential.
        account_id: Account to operate on; ``None`` means auto-discover the
            first account authorized for the token.
        env: ``"practice"`` (default) or ``"live"``; selects the API host.
        enable_trading: When ``True``, write tools (order placement, trade and
            position closes, account configuration) are registered. When
            ``False`` they do not exist on the server at all.
    """

    api_token: str = field(repr=False)
    account_id: str | None = None
    env: str = "practice"
    enable_trading: bool = False

    @property
    def base_url(self) -> str:
        """API host for the configured environment."""
        return _HOSTS[self.env]

    @classmethod
    def from_env(cls) -> "Settings":
        """Build ``Settings`` from ``OANDA_*`` environment variables.

        Raises ``ValueError`` naming any missing or invalid variable. Error
        messages name the variable only — they never echo its value.
        """
        api_token = os.environ.get("OANDA_API_TOKEN", "").strip()
        if not api_token:
            raise ValueError("OANDA_API_TOKEN is required but not set")

        account_id = os.environ.get("OANDA_ACCOUNT_ID", "").strip() or None

        env = os.environ.get("OANDA_ENV", "").strip().lower() or "practice"
        if env not in _HOSTS:
            raise ValueError("OANDA_ENV must be 'practice' or 'live'")

        raw_trading = os.environ.get("OANDA_MCP_ENABLE_TRADING", "").strip().lower()
        enable_trading = raw_trading in _TRUTHY

        return cls(
            api_token=api_token,
            account_id=account_id,
            env=env,
            enable_trading=enable_trading,
        )
