# Abred Catalog Pipeline

Отдельный crawler/parser/feed producer для AudioBookRed.

Текущая версия пакета: `0.1.4`.

Producer не подключается к production PostgreSQL и публикует source-native feed artifacts, которые Backend отдельно валидирует и импортирует.

Все массовые catalog crawler/parser/feed producer проекта находятся в этом репозитории. Backend `ivzaislu/abred` не должен выполнять bulk crawl внешних каталогов.

## Audiopolka

Workflow: `.github/workflows/audiopolka.yml`.

Расписание: каждый час в `:17 UTC`.

Порядок run: тесты → crawl → `feed.json`/`manifest.json` → upload artifact → сохранение cursor state. Artifact публикуется до cursor commit, чтобы не потерять диапазон при ошибке upload.

Локальный запуск:

```bash
python -m abred_catalog_pipeline run-audiopolka \
  --state state/audiopolka.json \
  --out artifacts
```

## RuTracker

Workflow: `.github/workflows/rutracker.yml`.

Расписание: каждые 2 часа в `:47 UTC`.

Весь HTTP-трафик к RuTracker идёт через project Worker. Scheduled runs используют TorrServer enrichment для получения torrent files и chapters.

### Два TorrServer

`0.1.4` поддерживает pool из двух TorrServer:

```text
TORRSERVER_URL
TORRSERVER_URL_2
TORRSERVER_USERNAME
TORRSERVER_PASSWORD
```

`TORRSERVER_USERNAME` и `TORRSERVER_PASSWORD` общие для обоих серверов; отдельные credentials для второго сервера не нужны.

Разные `info_hash` обрабатываются максимум двумя параллельными metadata jobs. Pool использует least-in-flight scheduling; один hash не отправляется одновременно на оба сервера. При timeout/network/HTTP `429`/`5xx` разрешён один последовательный failover на второй сервер. Structural metadata errors не failover'ятся и остаются blocking.

В `crawl-result.json` доступны:

```text
torrent_metadata.servers[].attempted
torrent_metadata.servers[].enriched
torrent_metadata.servers[].failed
torrent_metadata.failovers
```

RuTracker/Worker HTML-запросы при этом остаются последовательными — параллелится только TorrServer metadata enrichment.

Правила ошибок:

- `unsupported audio` — permanent reject и не удерживает cursor;
- временные network/Worker/TorrServer ошибки остаются blocking, если исчерпан retry/failover;
- HTTP `429`, `5xx` и transport errors Worker имеют bounded retry;
- обычные non-retriable `4xx` завершаются без retry;
- structural TorrServer metadata failure остаётся blocking.

RuTracker workflow публикует artifact до сохранения cursor/TorrServer state.

### Обложки RuTracker

`0.1.4` больше не принимает первую картинку post автоматически. Static assets, smiles, badges, маленькие изображения и явно широкие декоративные полосы отбрасываются. При доступных размерах предпочитается portrait/book-like aspect ratio; если безопасного кандидата нет, `cover_url` остаётся пустым.

## Uknig

Uknig реализован отдельным source package `abred_catalog_pipeline.uknig`.

Каталог использует `https://uknig.com/?p=<N>`, stable source ID берётся из `/books/<id>`. Полная аудиокнига подтверждается только непустым full playlist `/index.php/books/<id>/playlist.txt`; наличие одного ознакомительного фрагмента недостаточно.

Жёсткие правила доступности:

- `Прослушивание заблокировано правообладателем` → `rights_holder_blocked`, playable record не создаётся;
- есть только ознакомительный фрагмент, но нет полного playlist → `preview_only`, playable record не создаётся;
- в feed попадают только книги с непустым full playlist и валидными HTTPS media URL.

Canary workflow: `.github/workflows/uknig-canary.yml`.

Production feed workflow: `.github/workflows/uknig.yml`.

Расписание: каждый час в `:07 UTC`, перед Audiopolka (`:17 UTC`). Пока bootstrap не завершён, каждый scheduled run обрабатывает актуальную page 1 плюс 20 deep-backfill страниц (`backfill_pages=20`), то есть до 21 страницы каталога за запуск. После завершения bootstrap cursor-механизм продолжит проверять page 1 без повторного обхода старого диапазона.

Workflow формирует `uknig-feed-<github-run-id>` и сохраняет `state/uknig.json` только после успешного upload artifact. Задержка между Uknig HTTP requests по умолчанию — `0.35` секунды.

Ручной локальный запуск с тем же deep-backfill лимитом:

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

Пустой state означает полный bootstrap с нуля. Для RuTracker это также означает повторный сбор TorrServer metadata cache.

## Feed artifacts

```text
audiopolka-feed-<github-run-id>
rutracker-feed-<github-run-id>
uknig-feed-<github-run-id>
```

Backend `ivzaislu/abred` импортирует их через per-source `app.feed_auto`. Producer напрямую в production DB не пишет.

## Тесты

```bash
pip install -e '.[test]'
pytest -q
```
