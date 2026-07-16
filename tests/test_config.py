"""Tests for environment-driven configuration."""

import pytest

from oanda_mcp.config import Settings


def test_from_env_reads_all_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OANDA_API_TOKEN", "test-token")
    monkeypatch.setenv("OANDA_ACCOUNT_ID", "001-001-0000001-001")
    monkeypatch.setenv("OANDA_ENV", "live")
    monkeypatch.setenv("OANDA_MCP_ENABLE_TRADING", "true")

    settings = Settings.from_env()

    assert settings.api_token == "test-token"
    assert settings.account_id == "001-001-0000001-001"
    assert settings.env == "live"
    assert settings.enable_trading is True
    assert settings.base_url == "https://api-fxtrade.oanda.com"


def test_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OANDA_API_TOKEN", "test-token")

    settings = Settings.from_env()

    assert settings.account_id is None
    assert settings.env == "practice"
    assert settings.enable_trading is False
    assert settings.base_url == "https://api-fxpractice.oanda.com"


def test_from_env_missing_token_names_the_variable() -> None:
    with pytest.raises(ValueError, match="OANDA_API_TOKEN"):
        Settings.from_env()


def test_from_env_blank_token_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OANDA_API_TOKEN", "   ")
    with pytest.raises(ValueError, match="OANDA_API_TOKEN"):
        Settings.from_env()


def test_from_env_invalid_env_names_variable_without_echoing_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OANDA_API_TOKEN", "test-token")
    monkeypatch.setenv("OANDA_ENV", "mistakenly-pasted-value")

    with pytest.raises(ValueError) as excinfo:
        Settings.from_env()

    message = str(excinfo.value)
    assert "OANDA_ENV" in message
    assert "mistakenly-pasted-value" not in message
    assert "test-token" not in message


def test_from_env_missing_token_error_never_echoes_other_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OANDA_ACCOUNT_ID", "001-001-0000009-001")

    with pytest.raises(ValueError) as excinfo:
        Settings.from_env()

    assert "001-001-0000009-001" not in str(excinfo.value)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("True", True),
        ("1", True),
        ("yes", True),
        ("YES", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("", False),
        ("enabled", False),
    ],
)
def test_enable_trading_parsing(monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool) -> None:
    monkeypatch.setenv("OANDA_API_TOKEN", "test-token")
    monkeypatch.setenv("OANDA_MCP_ENABLE_TRADING", raw)

    assert Settings.from_env().enable_trading is expected


def test_repr_never_contains_the_token() -> None:
    """Debugger/traceback renderings of Settings must not leak the credential."""
    settings = Settings(api_token="super-secret-token-value")

    assert "super-secret-token-value" not in repr(settings)
    assert "super-secret-token-value" not in str(settings)
    assert settings.api_token == "super-secret-token-value"


def test_env_value_is_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OANDA_API_TOKEN", "test-token")
    monkeypatch.setenv("OANDA_ENV", "  LIVE ")

    assert Settings.from_env().env == "live"
