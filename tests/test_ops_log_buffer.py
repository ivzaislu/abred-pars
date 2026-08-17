from __future__ import annotations

import logging

from abred_catalog_pipeline.server.ops_log_buffer import SafeLogBuffer, SafeLogBufferHandler, redact_log_message


def test_redaction_covers_operational_secrets():
    raw = (
        "GET https://api.telegram.org/bot8447518548:ABCDEFGHIJKLMNOPQRSTUVWXYZ123456/getUpdates "
        "Authorization: Bearer abc.DEF_123 TELEGRAM_BACKEND_TOKEN=supersupersecret "
        "postgresql://abred:dbpassword@postgres:5432/abred"
    )
    value = redact_log_message(raw)
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in value
    assert "abc.DEF_123" not in value
    assert "supersupersecret" not in value
    assert "dbpassword" not in value
    assert "<redacted>" in value


def test_buffer_and_snapshot_are_bounded():
    buf = SafeLogBuffer(max_entries=10)
    for index in range(25):
        buf.append(created=1776466800 + index, level="INFO", logger_name="test", message=f"line {index}")
    rows = buf.snapshot(limit=100)
    assert len(rows) == 10
    assert rows[0]["message"] == "line 15"
    assert rows[-1]["message"] == "line 24"


def test_handler_redacts_before_storage():
    buf = SafeLogBuffer(max_entries=10)
    handler = SafeLogBufferHandler(buf)
    record = logging.LogRecord(
        "test",
        logging.INFO,
        __file__,
        1,
        "PARSER_API_TOKEN=%s",
        ("secret-value",),
        None,
    )
    handler.emit(record)
    assert "secret-value" not in buf.snapshot(limit=1)[0]["message"]
