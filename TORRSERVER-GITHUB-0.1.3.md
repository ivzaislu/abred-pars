# Abred catalog pipeline 0.1.3 — TorrServer metadata enrichment

## What this version does

RuTracker crawling stays in GitHub Actions through the existing Cloudflare Worker.
For an unconfirmed RuTracker `info_hash`, the GitHub runner asks the configured
TorrServer for torrent metadata using the magnet URI. An enriched feed contains:

- `torrent.info_hash`
- `torrent.magnet_uri`
- `torrent.files[]`
- playable `chapters[]` with `torrent://<info_hash>/<file_index>` media URLs

The backend 0.8.3.8 companion patch imports those files/chapters while preserving
existing transport rows when a later feed record is magnet-only.

## Shared TorrServer safety

The GitHub client is intentionally non-destructive:

- missing magnets are added with `save_to_db=false`;
- it never calls `rem`, `drop` or `wipe`;
- it only polls `get` until `file_stats` is available or the per-hash timeout expires.

This is important when the same TorrServer is used by other applications. The job
does not try to guess whether another client started using a torrent after GitHub
added it. Inactive non-persisted torrents are left to TorrServer's own lifecycle.

## File indexes

TorrServer exposes one-based, path-sorted web/stream IDs in `file_stats[].id`.
Abred catalog transport uses zero-based file indexes. Version 0.1.3 therefore stores
the zero-based `file_stats` position as `torrent.files[].index`; the persisted file
path is later used by the backend to resolve the current TorrServer stream ID.

## Cursor safety and metadata replay

A deep backfill page must not be skipped just because the per-run TorrServer request
limit was reached.

Version 0.1.3 keeps the deep cursor on the same page when either:

- metadata on that deep page was deferred by `TORRSERVER_MAX_NEW`; or
- an enriched deep-page hash still needs its configured replay delivery.

Page 1 does not hold the deep cursor because page 1 is fetched on every run.

Successful metadata state is persisted while the deep cursor is held, so the next
run does not discard completed work.

By default a hash needs **two successful enriched deliveries** before it becomes
`torrent_metadata_known`. This is a delivery-redundancy mechanism, not a backend
acknowledgement protocol: GitHub does not currently receive an import ACK from the
backend. `TORRSERVER_REPLAY_SUCCESSES=2` makes deep-page metadata appear in two
separate artifacts before that deep cursor can move on.

## Artifact/state ordering

The workflow uploads the feed artifact **before** committing `state/rutracker.json`.
If artifact upload fails, cursor/metadata state is not persisted and the next run can
repeat the same work.

## GitHub Actions configuration

Required repository variable:

- `TORRSERVER_URL` — externally reachable TorrServer base URL.

Recommended/optional repository variables:

- `TORRSERVER_MAX_NEW` — default `100` metadata requests per run.
- `TORRSERVER_TIMEOUT_SECONDS` — default `30` seconds per hash.
- `TORRSERVER_POLL_INTERVAL_SECONDS` — default `1` second.
- `TORRSERVER_REPLAY_SUCCESSES` — default `2` successful enriched deliveries.

Optional repository secrets, only when TorrServer Basic Auth is enabled:

- `TORRSERVER_USERNAME`
- `TORRSERVER_PASSWORD`

Do not place credentials directly in workflow YAML.

## First safe test

Run **RuTracker catalog feed** manually with:

- `max_topics = 5`
- `advance_cursor = false`
- `download_torrents = false`
- `torrserver_enrich = true`
- `torrserver_max_new = 5`

Because `advance_cursor=false`, the probe does not persist cursor or metadata state.
Check `crawl-result.json` for `torrent_metadata.enriched > 0`, then inspect `feed.json`
for non-empty `torrent.files` and `chapters`.

## State compatibility

Existing 0.1.2 `torrent_metadata_hashes` are loaded as already confirmed hashes.
Version 0.1.3 adds `torrent_metadata_pending`, while both cache fields remain outside
`cursor_before` / `cursor_after` in the feed payload.
