# Abred Parser Server 0.0.1

This branch moves the production runtime away from GitHub Actions. GitHub remains source control only; the parser server owns schedules, durable crawler state and immutable feed storage.

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
read-only HTTPS API
      ↓
Abred backend pulls, validates, dry-runs and imports
```

The parser server must not receive production PostgreSQL credentials and must not write directly to the Abred backend database.

## Durable data

Everything operational is under `PARSER_DATA_DIR` (default `/data`):

```text
/data/server.sqlite3       feed registry, run history, schedule claims
/data/state/*.json         existing crawler cursor/state format
/data/feeds/<source>/*.zip immutable feed bundles
/data/staging/             temporary run output
/data/locks/               per-source process locks
```

A source cursor advances only after its feed ZIP has been atomically published and registered. Feed ZIPs are immutable while retained.

Feed bundles are retained for 96 hours (4 days) by default. Expired bundle files and their feed-registry rows are deleted at server startup and by hourly retention maintenance. Configure this with `PARSER_FEED_RETENTION_HOURS`.

## API

`GET /health` is public and contains no credentials. All `/v1/*` routes require either:

```text
Authorization: Bearer <PARSER_API_TOKEN>
```

or:

```text
X-Parser-Token: <PARSER_API_TOKEN>
```

Available read-only endpoints:

```text
GET /v1/sources
GET /v1/stats
GET /v1/feeds?source=uknig&after=0&limit=50
GET /v1/feeds/{feed_id}
GET /v1/feeds/{feed_id}/bundle
GET /v1/runs?source=uknig&limit=20
```

`/v1/stats` is the control/monitoring endpoint. It reports the retention window, retained feed count and bytes, missing bundle count, run counts by status, data-volume disk usage, scheduler configuration, and per-source last run/feed status.

`cursor` in the feed API is a server-local monotonically increasing SQLite sequence. A backend stores the last successfully imported cursor per source and requests rows after it. Feed identity itself remains `source:run_id` inside the existing v1 feed contract.

The API deliberately has no remote `run parser` endpoint. Expensive parser execution is controlled by the local scheduler or local CLI, not by the backend-facing token.

## Scheduler

Built-in schedules use UTC:

```text
Uknig      every hour at :07
Audiopolka every hour at :17
RuTracker  every 2 hours at :47
```

The minute offsets are configurable with `UKNIG_SCHEDULE_MINUTE`, `AUDIOPOLKA_SCHEDULE_MINUTE`, and `RUTRACKER_SCHEDULE_MINUTE`. RuTracker's interval is configured with `RUTRACKER_SCHEDULE_EVERY_HOURS` and defaults to `2`.

Schedule claims are persisted in SQLite, so restarting the process inside the same due minute does not create another scheduled run. Per-source file locks prevent overlapping runs across local processes. Retention maintenance also uses a durable hourly claim.

Disable the built-in scheduler with `PARSER_SCHEDULER_ENABLED=false` if systemd timers or another host scheduler will be used. Retention still runs once when the API server starts; recurring retention maintenance requires the built-in scheduler or equivalent host maintenance.

## Docker

```bash
cp .env.server.example .env.server
# fill PARSER_API_TOKEN and RuTracker/TorrServer settings

docker compose --env-file .env.server -f docker-compose.server.yml up -d --build
```

The compose file binds to `127.0.0.1:8081` by default. Use a private network, VPN or authenticated TLS reverse proxy if the Abred backend is on another machine; do not expose the parser API directly to the public Internet just by changing the bind address.

The image runs as a non-root user. `/data` must be persistent and backed up.

## Monitoring

Example:

```bash
TOKEN=$(grep '^PARSER_API_TOKEN=' .env.server | cut -d= -f2-)
curl -sS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8081/v1/stats
```

A healthy server should normally show `missing_bundles: 0`, no long-lived `running` runs, recent `last_run`/`last_feed` values for enabled sources, and enough `disk.free_bytes` for the four-day retention window.

## Local operations

```bash
abred-parser-server run uknig
abred-parser-server run audiopolka
abred-parser-server run rutracker
abred-parser-server status
abred-parser-server list-feeds --source uknig
abred-parser-server serve
abred-parser-server scheduler
```

A manual `run` does not require `PARSER_API_TOKEN`; `serve` does.

## RuTracker

RuTracker still uses the existing Worker transport and TorrServer enrichment. Server mode expects `RUTRACKER_WORKER_URL`. With `RUTRACKER_TORRSERVER_ENRICH=true` (the default), at least `TORRSERVER_URL` must be configured; `TORRSERVER_URL_2` remains optional for pool/failover and parallel metadata work. One successful TorrServer metadata result is sufficient (`TORRSERVER_REPLAY_SUCCESSES=1`).

## Backend migration

Backend migration should replace `GitHubCatalogArtifactClient` with a provider that:

1. calls `/v1/feeds?source=<source>&after=<cursor>`;
2. consumes the oldest returned feed first;
3. downloads `/v1/feeds/{feed_id}/bundle`;
4. verifies `X-Bundle-SHA256` and then the existing manifest/feed SHA-256;
5. runs the existing structural/source-policy preflight and dry-run;
6. imports under the existing catalog mutation lock;
7. advances the backend cursor only after successful import.

Because parser feeds expire after four days, backend monitoring should alert if a source has not successfully imported for long enough to approach the retention window.

The backend should continue treating parser output as untrusted input even when both servers are operated by the same owner.

## GitHub Actions

No GitHub Actions workflow is required by server mode. Existing workflows remain in `.github/workflows-disabled`; this branch does not enable or execute them.
