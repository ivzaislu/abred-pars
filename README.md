# Abred Catalog Pipeline

Отдельный crawler/parser/feed producer для AudioBookRed.

Текущая версия пакета: `0.1.4`.

Producer не подключается к production PostgreSQL. Он читает внешние каталоги, формирует source-native `feed.json` + `manifest.json` и публикует GitHub Actions artifacts. Backend `ivzaislu/abred` отдельно валидирует и импортирует artifacts через `app.feed_auto`.

Все bulk catalog crawler/parser/feed producer проекта находятся здесь. Backend не должен выполнять scheduled bulk crawl внешних каталогов.

## Production schedules

```text
Uknig      каждый час в :07 UTC
Audiopolka каждый час в :17 UTC
RuTracker  каждые 2 часа в :47 UTC
```

Schedules разведены по минутам. Producer cron и Backend `feed_auto` — независимые механизмы: наличие artifact producer schedule не доказывает, что конкретный Backend source уже enabled/READY.

## Общий artifact/state contract

Для scheduled producer действует порядок:

```text
tests/preflight
→ crawl/parse
→ feed.json + manifest.json
→ source/feed audit
→ upload Actions artifact
→ persist cursor/cache state
```

Если upload artifact не удался, cursor state не должен продвигаться.

Artifacts:

```text
audiopolka-feed-<github-run-id>
rutracker-feed-<github-run-id>
uknig-feed-<github-run-id>
```

Production feed retention: 14 дней.

## Audiopolka

Workflow: `.github/workflows/audiopolka.yml`.

Расписание: каждый час в `:17 UTC`.

Scheduled crawl обрабатывает актуальную page 1 и finite descending bootstrap. Cursor хранится в `state/audiopolka.json`.

```bash
python -m abred_catalog_pipeline run-audiopolka \
  --state state/audiopolka.json \
  --out artifacts
```

## RuTracker

Workflow: `.github/workflows/rutracker.yml`.

Расписание: каждые 2 часа в `:47 UTC`.

Весь HTTP-трафик к RuTracker идёт через project Worker. Scheduled runs используют TorrServer enrichment для torrent metadata/files и chapters.

Required Worker settings:

```text
RUTRACKER_WORKER_URL
RUTRACKER_WORKER_TOKEN
RUTRACKER_WORKER_TOKEN_HEADER   default X-Proxy-Token
RUTRACKER_WORKER_MODE           default mirror
```

Transient Worker transport errors, HTTP `429` и `5xx` имеют bounded retry. Обычные non-retriable `4xx` завершаются без retry.

### Два TorrServer

`0.1.4` поддерживает pool:

```text
TORRSERVER_URL
TORRSERVER_URL_2
TORRSERVER_USERNAME
TORRSERVER_PASSWORD
```

Credentials общие для обоих instances. Разные `info_hash` обрабатываются максимум двумя параллельными metadata jobs; RuTracker/Worker HTML остаётся последовательным.

Pool использует least-in-flight scheduling. Один hash не отправляется одновременно на оба сервера. Timeout/network/HTTP `429`/`5xx` допускают один последовательный failover на второй сервер; structural metadata errors остаются blocking.

Run statistics:

```text
torrent_metadata.servers[].attempted
torrent_metadata.servers[].enriched
torrent_metadata.servers[].failed
torrent_metadata.failovers
```

Правила результата:

- `unsupported audio` — permanent reject и не удерживает cursor;
- временная Worker/TorrServer проблема удерживает cursor, если retry/failover исчерпан;
- structural TorrServer metadata failure остаётся blocking;
- truncated manual run не продвигает cursor.

### Обложки RuTracker

Static assets, smiles, badges, маленькие изображения и явно широкие декоративные полосы отбрасываются. При доступных размерах предпочитается portrait/book-like candidate; если безопасной обложки нет, `cover_url` остаётся пустым.

## Uknig

Uknig реализован отдельным package `abred_catalog_pipeline.uknig`.

Catalog pages: `https://uknig.com/?p=<N>`. Stable source ID берётся из `/books/<id>`.

Полная аудиокнига подтверждается только full playlist `/index.php/books/<id>/playlist.txt`.

Availability rules:

- `Прослушивание заблокировано правообладателем` → `rights_holder_blocked` tombstone;
- явный ознакомительный/preview marker → `preview_only` tombstone;
- пустой/недоступный full playlist → `preview_only`;
- playable record обязан иметь непустые chapters;
- production workflow дополнительно проверяет HTTPS media URL на `uknig.com`.

Canary workflow: `.github/workflows/uknig-canary.yml`.

Production workflow: `.github/workflows/uknig.yml`.

Расписание: каждый час в `:07 UTC`. Пока bootstrap не завершён, scheduled run обрабатывает page 1 + 20 deep-backfill страниц. После завершения bootstrap cursor планирует page 1 only.

```bash
python -m abred_catalog_pipeline.uknig \
  --state state/uknig.json \
  --out artifacts \
  --backfill-pages 20 \
  --delay 0.35
```

## State

```text
state/audiopolka.json
state/rutracker.json
state/uknig.json
```

State сейчас хранится в Git и scheduled workflows коммитят его обратно в `main` только после artifact upload. Это надёжно с точки зрения cursor durability, но создаёт шумные bot commits; перенос operational state в отдельный durable storage/state branch внесён в roadmap.

## Подтверждённый reliability debt

Audiopolka и Uknig сейчас различают permanent availability outcomes и generic `detail_fetch_or_parse_error`, но при generic transient/error outcome всё равно формируют `cursor_after` и workflow сохраняет его после upload.

Для deep bootstrap это риск пропуска книги после единичного timeout/5xx/неустойчивого parse: page может быть помечена пройденной, хотя отдельный detail не был успешно классифицирован.

Следующий producer patch должен сделать transient uncertainty cursor-blocking либо сохранять durable retry queue. Permanent tombstones/rejects могут продолжать двигать cursor.

## Тесты

```bash
pip install -e '.[test]'
pytest -q
```

Deterministic unit/fixture tests должны оставаться основным PR gate. Live source canaries полезны отдельно, но не должны подменять deterministic regression suite.

Следующая работа и подтверждённые cleanup-пункты: [`PATCH_ROADMAP.md`](PATCH_ROADMAP.md).
