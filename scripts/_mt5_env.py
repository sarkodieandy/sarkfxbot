"""Shared, secret-safe MT5 script configuration."""

from __future__ import annotations

from app.brokers.mt5 import MT5BrokerAdapter
from app.config.settings import Settings


def broker_from_environment() -> MT5BrokerAdapter:
    # Settings loads both the process environment and the documented local .env file.
    # SecretStr keeps credentials out of repr/logging while the adapter receives the
    # raw value only at this construction boundary.
    settings = Settings()
    login_text = (settings.mt5_login or "").strip()
    server = (settings.mt5_server or "").strip()
    password = settings.mt5_password.get_secret_value() if settings.mt5_password is not None else ""
    terminal_path = (settings.mt5_terminal_path or "").strip() or None
    if not login_text or not server or not password:
        raise ValueError("MT5_LOGIN, MT5_SERVER, and MT5_PASSWORD must be set")
    try:
        login = int(login_text)
    except ValueError as exc:
        raise ValueError("MT5_LOGIN must be an integer") from exc
    return MT5BrokerAdapter(
        login=login,
        server=server,
        password=password,
        terminal_path=terminal_path,
    )


def masked_account(account_id: str) -> str:
    if len(account_id) <= 4:
        return "****"
    return f"{'*' * (len(account_id) - 4)}{account_id[-4:]}"
