# Abred Parser Server 0.0.1

Production catalog ingestion is server-to-server. GitHub remains source control
only and is not a feed transport.

## Contract

```text
external catalogs
      ↓
parser server
      ↓
feed.json + manifest.json
      ↓
immutable ZIP in /data/feeds
      ↓
read-only authenticated API
      ↓
Abred backend pulls, validates, dry-runs and imports
```

Parser must not receive production PostgreSQL credentials and must not write
directly to the Backend database.

## Durable data

```text
/data/server.sqlite3       feed registry, run history, schedule claims
/data/state/*.json         durable crawler cursor/state
/data/feeds/<source>/*.zip immutable feed bundles
/data/staging/             temporary run output
/data/locks/               per-source process locks
```

A source cursor advances only after the ZIP has been atomically published and
registered. Bundles are immutable while retained. Default retention is 96 hours.

## API

`GET /health` is public. All `/v1/*` routes require `PARSER_API_TOKEN` via Bearer
or `X-Parser-Token`.

```text
GET /v1/sources
GET /v1/stats
GET /v1/feeds?source=uknig&after=0&limit=50
GET /v1/feeds/{feed_id}
GET /v1/feeds/{feed_id}/bundle
GET /v1/runs?source=uknig&limit=20
```

The API deliberately has no remote parser-run endpoint.

## Scheduler

Built-in UTC schedules:

```text
Uknig      every hour at :07
Audiopolka every hour at :17
RuTracker  every 2 hours at :47
```

Schedule claims are persisted in SQLite and per-source file locks prevent
overlap. Retention maintenance also has a durable hourly claim.

## Backend consumer contract

Backend is already cut over to parser-server and stores
`last_success_cursor` per source in `catalog_feed_server_source_state`.

For each source it:

1. calls `/v1/feeds?source=<source>&after=<cursor>`;
2. consumes the oldest row;
3. downloads `/v1/feeds/{feed_id}/bundle`;
4. verifies bundle/feed SHA-256 and size;
5. validates source policy and RuTracker transport completeness;
6. runs a DB dry-run;
7. imports under the shared catalog mutation lock;
8. advances the cursor only after successful apply.

If Backend may have been offline longer than the advertised retention window,
it fails closed rather than silently skipping expired feeds.

**GitHub Actions artifacts are retired permanently.** Backend has no supported
GitHub feed credentials, artifact-floor, artifact-skip or repository fallback.
GitHub may still be used by Abred for unrelated source-control and Android update
distribution.

## RuTracker

RuTracker uses Worker + TorrServer enrichment. One successful TorrServer result
is enough. Transient topic/TorrServer failures are stored durably in
`topic_retry_queue` and retried on later runs without freezing the deep cursor.

## Monitoring

`/v1/stats` reports feed storage, disk usage, scheduler settings and per-source
last run/feed. Telegram Ops Bot can show the same operational status plus
sanitized bounded logs.

## GitHub Actions

No GitHub Actions workflow is required or enabled for this runtime. Workflow
files remain under `.github/workflows-disabled`.
