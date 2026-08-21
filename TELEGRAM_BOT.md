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

Generic start command for a normal Docker installation:

```bash
docker compose --env-file .env.server -f docker-compose.server.yml \
  --profile telegram up -d --build telegram-bot
```

## Запуск на production-сервере с Docker Snap

На текущем production-сервере репозиторий расположен в `/opt/abred-pars`, а
Docker установлен через Snap. Поэтому Compose нужно запускать через bind-путь
`/var/snap/docker/common/abred-pars`.

Стандартная команда для следующей сборки и поднятия одновременно parser-server
и Telegram-бота:

```bash
docker compose \
  -p abred-pars \
  --env-file /var/snap/docker/common/abred-pars/.env.server \
  -f /var/snap/docker/common/abred-pars/docker-compose.server.yml \
  --profile telegram \
  up -d --build parser-server telegram-bot
```

Если существующий контейнер бота был только остановлен и пересборка не нужна:

```bash
docker start abred-pars-telegram-bot-1
```

Проверка:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}'
docker logs --tail 50 abred-pars-telegram-bot-1
```

Полный русский runbook, включая bind-mount, `/etc/fstab`, backup постоянного
parser volume и проверки после обновления: [`SERVER_DEPLOY_RU.md`](SERVER_DEPLOY_RU.md).

No inbound bot port or webhook is required.
