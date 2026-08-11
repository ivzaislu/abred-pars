# Abred Catalog Pipeline

Standalone GitHub-hosted catalog crawler/parser/feed writer for AudioBookRed.
It does not connect to the production PostgreSQL database and never knows Abred UUIDs.

## Sources

### Audiopolka — Parser 0.1.0

Each run always scans page 1 and then five descending backfill pages. The cursor is stored in `state/audiopolka.json`.

```bash
python -m abred_catalog_pipeline run-audiopolka \
  --state state/audiopolka.json \
  --out artifacts
```

### RuTracker — Parser 0.1.1

All RuTracker HTTP traffic goes through a project-controlled Cloudflare Worker. GitHub Actions never talks to `rutracker.org` directly.

Required GitHub Actions configuration:

- variable `RUTRACKER_WORKER_URL`, e.g. `https://rutracker.johann0789.workers.dev`;
- secret `RUTRACKER_WORKER_TOKEN`;
- optional variable `RUTRACKER_WORKER_TOKEN_HEADER` (default `X-Proxy-Token`);
- optional variable `RUTRACKER_WORKER_MODE` (`mirror` by default, `fetch` also supported);
- optional variable `RUTRACKER_ENABLED=true` to enable scheduled full runs after manual verification.

Manual probe of one forum and five topics without advancing the cursor:

```bash
RUTRACKER_WORKER_URL=https://rutracker.example.workers.dev \
RUTRACKER_WORKER_TOKEN=... \
python -m abred_catalog_pipeline run-rutracker \
  --forums 2387 \
  --max-topics 5 \
  --state state/rutracker.json \
  --out artifacts
```

Optional raw `.torrent` enrichment probe (not required for a valid feed):

```bash
python -m abred_catalog_pipeline run-rutracker \
  --forums 2387 \
  --max-topics 5 \
  --download-torrents \
  --state state/rutracker.json \
  --out artifacts
```

A full cursor-advancing run uses all configured audiobook forums and the roadmap schedule `page 1 + five descending backfill pages` independently for each forum:

```bash
python -m abred_catalog_pipeline run-rutracker \
  --advance-cursor \
  --state state/rutracker.json \
  --out artifacts
```

The stable source identity is the RuTracker `topic_id`, never the viewforum page number. `viewforum.php` discovers topics; `viewtopic.php?t=<topic_id>` supplies metadata. **Magnet + BTIH info-hash are the primary transport identity.** A normal run does not call `dl.php` and emits `torrent_metadata_status=magnet` with an empty file list until the backend/TorrServer resolves concrete files. Raw `.torrent` metainfo is an optional probe/enrichment only: pass `--download-torrents` (or enable the manual workflow checkbox) to try it through the same Worker. If that optional request fails while the topic still has a valid BTIH magnet, the record remains successful as `magnet_fallback`; the diagnostic is retained but the topic is not rejected.

## Feed bundle

Every successful run writes:

```text
artifacts/<run-id>/feed.json
artifacts/<run-id>/manifest.json
```

`manifest.json` contains the SHA-256 and exact byte size of canonical `feed.json`.

The feed contains source-native external IDs only. RuTracker records additionally contain torrent metadata (`info_hash`, magnet URI, torrent URL, file list, seed/leech snapshot) when available.

## Tests

```bash
pip install -e '.[test]'
pytest -q
```
