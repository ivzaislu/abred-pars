# Abred Catalog Pipeline

Отдельный crawler/parser/feed producer для AudioBookRed.

Текущая версия пакета: `0.1.3`.
Следующий patch: `0.1.4` — Uknig, dual-TorrServer RuTracker и исправление выбора обложек.

Подробный план: [`PATCH_ROADMAP.md`](PATCH_ROADMAP.md).

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

Правила ошибок:

- `unsupported audio` — permanent reject и не удерживает cursor;
- временные network/Worker/TorrServer ошибки остаются blocking;
- HTTP `429`, `5xx` и transport errors имеют bounded retry;
- обычные non-retriable `4xx` завершаются без retry.

RuTracker workflow также публикует artifact до сохранения cursor/TorrServer state.

## State

```text
state/audiopolka.json
state/rutracker.json
```

Пустой state означает полный bootstrap с нуля. Для RuTracker это также означает повторный сбор TorrServer metadata cache.

## Feed artifacts

```text
audiopolka-feed-<github-run-id>
rutracker-feed-<github-run-id>
```

Backend `ivzaislu/abred` импортирует их через per-source `app.feed_auto`. Producer напрямую в production DB не пишет.

## Тесты

```bash
pip install -e '.[test]'
pytest -q
```
