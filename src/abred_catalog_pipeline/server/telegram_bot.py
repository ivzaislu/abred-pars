from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("abred.parser.telegram_bot")

SOURCES = ("audiopolka", "rutracker", "uknig")
SOURCE_LABELS = {
    "audiopolka": "Audiopolka",
    "rutracker": "RuTracker",
    "uknig": "Uknig",
}
MESSAGE_LIMIT = 3900

MAIN_MENU = {
    "keyboard": [
        [{"text": "📊 Статус"}, {"text": "🧩 Парсер"}],
        [{"text": "📦 Фиды"}, {"text": "🖥 Бэкенд"}],
        [{"text": "⚙️ Управление"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}


def _inline(rows: list[list[tuple[str, str]]]) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": text, "callback_data": data} for text, data in row]
            for row in rows
        ]
    }


def parser_menu() -> dict[str, Any]:
    return _inline([
        [(SOURCE_LABELS[source], f"parser:{source}") for source in SOURCES],
        [("Все источники", "parser:all")],
    ])


def control_sources_menu() -> dict[str, Any]:
    return _inline([
        [(SOURCE_LABELS[source], f"control:{source}") for source in SOURCES],
    ])


def control_actions_menu(source: str) -> dict[str, Any]:
    return _inline([
        [("🔎 Dry-run", f"dryrun:{source}"), ("▶️ Импорт 1 feed", f"ask:run:{source}")],
        [("✅ Enable", f"ask:enable:{source}"), ("⏸ Disable", f"ask:disable:{source}")],
        [("🔓 Unblock", f"ask:unblock:{source}")],
        [("⬅️ Источники", "control:menu")],
    ])


def confirm_menu(action: str, source: str) -> dict[str, Any]:
    return _inline([
        [("✅ Подтвердить", f"do:{action}:{source}"), ("❌ Отмена", f"control:{source}")],
    ])


def _parse_int_set(raw: str) -> frozenset[int]:
    values: set[int] = set()
    for part in (raw or "").replace(";", ",").split(","):
        value = part.strip()
        if not value:
            continue
        values.add(int(value))
    return frozenset(values)


def _env_float(name: str, default: float, *, minimum: float) -> float:
    raw = os.environ.get(name, "").strip()
    value = default if not raw else float(raw)
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _safe_base_url(name: str, default: str) -> str:
    value = os.environ.get(name, default).strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise RuntimeError(f"{name} must be a plain http(s) URL without embedded credentials")
    if parsed.query or parsed.fragment:
        raise RuntimeError(f"{name} must not contain query or fragment")
    return value


def _fmt_bytes(value: Any) -> str:
    try:
        size = float(value or 0)
    except (TypeError, ValueError):
        return "—"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(size) < 1024 or unit == "TiB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return "—"


def _icon(status: str) -> str:
    value = (status or "").upper()
    if value in {"OK", "READY", "COMPLETED", "IDLE"}:
        return "🟢"
    if value in {"RUNNING", "RETRY_WAIT", "DISABLED", "LOCKED"}:
        return "🟡"
    return "🔴"


def _chunks(text: str, limit: int = MESSAGE_LIMIT) -> Iterable[str]:
    remaining = text or ""
    if not remaining:
        yield "—"
        return
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = limit
        yield remaining[:split_at]
        remaining = remaining[split_at:].lstrip("\n")
    if remaining:
        yield remaining


def _json_bytes(payload: dict[str, Any] | None) -> bytes:
    if payload is None:
        return b""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _backend_signature(
    secret: str,
    *,
    method: str,
    path: str,
    timestamp: int,
    nonce: str,
    body: bytes,
) -> str:
    canonical = "\n".join(
        (
            method.upper(),
            path,
            str(int(timestamp)),
            nonce,
            hashlib.sha256(body).hexdigest(),
        )
    ).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class BotSettings:
    telegram_token: str
    allowed_user_ids: frozenset[int]
    allowed_chat_ids: frozenset[int]
    parser_url: str
    parser_token: str
    backend_url: str
    backend_token: str
    poll_timeout_seconds: float
    request_timeout_seconds: float

    @classmethod
    def from_env(cls) -> "BotSettings":
        telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not telegram_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
        parser_token = os.environ.get("PARSER_API_TOKEN", "").strip()
        if len(parser_token) < 32:
            raise RuntimeError("PARSER_API_TOKEN must be at least 32 characters")
        backend_token = os.environ.get("TELEGRAM_BACKEND_TOKEN", "").strip()
        if len(backend_token) < 32:
            raise RuntimeError("TELEGRAM_BACKEND_TOKEN must be at least 32 characters")
        return cls(
            telegram_token=telegram_token,
            allowed_user_ids=_parse_int_set(os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "")),
            allowed_chat_ids=_parse_int_set(os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "")),
            parser_url=_safe_base_url("TELEGRAM_PARSER_URL", "http://parser-server:8081"),
            parser_token=parser_token,
            backend_url=_safe_base_url("TELEGRAM_BACKEND_URL", "http://127.0.0.1:8000"),
            backend_token=backend_token,
            poll_timeout_seconds=_env_float("TELEGRAM_POLL_TIMEOUT_SECONDS", 30.0, minimum=1.0),
            request_timeout_seconds=_env_float("TELEGRAM_REQUEST_TIMEOUT_SECONDS", 30.0, minimum=1.0),
        )

    def authorized(self, *, user_id: int, chat_id: int) -> bool:
        if user_id not in self.allowed_user_ids:
            return False
        if self.allowed_chat_ids and chat_id not in self.allowed_chat_ids:
            return False
        return True


