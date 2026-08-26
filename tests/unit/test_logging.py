from __future__ import annotations

import io
import json
import logging

import pytest

from app.config.logging import (
    REDACTED,
    bind_correlation_id,
    clear_correlation_id,
    configure_logging,
    reset_correlation_id,
)


@pytest.fixture(autouse=True)
def restore_root_logger() -> object:
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    clear_correlation_id()
    try:
        yield
    finally:
        root.handlers[:] = original_handlers
        root.setLevel(original_level)
        clear_correlation_id()


def test_json_logging_is_structured_correlated_and_redacted() -> None:
    output = io.StringIO()
    configure_logging(
        level="INFO",
        json_logs=True,
        secret_values=("literal-secret",),
        stream=output,
    )
    token = bind_correlation_id("request-123")
    try:
        logging.getLogger("goldflow.test").info(
            "connected with literal-secret\nsecond line",
            extra={
                "password": "broker-password",
                "nested": {"api_token": "telegram-token", "account": "12345"},
            },
        )
    finally:
        reset_correlation_id(token)

    event = json.loads(output.getvalue())
    assert event["level"] == "INFO"
    assert event["logger"] == "goldflow.test"
    assert event["service"] == "goldflow"
    assert event["event"] == "LOG_EVENT"
    assert event["metadata"] == {}
    assert event["signal_id"] is None
    assert event["correlation_id"] == "request-123"
    assert event["password"] == REDACTED
    assert event["nested"] == {"api_token": REDACTED, "account": "12345"}
    assert "literal-secret" not in output.getvalue()
    assert "broker-password" not in output.getvalue()
    assert "telegram-token" not in output.getvalue()
    assert "\n" not in event["message"]


def test_text_logging_redacts_registered_literal() -> None:
    output = io.StringIO()
    configure_logging(
        level="WARNING",
        json_logs=False,
        secret_values=("broker-secret",),
        stream=output,
    )

    logging.getLogger("goldflow.test").warning("failure: broker-secret")

    assert "broker-secret" not in output.getvalue()
    assert REDACTED in output.getvalue()


def test_empty_correlation_id_and_invalid_level_are_rejected() -> None:
    with pytest.raises(ValueError, match="correlation_id"):
        bind_correlation_id("  ")
    with pytest.raises(ValueError, match="log level"):
        configure_logging(level="TRACE")
