# История изменений

## Unreleased — после 0.1.4

- Uknig production workflow переведён с manual-only на hourly schedule `7 * * * *` (`:07 UTC`).
- Scheduled Uknig bootstrap обрабатывает page 1 + 20 deep-backfill страниц за run с default delay `0.35s`.
- Исправлена preview-only semantics Uknig: явный ознакомительный фрагмент публикуется как tombstone даже если страница содержит CTA/ссылку на полную версию.
- Uknig cursor state уже продвигался production runs после успешной публикации artifacts.
- Документация синхронизирована с фактическим scheduled state; старое утверждение `manual-only` больше не актуально.

### Audit debt, не исправленный в этом docs patch

- Audiopolka/Uknig generic `detail_fetch_or_parse_error` сейчас не удерживает deep cursor; transient detail failure может привести к пропуску старой страницы после продвижения bootstrap state.
- Artifact cleanup workflow удаляет только `audiopolka-feed-*` и `rutracker-feed-*`, но не `uknig-feed-*`.
- Operational cursor/cache state коммитится ботом прямо в `main`, что смешивает code history и runtime state history.
- Uknig live canary обращается к внешнему source из PR workflow; deterministic tests и live-source verification стоит разделить жёстче.
- В `tools/` остаётся one-shot `apply_rutracker_title_cleanup.py`, хотя соответствующая parser/test логика уже находится в main; перед удалением нужен final equivalence check.

## 0.1.4

- RuTracker metadata enrichment переведён на pool из `TORRSERVER_URL` + `TORRSERVER_URL_2` с общими `TORRSERVER_USERNAME` / `TORRSERVER_PASSWORD`.
- Разные `info_hash` могут обрабатываться двумя параллельными metadata workers; RuTracker/Worker HTML остаётся последовательным.
- Добавлен least-in-flight scheduling: один hash не отправляется одновременно на оба TorrServer.
- Timeout/network/HTTP `429`/`5xx` допускают один последовательный failover на второй TorrServer; structural metadata errors остаются blocking без failover.
- В статистику run добавлены per-server `attempted/enriched/failed/in_flight` и общий `failovers`.
- Исправлен выбор RuTracker `cover_url`: static/smiles/badges, маленькие изображения и широкие декоративные полосы отбрасываются; предпочитаются portrait/book-like кандидаты.
- Добавлен fixture corpus для обычной обложки, нескольких изображений, широкого декора перед обложкой и post без пригодной обложки.
- Исправлена старая битая RuTracker-разметка `Цикл/серия`: следующие metadata-поля больше не захватываются в `series_name`; аномально длинное неразделимое значение отбрасывается вместо отправки структурно невалидной строки в Backend.
- Uknig parser/crawler подтверждает только full playlist и исключает rights-holder-blocked/preview-only записи.
- Добавлен production workflow `uknig.yml` с безопасным порядком `tests → crawl → audit → artifact upload → state commit`. На момент самого release `0.1.4` cron ещё не был включён; hourly schedule добавлен позже в `main` и отражён в разделе Unreleased.
- Версия пакета поднята до `0.1.4`.

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
