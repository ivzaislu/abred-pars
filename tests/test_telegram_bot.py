from __future__ import annotations

import pytest

from abred_catalog_pipeline.server.telegram_bot import (
    BotSettings,
    MAIN_MENU,
    TelegramBot,
    _backend_signature,
    confirm_menu,
    control_actions_menu,
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
    def __init__(self):
        self.calls = []

    async def status_text(self):
        self.calls.append(("status",))
        return "STATUS"

    async def parser_text(self, source=None):
        self.calls.append(("parser", source))
        return f"PARSER {source}"

    async def feeds_text(self):
        self.calls.append(("feeds",))
        return "FEEDS"

    async def backend_text(self):
        self.calls.append(("backend",))
        return "BACKEND"

    async def dry_run(self, source):
        self.calls.append(("dryrun", source))
        return "DRY"

    async def apply_one(self, source):
        self.calls.append(("run", source))
        return "RUN"

    async def action(self, source, action):
        self.calls.append((action, source))
        return action.upper()


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


def test_main_menu_is_small_and_persistent():
    labels = [button["text"] for row in MAIN_MENU["keyboard"] for button in row]
    assert labels == ["📊 Статус", "🧩 Парсер", "📦 Фиды", "🖥 Бэкенд", "⚙️ Управление"]
    assert MAIN_MENU["resize_keyboard"] is True
    assert MAIN_MENU["is_persistent"] is True


def test_state_changing_actions_require_confirmation_button():
    data = [button["callback_data"] for row in control_actions_menu("rutracker")["inline_keyboard"] for button in row]
    assert "ask:run:rutracker" in data
    assert "ask:disable:rutracker" in data
    assert "do:run:rutracker" not in data
    confirms = [button["callback_data"] for row in confirm_menu("run", "rutracker")["inline_keyboard"] for button in row]
    assert "do:run:rutracker" in confirms


def test_backend_hmac_matches_backend_fixed_vector():
    assert _backend_signature(
        "s" * 48,
        method="POST",
        path="/v1/telegram-ops/sources/rutracker/run",
        timestamp=1776466800,
        nonce="nonce-1234567890abcdef",
        body=b'{"apply":true,"confirm":true}',
    ) == "35de673fc7a6cf21f3679965c48434ce429806ea7ab5a8abf1f8209ec454a7ee"


@pytest.mark.asyncio
async def test_menu_button_runs_without_command_typing(settings):
    api = FakeAPI()
    service = FakeService()
    bot = TelegramBot(settings, api=api, service=service)
    await bot.handle_update({
        "message": {
            "chat": {"id": 222},
            "from": {"id": 111},
            "text": "📊 Статус",
        }
    })
    assert service.calls == [("status",)]
    assert api.messages[-1][1] == "STATUS"


@pytest.mark.asyncio
async def test_ask_callback_does_not_execute_action(settings):
    api = FakeAPI()
    service = FakeService()
    bot = TelegramBot(settings, api=api, service=service)
    await bot.handle_update({
        "callback_query": {
            "id": "cb1",
            "from": {"id": 111},
            "message": {"chat": {"id": 222}},
            "data": "ask:disable:uknig",
        }
    })
    assert service.calls == []
    markup = api.messages[-1][2]
    assert markup["inline_keyboard"][0][0]["callback_data"] == "do:disable:uknig"


@pytest.mark.asyncio
async def test_unauthorized_callback_cannot_execute(settings):
    api = FakeAPI()
    service = FakeService()
    bot = TelegramBot(settings, api=api, service=service)
    await bot.handle_update({
        "callback_query": {
            "id": "cb1",
            "from": {"id": 999},
            "message": {"chat": {"id": 222}},
            "data": "do:run:rutracker",
        }
    })
    assert service.calls == []
    assert api.callbacks == [("cb1", "Доступ запрещён")]
