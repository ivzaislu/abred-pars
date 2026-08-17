# Abred Parser Server

`server_parser-0.0.1` turns `abred-pars` into a long-running parser/feed producer for a dedicated server. GitHub is source control only; production crawling, schedules, cursor state and feed delivery do not depend on GitHub Actions or Actions artifacts.

Server version: **0.0.1**.

## Runtime shape

```text
Audiopolka / Uknig / RuTracker
              ↓
      existing parser modules
              ↓
       feed.json + manifest.json
              ↓
 immutable ZIP + SQLite registry
              ↓
      read-only parser API
              ↓
         Abred backend
```

The parser server has no production Abred PostgreSQL credentials. The backend pulls feeds and keeps responsibility for its own validation, dry-run and catalog import.

## Sources and schedules

Default UTC schedules keep the current production cadence:

```text
Uknig      hourly at :07
Audiopolka hourly at :17
RuTracker  every 2 hours at :47
```

The scheduler is part of the server process and can be disabled when systemd timers are preferred.

## Start with Docker

```bash
cp .env.server.example .env.server
# Set PARSER_API_TOKEN and RuTracker/TorrServer settings.

docker compose --env-file .env.server -f docker-compose.server.yml up -d --build
```

The compose file binds to `127.0.0.1:8081` by default. For a backend on another host, expose it only through a private network/VPN or an authenticated TLS reverse proxy.

## API

Health is public:

```text
GET /health
```

All `/v1/*` routes require `Authorization: Bearer <PARSER_API_TOKEN>` or `X-Parser-Token`:

```text
GET /v1/sources
GET /v1/feeds?source=uknig&after=0&limit=50
GET /v1/feeds/{feed_id}
GET /v1/feeds/{feed_id}/bundle
GET /v1/runs?source=uknig&limit=20
```

The API is intentionally read-only. It has no endpoint for remotely starting expensive crawls.

## Local CLI

```bash
abred-parser-server run uknig
abred-parser-server run audiopolka
abred-parser-server run rutracker
abred-parser-server status
abred-parser-server list-feeds --source uknig
abred-parser-server serve
abred-parser-server scheduler
```

Manual parser runs do not require the API token. Serving the API does.

## Durable storage

Operational data lives below `PARSER_DATA_DIR` (default `/data`):

```text
server.sqlite3       feed registry, run history, scheduler claims
state/*.json         existing durable crawler cursors
feeds/<source>/*.zip immutable feed bundles
staging/             temporary run output
locks/               per-source process locks
```

A server run publishes an immutable bundle before advancing the source cursor. This preserves the existing producer invariant that operational state cannot move past source data that was never durably published.

## RuTracker

RuTracker keeps the existing Worker transport and TorrServer enrichment/pool. Server mode expects `RUTRACKER_WORKER_URL`; when `RUTRACKER_TORRSERVER_ENRICH=true`, at least `TORRSERVER_URL` must be configured and `TORRSERVER_URL_2` remains optional.

## Backend integration

The backend should replace GitHub artifact discovery with a feed provider that polls `/v1/feeds`, downloads the oldest unseen bundle, verifies the transport SHA plus the existing manifest SHA, then runs the same backend source-policy validation, dry-run and locked import used today.

Detailed deployment and migration notes: [`SERVER_PARSER.md`](SERVER_PARSER.md).

## GitHub Actions

No GitHub Actions workflow is used by this server runtime. Existing workflow files remain under `.github/workflows-disabled` and are not enabled by this branch.

## Tests

Deterministic tests remain available for local development:

```bash
pip install -e '.[test]'
pytest -q
```
