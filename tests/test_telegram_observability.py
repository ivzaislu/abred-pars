from __future__ import annotations

from datetime import datetime, timezone

import pytest

from abred_catalog_pipeline.server.telegram_bot import BotSettings
from abred_catalog_pipeline.server.telegram_bot_observability import (
    OBS_MAIN_MENU,
    TelegramBot,
    _format_logs,
    age_text,
    logs_menu,
)


class FakeAPI:
    def __init__(self):
        self.messages = []
        self.callbacks = []

    async def send_message(self, chat_id, text, *, reply_markup=None):
        self.messages.append((chat_id, text, reply_markup))

    async def answer_callback(self, callback_id, text=""):
        self.callbacks.append((callback_id, text))

    async def aclose(self):
        pass


class FakeService:
    async def parser_logs_text(self):
        return "PARSER LOGS"

    async def backend_logs_text(self):
        return "BACKEND LOGS"


@pytest.fixture
def settings():
    return BotSettings(
        telegram_token="telegram",
        allowed_user_ids=frozenset({111}),
        allowed_chat_ids=frozenset({222}),
        parser_url="http://parser-server:8081",
        parser_token="p" * 48,
        backend_url="http://backend:8000",
        backend_token="s" * 48,
        poll_timeout_seconds=30,
        request_timeout_seconds=30,
    )


def test_age_text_is_human_readable():
    now = datetime(2026, 8, 17, 22, 0, tzinfo=timezone.utc)
    assert age_text("2026-08-17T21:59:30+00:00", now=now) == "только что"
    assert age_text("2026-08-17T20:30:00+00:00", now=now) == "1 ч 30 мин назад"
    assert age_text("2026-08-15T20:00:00+00:00", now=now) == "2 д 2 ч назад"


def test_logs_button_and_targets_exist():
    labels = [button["text"] for row in OBS_MAIN_MENU["keyboard"] for button in row]
    assert "📜 Логи" in labels
    callbacks = [
        button["callback_data"]
        for row in logs_menu()["inline_keyboard"]
        for button in row
    ]
    assert callbacks == ["logs:parser", "logs:backend"]


def test_log_format_is_compact():
    text = _format_logs(
        "Логи парсера",
        {"logs": [{
            "time": "2026-08-17T21:50:00+00:00",
            "level": "INFO",
            "logger": "abred.parser.runner",
            "message": "parser run completed",
        }]},
    )
    assert "21:50:00 INFO runner" in text
    assert "parser run completed" in text


@pytest.mark.asyncio
async def test_logs_button_opens_two_choice_menu(settings):
    api = FakeAPI()
    bot = TelegramBot(settings, api=api, service=FakeService())
    await bot._handle_authorized_text(222, "📜 Логи")
    markup = api.messages[-1][2]
    callbacks = [button["callback_data"] for row in markup["inline_keyboard"] for button in row]
    assert callbacks == ["logs:parser", "logs:backend"]


@pytest.mark.asyncio
async def test_parser_logs_callback_is_allowlisted(settings):
    api = FakeAPI()
    bot = TelegramBot(settings, api=api, service=FakeService())
    await bot._handle_callback({
        "id": "cb1",
        "from": {"id": 111},
        "message": {"chat": {"id": 222}},
        "data": "logs:parser",
    })
    assert api.callbacks == [("cb1", "")]
    assert api.messages[-1][1] == "PARSER LOGS"
    assert any(
        button["text"] == "📜 Логи"
        for row in api.messages[-1][2]["keyboard"]
        for button in row
    )
