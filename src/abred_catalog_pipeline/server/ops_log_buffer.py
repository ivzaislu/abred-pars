from __future__ import annotations

import logging
import re
import threading
import traceback
from collections import deque
from datetime import datetime, timezone
from typing import Any

_TELEGRAM_URL_RE = re.compile(r"(?i)(https?://api\.telegram\.org/bot)[^/\s?]+")
_TELEGRAM_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:TOKEN|PASSWORD|SECRET|SIGNATURE)[A-Z0-9_]*)\s*[:=]\s*([^\s,;]+)"
)
_URL_CREDENTIAL_RE = re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^:/\s]+:)[^@/\s]+@")


def redact_log_message(value: Any) -> str:
    text = str(value or "")
    text = _TELEGRAM_URL_RE.sub(r"\1<redacted>", text)
    text = _TELEGRAM_TOKEN_RE.sub("<redacted-telegram-token>", text)
    text = _BEARER_RE.sub("Bearer <redacted>", text)
    text = _SECRET_ASSIGNMENT_RE.sub(r"\1=<redacted>", text)
    text = _URL_CREDENTIAL_RE.sub(r"\1<redacted>@", text)
    text = " ⏎ ".join(part.strip() for part in text.replace("\r", "").split("\n") if part.strip())
    return text[:1600]


class SafeLogBuffer:
    def __init__(self, *, max_entries: int = 500):
        self._entries: deque[dict[str, str]] = deque(maxlen=max(10, int(max_entries)))
        self._lock = threading.Lock()

    def append(self, *, created: float, level: str, logger_name: str, message: str) -> None:
        item = {
            "time": datetime.fromtimestamp(created, tz=timezone.utc).isoformat(),
            "level": str(level or "INFO").upper(),
            "logger": str(logger_name or "root")[:160],
            "message": redact_log_message(message),
        }
        with self._lock:
            self._entries.append(item)

    def snapshot(self, *, limit: int = 40) -> list[dict[str, str]]:
        count = max(1, min(int(limit), 100))
        with self._lock:
            return list(self._entries)[-count:]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


class SafeLogBufferHandler(logging.Handler):
    def __init__(self, buffer: SafeLogBuffer):
        super().__init__(level=logging.INFO)
        self.buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info:
                message += "\n" + "".join(traceback.format_exception(*record.exc_info))
            self.buffer.append(
                created=record.created,
                level=record.levelname,
                logger_name=record.name,
                message=message,
            )
        except Exception:
            self.handleError(record)


LOG_BUFFER = SafeLogBuffer(max_entries=500)
_HANDLER = SafeLogBufferHandler(LOG_BUFFER)
_INSTALL_LOCK = threading.Lock()
_INSTALLED = False


def install_log_buffer(*namespaces: str) -> None:
    global _INSTALLED
    with _INSTALL_LOCK:
        if _INSTALLED:
            return
        for namespace in namespaces:
            if namespace:
                logging.getLogger(namespace).setLevel(logging.INFO)
        root = logging.getLogger()
        root.addHandler(_HANDLER)
        uvicorn_error = logging.getLogger("uvicorn.error")
        if not uvicorn_error.propagate:
            uvicorn_error.addHandler(_HANDLER)
        _INSTALLED = True