class TelegramAPI:
    def __init__(self, token: str, *, timeout_seconds: float):
        self.client = httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{token}/",
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def get_updates(self, *, offset: int | None, poll_timeout: float) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "timeout": max(1, int(poll_timeout)),
            "allowed_updates": json.dumps(["message", "callback_query"]),
        }
        if offset is not None:
            params["offset"] = offset
        response = await self.client.get("getUpdates", params=params)
        response.raise_for_status()
        payload = response.json()
        if payload.get("ok") is not True or not isinstance(payload.get("result"), list):
            raise RuntimeError("invalid Telegram getUpdates response")
        return payload["result"]

    async def send_message(self, chat_id: int, text: str, *, reply_markup: dict[str, Any] | None = None) -> None:
        chunks = list(_chunks(text))
        for index, chunk in enumerate(chunks):
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            }
            if index == len(chunks) - 1 and reply_markup is not None:
                payload["reply_markup"] = reply_markup
            response = await self.client.post("sendMessage", json=payload)
            response.raise_for_status()
            if response.json().get("ok") is not True:
                raise RuntimeError("invalid Telegram sendMessage response")

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        response = await self.client.post(
            "answerCallbackQuery",
            json={"callback_query_id": callback_id, "text": text[:180]},
        )
        response.raise_for_status()


class ParserAPI:
    def __init__(self, settings: BotSettings):
        self.base_url = settings.parser_url
        self.token = settings.parser_token
        self.timeout = settings.request_timeout_seconds

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    async def _get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
            response = await client.get(f"{self.base_url}{path}", headers=self._headers(), params=params)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("invalid parser response")
            return payload

    async def stats(self) -> dict[str, Any]:
        return await self._get("/v1/stats")

    async def feeds(self, source: str, *, after: int) -> list[dict[str, Any]]:
        payload = await self._get("/v1/feeds", params={"source": source, "after": after, "limit": 100})
        rows = payload.get("feeds")
        if not isinstance(rows, list):
            raise RuntimeError("invalid parser feeds response")
        return [row for row in rows if isinstance(row, dict)]


class BackendOpsAPI:
    def __init__(self, settings: BotSettings):
        self.base_url = settings.backend_url
        self.secret = settings.backend_token
        self.timeout = settings.request_timeout_seconds

    async def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = _json_bytes(payload)
        timestamp = int(time.time())
        nonce = secrets.token_urlsafe(18)
        headers = {
            "X-Abred-Timestamp": str(timestamp),
            "X-Abred-Nonce": nonce,
            "X-Abred-Signature": _backend_signature(
                self.secret,
                method=method,
                path=path,
                timestamp=timestamp,
                nonce=nonce,
                body=body,
            ),
        }
        if body:
            headers["Content-Type"] = "application/json"
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
            response = await client.request(method, f"{self.base_url}{path}", headers=headers, content=body)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise RuntimeError("invalid backend ops response")
            return data

    async def status(self) -> dict[str, Any]:
        return await self.request("GET", "/v1/telegram-ops/status")

    async def dry_run(self, source: str) -> dict[str, Any]:
        return await self.request(
            "POST",
            f"/v1/telegram-ops/sources/{source}/run",
            {"apply": False, "confirm": False},
        )

    async def apply_one(self, source: str) -> dict[str, Any]:
        return await self.request(
            "POST",
            f"/v1/telegram-ops/sources/{source}/run",
            {"apply": True, "confirm": True},
        )

    async def action(self, source: str, action: str) -> dict[str, Any]:
        if action not in {"enable", "disable", "unblock"}:
            raise ValueError("unsupported backend action")
        return await self.request(
            "POST",
            f"/v1/telegram-ops/sources/{source}/{action}",
            {"confirm": True},
        )


