# Telegram Ops Bot

The bot runs next to parser-server, where Telegram is reachable, and reads parser
statistics through the local authenticated parser API. Backend operations use a
separate HMAC-shared secret and a narrow `/v1/telegram-ops/*` API.

Main reply keyboard:

- `📊 Статус`
- `🧩 Парсер`
- `📦 Фиды`
- `🖥 Бэкенд`
- `⚙️ Управление`

Parser/control source selection uses inline buttons. Mutating backend actions are
never performed on the first tap: the bot shows a separate confirmation button.

Configure `.env.server` with the existing Telegram bot token and your allowlisted
Telegram IDs plus backend connectivity:

```dotenv
TELEGRAM_BOT_TOKEN=<secret>
TELEGRAM_ALLOWED_USER_IDS=<your numeric user id>
TELEGRAM_ALLOWED_CHAT_IDS=<optional private chat id>
TELEGRAM_BACKEND_URL=http://<backend-address>:8000
TELEGRAM_BACKEND_TOKEN=<independent 32+ char secret shared with backend>
```

Start only the optional bot profile:

```bash
docker compose --env-file .env.server -f docker-compose.server.yml \
  --profile telegram up -d --build telegram-bot
```

No inbound bot port or webhook is required.
