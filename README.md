# Abred Catalog Pipeline 0.1.0

Standalone Audiopolka crawler/parser/feed writer intended for GitHub Actions.
It deliberately has **no FastAPI, PostgreSQL, SQLAlchemy production models,
profiles, sync, or TorrServer dependencies**.

## Crawl policy

Every run:

1. fetch page 1;
2. detect the current last catalog page;
3. fetch 5 backfill pages from `deep_page` downward;
4. never include page 1 in the backfill window;
5. after page 2, wrap the next `deep_page` to the current last page.

Example for 503 pages:

```text
run 1: 1, 503, 502, 501, 500, 499
run 2: 1, 498, 497, 496, 495, 494
...
run N: 1, 6, 5, 4, 3, 2
next : 1, 503, 502, 501, 500, 499
```

Cursor is persisted in `state/audiopolka.json`.

## Feed bundle

Each run writes:

- `artifacts/<run-id>/feed.json`
- `artifacts/<run-id>/manifest.json`

`manifest.json` contains SHA-256 of the canonical `feed.json` bytes. Feed
records use source-native immutable identities (`source + external_id`) and do
not contain PostgreSQL IDs.

Explicit rightsholder removals become `tombstones`; preview-only books and
parse/fetch failures are recorded under `rejected` instead of being imported
as normal books.

## Local use

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest -q

python -m abred_catalog_pipeline plan-pages --last-page 503
python -m abred_catalog_pipeline run-audiopolka \
  --state state/audiopolka.json \
  --out artifacts
```

The project does not import feeds into the backend. That belongs to Backend
0.8.3.8.