def _source_rows(stats: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = stats.get("sources")
    if not isinstance(rows, list):
        return {}
    return {
        str(row.get("source")): row
        for row in rows
        if isinstance(row, dict) and row.get("source") in SOURCES
    }


def _backend_states(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = payload.get("sources")
    if isinstance(rows, list):
        return {
            str(row.get("source")): row
            for row in rows
            if isinstance(row, dict) and row.get("source") in SOURCES
        }
    if isinstance(rows, dict):
        return {key: value for key, value in rows.items() if key in SOURCES and isinstance(value, dict)}
    return {}


def _format_parser_source(source: str, row: dict[str, Any] | None) -> str:
    if not row:
        return f"🔴 {SOURCE_LABELS[source]}: нет статистики"
    last_run = row.get("last_run") if isinstance(row.get("last_run"), dict) else {}
    run_stats = last_run.get("stats") if isinstance(last_run.get("stats"), dict) else {}
    status = str(last_run.get("status") or "unknown")
    lines = [
        f"{_icon(status)} {SOURCE_LABELS[source]}: {status}",
        f"records={run_stats.get('records', '—')} rejected={run_stats.get('rejected', '—')}",
    ]
    if source == "rutracker":
        torrent = run_stats.get("torrent_metadata") if isinstance(run_stats.get("torrent_metadata"), dict) else {}
        retry = torrent.get("retry_queue") if isinstance(torrent.get("retry_queue"), dict) else {}
        lines.append(
            "metadata enriched={} failed={} failovers={}".format(
                torrent.get("enriched", "—"), torrent.get("failed", "—"), torrent.get("failovers", "—")
            )
        )
        lines.append(
            "retry pending={} resolved={} queued={}".format(
                retry.get("pending", "—"), retry.get("resolved", "—"), retry.get("newly_queued", "—")
            )
        )
    return "\n".join(lines)


class OpsService:
    def __init__(self, settings: BotSettings):
        self.parser = ParserAPI(settings)
        self.backend = BackendOpsAPI(settings)

    async def parser_text(self, source: str | None = None) -> str:
        stats = await self.parser.stats()
        rows = _source_rows(stats)
        if source:
            return "Парсер\n" + _format_parser_source(source, rows.get(source))
        lines = ["Парсер"]
        for item in SOURCES:
            lines.append(_format_parser_source(item, rows.get(item)))
        scheduler = stats.get("scheduler") if isinstance(stats.get("scheduler"), dict) else {}
        retention = stats.get("retention") if isinstance(stats.get("retention"), dict) else {}
        lines.append(f"scheduler={scheduler.get('enabled', '—')} retention={retention.get('hours', '—')}h")
        return "\n\n".join(lines)

    async def backend_text(self) -> str:
        payload = await self.backend.status()
        states = _backend_states(payload)
        lines = [
            "Бэкенд",
            f"{_icon(str(payload.get('status') or ''))} API={payload.get('status', '—')} version={payload.get('version', '—')}",
            f"feed transport={payload.get('feed_transport', '—')}",
        ]
        for source in SOURCES:
            state = states.get(source, {})
            status = "BLOCKED" if state.get("blocked") else ("READY" if state.get("enabled") else "DISABLED")
            lines.append(
                f"{_icon(status)} {SOURCE_LABELS[source]}: {status} cursor={state.get('last_success_cursor') or '—'} failures={state.get('failure_count', 0)}"
            )
        return "\n".join(lines)

    async def feeds_text(self) -> str:
        parser_stats, backend = await asyncio.gather(self.parser.stats(), self.backend.status())
        states = _backend_states(backend)
        feeds_summary = parser_stats.get("feeds") if isinstance(parser_stats.get("feeds"), dict) else {}
        lines = [
            "Фиды",
            f"parser total={feeds_summary.get('count', '—')} size={_fmt_bytes(feeds_summary.get('bundle_bytes'))} missing={feeds_summary.get('missing_bundles', '—')}",
        ]
        for source in SOURCES:
            cursor = int(states.get(source, {}).get("last_success_cursor") or 0)
            pending = await self.parser.feeds(source, after=cursor)
            lines.append(f"{SOURCE_LABELS[source]}: backend cursor={cursor or '—'} pending={len(pending)}")
        return "\n".join(lines)

    async def status_text(self) -> str:
        parser_stats, backend = await asyncio.gather(self.parser.stats(), self.backend.status())
        parser_rows = _source_rows(parser_stats)
        states = _backend_states(backend)
        lines = [
            "Abred Ops",
            f"{_icon('OK')} Parser: OK",
            f"{_icon(str(backend.get('status') or ''))} Backend: {backend.get('status', '—')} {backend.get('version', '')}".rstrip(),
            "",
        ]
        for source in SOURCES:
            state = states.get(source, {})
            cursor = int(state.get("last_success_cursor") or 0)
            pending = await self.parser.feeds(source, after=cursor)
            backend_status = "BLOCKED" if state.get("blocked") else ("READY" if state.get("enabled") else "DISABLED")
            last_run = parser_rows.get(source, {}).get("last_run")
            run_status = last_run.get("status", "—") if isinstance(last_run, dict) else "—"
            lines.append(
                f"{SOURCE_LABELS[source]}: backend={backend_status} cursor={cursor or '—'} pending={len(pending)} parser={run_status}"
            )
        return "\n".join(lines)

    async def dry_run(self, source: str) -> str:
        result = await self.backend.dry_run(source)
        return _format_action_result("Dry-run", source, result)

    async def apply_one(self, source: str) -> str:
        result = await self.backend.apply_one(source)
        return _format_action_result("Импорт", source, result)

    async def action(self, source: str, action: str) -> str:
        result = await self.backend.action(source, action)
        return _format_action_result(action, source, result)


def _format_action_result(action: str, source: str, result: dict[str, Any]) -> str:
    lines = [f"{action}: {SOURCE_LABELS[source]}", f"status={result.get('status', '—')}"]
    if result.get("cursor") is not None:
        lines.append(f"cursor={result.get('cursor')}")
    if result.get("feed_id"):
        lines.append(f"feed={result.get('feed_id')}")
    if result.get("error"):
        lines.append(f"error={result.get('error')}")
    return "\n".join(lines)


class TelegramBot:
    def __init__(
        self,
        settings: BotSettings,
        *,
        api: TelegramAPI | None = None,
        service: OpsService | None = None,
    ):
        self.settings = settings
        self.api = api or TelegramAPI(settings.telegram_token, timeout_seconds=settings.request_timeout_seconds)
        self.service = service or OpsService(settings)
        self.offset: int | None = None

    async def aclose(self) -> None:
        await self.api.aclose()

    async def run_forever(self) -> None:
        logger.info("Abred parser Telegram bot started; allowlisted_users=%d", len(self.settings.allowed_user_ids))
        backoff = 1.0
        while True:
            try:
                updates = await self.api.get_updates(offset=self.offset, poll_timeout=self.settings.poll_timeout_seconds)
                backoff = 1.0
                for update in updates:
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        self.offset = update_id + 1
                    await self.handle_update(update)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Telegram polling error: %s", type(exc).__name__)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def handle_update(self, update: dict[str, Any]) -> None:
        if isinstance(update.get("callback_query"), dict):
            await self._handle_callback(update["callback_query"])
            return
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        sender = message.get("from") if isinstance(message.get("from"), dict) else {}
        chat_id = chat.get("id")
        user_id = sender.get("id")
        text = message.get("text")
        if not isinstance(chat_id, int) or not isinstance(user_id, int) or not isinstance(text, str):
            return
        text = text.strip()
        if text.split("@", 1)[0] == "/whoami":
            await self.api.send_message(chat_id, f"user_id={user_id}\nchat_id={chat_id}")
            return
        if not self.settings.authorized(user_id=user_id, chat_id=chat_id):
            await self.api.send_message(chat_id, "Доступ запрещён. /whoami")
            return
        await self._handle_authorized_text(chat_id, text)

    async def _handle_authorized_text(self, chat_id: int, text: str) -> None:
        parts = text.split()
        command = parts[0].split("@", 1)[0].casefold() if parts and parts[0].startswith("/") else ""
        args = [part.strip().casefold() for part in parts[1:]]
        if command in {"/start", "/menu", "/help"} or text == "🏠 Меню":
            await self.api.send_message(
                chat_id,
                "Abred Ops Bot\nВыбирай раздел кнопками ниже.",
                reply_markup=MAIN_MENU,
            )
            return
        if command == "/status" or text == "📊 Статус":
            await self._send_service(chat_id, self.service.status_text())
        elif command == "/parser" and args and args[0] in SOURCES:
            await self._send_service(chat_id, self.service.parser_text(args[0]))
        elif command == "/parser" or text == "🧩 Парсер":
            await self.api.send_message(chat_id, "Парсер — выбери источник:", reply_markup=parser_menu())
        elif command == "/feeds" or text == "📦 Фиды":
            await self._send_service(chat_id, self.service.feeds_text())
        elif command == "/backend" or text == "🖥 Бэкенд":
            await self._send_service(chat_id, self.service.backend_text())
        elif text == "⚙️ Управление":
            await self.api.send_message(chat_id, "Управление backend intake — выбери источник:", reply_markup=control_sources_menu())
        else:
            await self.api.send_message(chat_id, "Используй меню 👇", reply_markup=MAIN_MENU)

    async def _send_service(self, chat_id: int, awaitable) -> None:
        try:
            text = await awaitable
        except Exception as exc:
            logger.warning("Telegram operation failed: %s", type(exc).__name__)
            text = f"🔴 Ошибка: {type(exc).__name__}. Подробности в логах telegram-bot."
        await self.api.send_message(chat_id, text, reply_markup=MAIN_MENU)

    async def _handle_callback(self, callback: dict[str, Any]) -> None:
        callback_id = callback.get("id")
        sender = callback.get("from") if isinstance(callback.get("from"), dict) else {}
        message = callback.get("message") if isinstance(callback.get("message"), dict) else {}
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        data = callback.get("data")
        user_id = sender.get("id")
        chat_id = chat.get("id")
        if not isinstance(callback_id, str) or not isinstance(data, str) or not isinstance(user_id, int) or not isinstance(chat_id, int):
            return
        if not self.settings.authorized(user_id=user_id, chat_id=chat_id):
            await self.api.answer_callback(callback_id, "Доступ запрещён")
            return
        await self.api.answer_callback(callback_id)
        parts = data.split(":")
        try:
            if data == "parser:all":
                await self._send_service(chat_id, self.service.parser_text())
            elif parts[0] == "parser" and len(parts) == 2 and parts[1] in SOURCES:
                await self._send_service(chat_id, self.service.parser_text(parts[1]))
            elif data == "control:menu":
                await self.api.send_message(chat_id, "Управление — выбери источник:", reply_markup=control_sources_menu())
            elif parts[0] == "control" and len(parts) == 2 and parts[1] in SOURCES:
                await self.api.send_message(
                    chat_id,
                    f"{SOURCE_LABELS[parts[1]]}: выбери действие",
                    reply_markup=control_actions_menu(parts[1]),
                )
            elif parts[0] == "dryrun" and len(parts) == 2 and parts[1] in SOURCES:
                await self._send_service(chat_id, self.service.dry_run(parts[1]))
            elif parts[0] == "ask" and len(parts) == 3 and parts[1] in {"run", "enable", "disable", "unblock"} and parts[2] in SOURCES:
                await self.api.send_message(
                    chat_id,
                    f"Подтвердить {parts[1]} для {SOURCE_LABELS[parts[2]]}?",
                    reply_markup=confirm_menu(parts[1], parts[2]),
                )
            elif parts[0] == "do" and len(parts) == 3 and parts[1] in {"run", "enable", "disable", "unblock"} and parts[2] in SOURCES:
                source = parts[2]
                if parts[1] == "run":
                    await self._send_service(chat_id, self.service.apply_one(source))
                else:
                    await self._send_service(chat_id, self.service.action(source, parts[1]))
            else:
                await self.api.send_message(chat_id, "Кнопка устарела. Открой меню заново.", reply_markup=MAIN_MENU)
        except Exception as exc:
            logger.warning("Telegram callback failed: %s", type(exc).__name__)
            await self.api.send_message(chat_id, f"🔴 Ошибка: {type(exc).__name__}", reply_markup=MAIN_MENU)


async def _amain() -> None:
    logging.basicConfig(
        level=os.environ.get("TELEGRAM_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = BotSettings.from_env()
    bot = TelegramBot(settings)
    try:
        await bot.run_forever()
    finally:
        await bot.aclose()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
