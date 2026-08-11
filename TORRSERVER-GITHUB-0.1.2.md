# Abred catalog pipeline 0.1.2 — TorrServer metadata enrichment

## What changes

RuTracker crawling remains in GitHub Actions through the existing Cloudflare Worker.
For a previously unseen RuTracker `info_hash`, the GitHub runner asks the configured
TorrServer for torrent metadata using the magnet URI. The produced feed then contains:

- `torrent.info_hash`
- `torrent.magnet_uri`
- `torrent.files[]`
- playable `chapters[]` with `torrent://<info_hash>/<file_index>` media URLs

TorrServer is used only for metadata discovery. This client never calls `rem`, `drop`,
or `wipe`, and adds missing magnets with `save_to_db=false`.

Successful hashes are persisted in `state/rutracker.json` as
`torrent_metadata_hashes`. On later crawls a known hash is emitted magnet-only so the
shared TorrServer is not queried repeatedly. The backend must preserve existing
files/chapters when a later record is magnet-only.

## GitHub configuration

Repository variable (required for scheduled RuTracker runs):

- `TORRSERVER_URL` — externally reachable TorrServer base URL, for example `https://torr.example.net`

Optional repository variables:

- `TORRSERVER_MAX_NEW` — default `300`
- `TORRSERVER_TIMEOUT_SECONDS` — default `45`
- `TORRSERVER_POLL_INTERVAL_SECONDS` — default `1`

Optional repository secrets, only when TorrServer Basic Auth is enabled:

- `TORRSERVER_USERNAME`
- `TORRSERVER_PASSWORD`

Do not put credentials directly in the workflow YAML.

## First safe test

Run **RuTracker catalog feed** manually with:

- `max_topics = 5`
- `advance_cursor = false`
- `download_torrents = false`
- `torrserver_enrich = true`
- `torrserver_max_new = 5`

Expected `crawl-result.json` contains a `torrent_metadata` block. At least one healthy
magnet should show `enriched > 0`; enriched records must contain non-empty
`torrent.files` and `chapters`.

Because `advance_cursor=false`, this probe does not persist the cursor/hash cache.
After verification, run normally with `advance_cursor=true` or allow the scheduled run.

## Backend requirement

Backend 0.8.3.8 needs the companion patch
`abred-backend-0.8.3.8-rutracker-torrserver-feed.patch` before applying enriched feeds.
The patch allows validated `torrent://` RuTracker chapter URLs and imports enriched
RuTracker chapters in place while preserving existing chapters on magnet-only feeds.
