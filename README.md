# Abred Parser Server

`server_parser-0.0.1` is the production catalog producer for Abred. GitHub is
source control only: crawling, schedules, durable cursor state and feed delivery
do not use GitHub Actions or Actions artifacts.

Server version: **0.0.1**.

## Runtime shape

```text
Audiopolka / Uknig / RuTracker
              ↓
       parser modules
              ↓
       feed.json + manifest.json
              ↓
 immutable ZIP + SQLite registry
              ↓
      read-only parser API
              ↓
         Abred backend
```

The parser server has no production Abred PostgreSQL credentials. Backend pulls
feeds and remains responsible for validation, dry-run and catalog import.

## Sources and schedules

Default UTC schedules:

```text
Uknig      hourly at :07
Audiopolka hourly at :17
RuTracker  every 2 hours at :47
```

## Start with Docker

```bash
cp .env.server.example .env.server
# Set PARSER_API_TOKEN and source/TorrServer settings.

docker compose --env-file .env.server -f docker-compose.server.yml up -d --build
```

The compose file binds to `127.0.0.1:8081` by default. For a Backend on another
host, expose the API only through a private network/VPN or an authenticated TLS
reverse proxy.

## API

Health is public:

```text
GET /health
```

All `/v1/*` routes require `Authorization: Bearer <PARSER_API_TOKEN>` or
`X-Parser-Token`:

```text
GET /v1/sources
GET /v1/stats
GET /v1/feeds?source=uknig&after=0&limit=50
GET /v1/feeds/{feed_id}
GET /v1/feeds/{feed_id}/bundle
GET /v1/runs?source=uknig&limit=20
```

The API is intentionally read-only. It has no endpoint for remotely starting
expensive crawls.

## Durable storage

Operational data lives below `PARSER_DATA_DIR` (default `/data`):

```text
server.sqlite3       feed registry, run history, scheduler claims
state/*.json         durable crawler cursors/state
feeds/<source>/*.zip immutable feed bundles
staging/             temporary run output
locks/               per-source process locks
```

A source cursor advances only after its immutable ZIP has been durably published
and registered. Feed retention defaults to 96 hours.

## RuTracker

RuTracker uses the Worker transport and TorrServer enrichment pool.
`TORRSERVER_URL_2` is optional parallel/failover capacity. One successful
TorrServer metadata result is sufficient.

Transient per-topic failures are stored durably in the RuTracker retry queue and
do not hold the deep cursor.

## Backend integration

Parser-server is the **sole** catalog-feed transport for Backend. Backend:

1. requests feeds after its per-source cursor;
2. consumes the oldest visible feed first;
3. downloads the immutable ZIP;
4. verifies transport and manifest SHA-256;
5. performs source/RuTracker preflight and DB dry-run;
6. imports under its catalog mutation lock;
7. advances its cursor only after successful apply.

There is no GitHub artifact fallback and no Backend repository/artifact selector.

Detailed contract: [`SERVER_PARSER.md`](SERVER_PARSER.md).

## Telegram operations bot

The optional `telegram-bot` Compose profile runs on the parser host. It provides
allowlisted status, parser/backend logs and bounded Backend feed-control actions.
It has no Docker socket or arbitrary shell access. Backend requests use a
dedicated HMAC secret.

## GitHub Actions

No GitHub Actions workflow is used by the parser runtime. Existing workflow files
remain under `.github/workflows-disabled`.

## Tests

```bash
pip install -e '.[test]'
pytest -q
```
