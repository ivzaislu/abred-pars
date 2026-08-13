# История изменений

## 0.1.3

- RuTracker enrichment через TorrServer формирует полный transport: `torrent.files[]` и главы `torrent://<info_hash>/<file_index>`.
- Исправлено соответствие zero-based file index внутреннему transport-контракту Abred.
- Cursor удерживается на deep page при временной metadata-проблеме или незавершённой replay-политике.
- Scheduled RuTracker workflow публикует artifact до сохранения cursor/TorrServer state.
- Permanent reject `rutracker_unsupported_audio` больше не удерживает cursor.
- Временные Worker transport errors, HTTP `429` и `5xx` получили bounded retry; обычные non-retriable `4xx` завершаются без retry.
- Audiopolka workflow также переведён на безопасный порядок: artifact upload до cursor commit.

## 0.1.2

- Добавлен TorrServer metadata enrichment для RuTracker по magnet/info-hash.
- В feed появились concrete torrent files и playable chapters.
- Клиент TorrServer остаётся недеструктивным: не вызывает `rem`, `drop` или `wipe`, а новые magnets добавляет без постоянного сохранения в библиотеке.
- Добавлено сохранение metadata state в `state/rutracker.json`.

## 0.1.1-v3 — magnet-first production contract

- Magnet из RuTracker `viewtopic` и BTIH info-hash стали основным успешным transport-контрактом.
- Обычный CLI, scheduled runs и manual probes больше не запрашивают `dl.php`.
- Raw `.torrent` retrieval оставлен только как явный opt-in enrichment через `--download-torrents`.
- Добавлены `torrent_metadata_attempted` и статусы `magnet`, `torrent_metainfo`, `magnet_fallback`.
- Ошибка optional `.torrent` не блокирует запись, если topic содержит валидный magnet/info-hash.
- Флаг `--no-torrent-download` сохранён как скрытый compatibility alias.

## 0.1.1-v2 — усиление production probe

- Raw `.torrent` requests отправляют topic `Referer`, как в проверенном backend transport contract.
- Magnet-only fallback сохраняет release size из `viewforum` вместо нулевого размера.
- Добавлен regression test для Worker token, target и topic Referer при `dl.php` request.

## 0.1.1

- Добавлен RuTracker catalog pipeline только через Cloudflare Worker; прямого GitHub runner → RuTracker трафика нет.
- Добавлен production scope audiobook forums.
- Добавлены независимые per-forum cursors.
- `topic_id` остаётся стабильной source identity RuTracker.
- Перенесены hardened parsers `viewforum` и `viewtopic`.
- Добавлены BTIH normalization, bencoded `.torrent` parsing, file metadata и torrent-backed chapters.
- Поддержаны mirror/fetch Worker modes и настраиваемый token header с default `X-Proxy-Token`.
- Feed records поддерживают torrent metadata и явные source metadata flags.

## 0.1.0

- Первый GitHub catalog pipeline для Audiopolka.
- Page 1 + descending backfill cursor.
- Feed + manifest SHA-256 bundles.
- Preview-only rejection и rightsholder tombstones.
