# Развёртывание Abred Parser Server на сервере

Этот файл фиксирует рабочую схему эксплуатации ветки `server_parser-0.0.1` на сервере Ubuntu 18.04 (`bionic`, `amd64`) с Docker, установленным через Snap.

## Текущая схема

Репозиторий хранится здесь:

```text
/opt/abred-pars
```

Файл окружения:

```text
/opt/abred-pars/.env.server
```

Docker установлен через Snap:

```text
/snap/bin/docker
```

Его `DockerRootDir`:

```text
/var/snap/docker/common/var-lib-docker
```

Из-за Snap confinement `docker compose` не может напрямую читать compose-файл и `.env.server` из `/opt`. Поэтому используется bind-mount репозитория в доступную Snap-область:

```text
/opt/abred-pars
        ↓ bind
/var/snap/docker/common/abred-pars
```

Compose всегда запускается с фиксированным project name:

```text
abred-pars
```

Это важно, чтобы сохранялось имя постоянного volume:

```text
abred-pars_parser_data
```

В нём лежат критичные данные parser-server:

```text
/data/server.sqlite3
/data/state/*.json
/data/feeds/<source>/*.zip
/data/staging/
/data/locks/
```

Нельзя удалять этот volume при обычном обновлении.

## Однократная настройка bind-mount

Создать каталог, видимый Snap Docker:

```bash
mkdir -p /var/snap/docker/common/abred-pars
```

Подключить репозиторий:

```bash
mount --bind \
  /opt/abred-pars \
  /var/snap/docker/common/abred-pars
```

Проверить:

```bash
ls -l /var/snap/docker/common/abred-pars/docker-compose.server.yml
ls -l /var/snap/docker/common/abred-pars/.env.server
git -C /var/snap/docker/common/abred-pars rev-parse HEAD
```

Чтобы bind восстанавливался после перезагрузки, в `/etc/fstab` должна быть строка:

```fstab
/opt/abred-pars /var/snap/docker/common/abred-pars none bind 0 0
```

Добавлять её повторно нельзя. Проверка:

```bash
grep -F '/opt/abred-pars /var/snap/docker/common/abred-pars none bind 0 0' /etc/fstab
```

Если после перезагрузки bind не подключился:

```bash
mkdir -p /var/snap/docker/common/abred-pars
mount /var/snap/docker/common/abred-pars
```

Проверка:

```bash
mountpoint /var/snap/docker/common/abred-pars
```

## Обычное обновление из GitHub

Работаем с физическим репозиторием в `/opt`:

```bash
cd /opt/abred-pars

git status --short
git rev-parse --abbrev-ref HEAD
git pull --ff-only
git rev-parse HEAD
```

Рабочая ветка parser-server:

```text
server_parser-0.0.1
```

Перед запуском Compose убедиться, что bind активен:

```bash
mountpoint -q /var/snap/docker/common/abred-pars || \
  mount /var/snap/docker/common/abred-pars
```

## Сборка и поднятие parser-server и Telegram-бота

Это стандартная команда для следующего обновления. Она пересобирает образ и поднимает оба сервиса:

```bash
docker compose \
  -p abred-pars \
  --env-file /var/snap/docker/common/abred-pars/.env.server \
  -f /var/snap/docker/common/abred-pars/docker-compose.server.yml \
  --profile telegram \
  up -d --build parser-server telegram-bot
```

Не использовать путь `/opt/abred-pars/.env.server` непосредственно в `docker compose` на этом сервере: Snap Docker его не видит и возвращает `couldn't find env file`.

## Поднять только parser-server

```bash
docker compose \
  -p abred-pars \
  --env-file /var/snap/docker/common/abred-pars/.env.server \
  -f /var/snap/docker/common/abred-pars/docker-compose.server.yml \
  up -d --build parser-server
```

## Поднять Telegram-бот без пересборки

Если существующий контейнер бота был только остановлен и его не нужно пересоздавать:

```bash
docker start abred-pars-telegram-bot-1
```

Проверить:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}'
docker logs --tail 50 abred-pars-telegram-bot-1
```

`telegram-bot` не хранит критичное состояние в `/data`; его работа основана на API parser-server и Backend. Критичное постоянное состояние находится в `abred-pars_parser_data` у parser-server.

## Проверка после обновления

Статус контейнеров:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}'
```

Health parser-server:

```bash
curl -s http://127.0.0.1:8081/health
echo
```

Проверить, что parser-server использует правильный volume:

```bash
docker inspect abred-pars-parser-server-1 \
  --format '{{range .Mounts}}{{println .Name "->" .Destination "source=" .Source}}{{end}}'
```

Ожидается:

```text
abred-pars_parser_data -> /data
```

Проверить наличие SQLite, состояния RuTracker и feed-файлов:

```bash
docker exec abred-pars-parser-server-1 \
  sh -c 'ls -lh /data/server.sqlite3 /data/state/rutracker.json && find /data/feeds/rutracker -type f | wc -l'
```

Проверка Compose-конфигурации без запуска:

```bash
docker compose \
  -p abred-pars \
  --env-file /var/snap/docker/common/abred-pars/.env.server \
  -f /var/snap/docker/common/abred-pars/docker-compose.server.yml \
  --profile telegram \
  config >/dev/null

echo $?
```

Нормальный результат — `0`.

## Резервная копия parser data перед опасными операциями

При обычном `git pull` + `docker compose up -d --build` полный backup не обязателен. Он нужен перед миграцией Docker, ручным вмешательством в storage, удалением feeds/SQLite или другими опасными операциями.

Сначала остановить сервисы:

```bash
docker stop abred-pars-telegram-bot-1
docker stop abred-pars-parser-server-1
```

Создать каталог backup:

```bash
mkdir -p /root/docker-migration-backup
```

Получить реальный mountpoint parser data:

```bash
PARSER_DATA="$(docker volume inspect abred-pars_parser_data --format '{{.Mountpoint}}')"
echo "$PARSER_DATA"
```

Сделать архив:

```bash
tar -C "$PARSER_DATA" \
  -czf /root/docker-migration-backup/abred-pars_parser_data.tgz .
```

Сохранить конфигурацию:

```bash
cp -a /opt/abred-pars/.env.server \
  /root/docker-migration-backup/.env.server

cp -a /opt/abred-pars/docker-compose.server.yml \
  /root/docker-migration-backup/docker-compose.server.yml
```

Проверить backup:

```bash
ls -lh /root/docker-migration-backup/
sha256sum /root/docker-migration-backup/abred-pars_parser_data.tgz
tar -tzf /root/docker-migration-backup/abred-pars_parser_data.tgz | head -30
```

В архиве должны быть как минимум:

```text
./server.sqlite3
./state/
./feeds/
```

После backup существующие контейнеры можно поднять без пересоздания:

```bash
docker start abred-pars-parser-server-1
docker start abred-pars-telegram-bot-1
```

## Что нельзя делать

Не выполнять при обычном обновлении:

```bash
docker compose down -v
docker volume rm abred-pars_parser_data
snap remove docker
```

Также нельзя менять project name `abred-pars`, если требуется продолжать использовать существующий volume `abred-pars_parser_data`.

GitHub Actions для runtime parser-server не используются.

## Важное про immutable feeds

Обновление кода parser-server исправляет только будущие запуски. Уже опубликованный ZIP feed не переписывается автоматически.

Если Backend заблокировал источник из-за плохого feed, обычная пересборка parser-server не делает старый feed корректным. Перед снятием `BLOCKED` нужно отдельно определить проблемный `feed_id/cursor` и безопасно обработать именно его, не пропуская последующие данные.
