from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from . import telegram_bot as base

logger = logging.getLogger("abred.parser.telegram_bot")

OBS_MAIN_MENU = {
    "keyboard": [
        [{"text": "📊 Статус"}, {"text": "🧩 Парсер"}],
        [{"text": "📦 Фиды"}, {"text": "🖥 Бэкенд"}],
        [{"text": "📜 Логи"}, {"text": "⚙️ Управление"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}


def logs_menu() -> dict[str, Any]:
    return base._inline([
        [("🧩 Парсер", "logs:parser"), ("🖥 Бэкенд", "logs:backend")],
    ])


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_text(value: Any, *, now: datetime | None = None) -> str:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return "нет данных"
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    seconds = max(0, int((current - parsed).total_seconds()))
    if seconds < 60:
        return "только что"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} мин назад"
    hours, rem_minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} ч {rem_minutes} мин назад" if rem_minutes else f"{hours} ч назад"
    days, rem_hours = divmod(hours, 24)
    if days < 14:
        return f"{days} д {rem_hours} ч назад" if rem_hours else f"{days} д назад"
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


def _last_run_age(row: dict[str, Any] | None) -> str:
    last_run = row.get("last_run") if isinstance(row, dict) and isinstance(row.get("last_run"), dict) else {}
    if not last_run:
        return "нет данных"
    timestamp = last_run.get("finished_at") or last_run.get("started_at")
    age = age_text(timestamp)
    if str(last_run.get("status") or "").casefold() == "running":
        return f"идёт, старт {age}"
    return age


def _format_parser_source(source: str, row: dict[str, Any] | None) -> str:
    if not row:
        return f"🔴 {base.SOURCE_LABELS[source]}: нет статистики"
    last_run = row.get("last_run") if isinstance(row.get("last_run"), dict) else {}
    run_stats = last_run.get("stats") if isinstance(last_run.get("stats"), dict) else {}
    status = str(last_run.get("status") or "unknown")
    lines = [
        f"{base._icon(status)} {base.SOURCE_LABELS[source]}: {status}",
        f"последний запуск: {_last_run_age(row)}",
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


def _format_logs(title: str, payload: dict[str, Any]) -> str:
    rows = payload.get("logs")
    if not isinstance(rows, list) or not rows:
        return f"📜 {title}\nЛог-буфер пока пуст (он хранится только с последнего рестарта процесса)."
    lines = [f"📜 {title}", "последние строки из RAM с последнего рестарта:", ""]
    for row in rows[-40:]:
        if not isinstance(row, dict):
            continue
        timestamp = _parse_timestamp(row.get("time"))
        stamp = timestamp.strftime("%H:%M:%S") if timestamp else "--:--:--"
        level = str(row.get("level") or "INFO")[:8]
        logger_name = str(row.get("logger") or "-").rsplit(".", 1)[-1][:24]
        message = str(row.get("message") or "").replace("\n", " ")[:700]
        lines.append(f"{stamp} {level} {logger_name}: {message}")
    return "\n".join(lines)


class ParserAPI(base.ParserAPI):
    async def logs(self, *, limit: int = 40) -> dict[str, Any]:
        return await self._get("/v1/logs", params={"limit": max(1, min(limit, 100))})


class BackendOpsAPI(base.BackendOpsAPI):
    async def logs(self) -> dict[str, Any]:
        return await self.request("GET", "/v1/telegram-ops/logs")


class OpsService(base.OpsService):
    def __init__(self, settings: base.BotSettings):
        self.parser = ParserAPI(settings)
        self.backend = BackendOpsAPI(settings)

    async def parser_text(self, source: str | None = None) -> str:
        stats = await self.parser.stats()
        rows = base._source_rows(stats)
        if source:
            return "Парсер\n" + _format_parser_source(source, rows.get(source))
        lines = ["Парсер"]
        for item in base.SOURCES:
            lines.append(_format_parser_source(item, rows.get(item)))
        scheduler = stats.get("scheduler") if isinstance(stats.get("scheduler"), dict) else {}
        retention = stats.get("retention") if isinstance(stats.get("retention"), dict) else {}
        lines.append(f"scheduler={scheduler.get('enabled', '—')} retention={retention.get('hours', '—')}h")
        return "\n\n".join(lines)

    async def status_text(self) -> str:
        parser_stats, backend = await asyncio.gather(self.parser.stats(), self.backend.status())
        parser_rows = base._source_rows(parser_stats)
        states = base._backend_states(backend)
        lines = [
            "Abred Ops",
            f"{base._icon('OK')} Parser: OK",
            f"{base._icon(str(backend.get('status') or ''))} Backend: {backend.get('status', '—')} {backend.get('version', '')}".rstrip(),
            "",
        ]
        for source in base.SOURCES:
            state = states.get(source, {})
            cursor = int(state.get("last_success_cursor") or 0)
            pending = await self.parser.feeds(source, after=cursor)
            backend_status = "BLOCKED" if state.get("blocked") else ("READY" if state.get("enabled") else "DISABLED")
            row = parser_rows.get(source, {})
            last_run = row.get("last_run") if isinstance(row.get("last_run"), dict) else {}
            run_status = last_run.get("status", "—") if isinstance(last_run, dict) else "—"
            lines.append(
                f"{base.SOURCE_LABELS[source]}: backend={backend_status} cursor={cursor or '—'} "
                f"pending={len(pending)} parser={run_status} · {_last_run_age(row)}"
            )
        return "\n".join(lines)

    async def parser_logs_text(self) -> str:
        return _format_logs("Логи парсера", await self.parser.logs(limit=40))

    async def backend_logs_text(self) -> str:
        return _format_logs("Логи бэкенда", await self.backend.logs())


class TelegramBot(base.TelegramBot):
    def __init__(
        self,
        settings: base.BotSettings,
        *,
        api: base.TelegramAPI | None = None,
        service: OpsService | None = None,
    ):
        super().__init__(settings, api=api, service=service or OpsService(settings))

    async def _handle_authorized_text(self, chat_id: int, text: str) -> None:
        parts = text.split()
        command = parts[0].split("@", 1)[0].casefold() if parts and parts[0].startswith("/") else ""
        if command in {"/start", "/menu", "/help"} or text == "🏠 Меню":
            await self.api.send_message(
                chat_id,
                "Abred Ops Bot\nВыбирай раздел кнопками ниже.",
                reply_markup=OBS_MAIN_MENU,
            )
            return
        if command == "/logs" or text == "📜 Логи":
            await self.api.send_message(chat_id, "Какие логи показать?", reply_markup=logs_menu())
            return
        await super()._handle_authorized_text(chat_id, text)

    async def _send_service(self, chat_id: int, awaitable) -> None:
        try:
            text = await awaitable
        except Exception as exc:
            logger.warning("Telegram operation failed: %s", type(exc).__name__)
            text = f"🔴 Ошибка: {type(exc).__name__}. Подробности в логах telegram-bot."
        await self.api.send_message(chat_id, text, reply_markup=OBS_MAIN_MENU)

    async def _handle_callback(self, callback: dict[str, Any]) -> None:
        data = callback.get("data")
        if data not in {"logs:parser", "logs:backend"}:
            await super()._handle_callback(callback)
            return
        callback_id = callback.get("id")
        sender = callback.get("from") if isinstance(callback.get("from"), dict) else {}
        message = callback.get("message") if isinstance(callback.get("message"), dict) else {}
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        user_id = sender.get("id")
        chat_id = chat.get("id")
        if not isinstance(callback_id, str) or not isinstance(user_id, int) or not isinstance(chat_id, int):
            return
        if not self.settings.authorized(user_id=user_id, chat_id=chat_id):
            await self.api.answer_callback(callback_id, "Доступ запрещён")
            return
        await self.api.answer_callback(callback_id)
        if data == "logs:parser":
            await self._send_service(chat_id, self.service.parser_logs_text())
        else:
            await self._send_service(chat_id, self.service.backend_logs_text())


async def _amain() -> None:
    logging.basicConfig(
        level=os.environ.get("TELEGRAM_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = base.BotSettings.from_env()
    bot = TelegramBot(settings)
    try:
        await bot.run_forever()
    finally:
        await bot.aclose()


def main() -> None:
    asyncio.run(_amain())
