# Changelog

## 0.1.1-v2 (production probe hardening)

- Raw `.torrent` requests now send the topic `Referer`, matching the proven backend transport contract.
- Magnet-only fallback keeps the `viewforum` listed release size instead of reporting zero bytes.
- Added a regression test asserting Worker token, target and topic Referer on `dl.php` requests.

## 0.1.1

- Added RuTracker catalog pipeline through a Cloudflare Worker only; no direct GitHub runner -> RuTracker traffic.
- Added production audiobook forum scope from the current backend.
- Added independent per-forum `page 1 + five descending backfill pages` cursors.
- Kept `topic_id` as the stable RuTracker source identity.
- Ported the hardened production RuTracker `viewforum` and `viewtopic` metadata parsers.
- Added magnet BTIH normalization, bencoded `.torrent` parsing, file metadata, and torrent-backed chapter generation.
- Added mirror/fetch Worker modes and configurable token header, with `X-Proxy-Token` default.
- Added `rutracker.yml` GitHub Actions workflow. Manual bounded runs do not advance cursors; scheduled runs remain disabled until `RUTRACKER_ENABLED=true`.
- Feed records now support optional torrent metadata and explicit source metadata presence flags.

## 0.1.0

- Initial Audiopolka GitHub catalog pipeline.
- Page 1 plus descending five-page backfill cursor.
- Feed + manifest SHA-256 bundles.
- Preview-only rejection and rightsholder tombstones.
