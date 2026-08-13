# Abred Catalog Pipeline

Standalone catalog crawler/parser/feed producer for AudioBookRed.

Current package version: `0.1.3`.

Producer does not connect to production PostgreSQL and emits source-native feed artifacts for Backend import.

## Audiopolka

Workflow: `.github/workflows/audiopolka.yml`.

Current schedule: every hour at `:17 UTC`.

The workflow runs tests, crawls Audiopolka, uploads `feed.json` + `manifest.json`, and only then persists cursor state. Artifact publication must happen before cursor advancement so a failed upload cannot silently skip a cursor window.

Local run:

```bash
python -m abred_catalog_pipeline run-audiopolka \
  --state state/audiopolka.json \
  --out artifacts
```

## RuTracker

Workflow: `.github/workflows/rutracker.yml`.

Current schedule: every 2 hours at `:47 UTC`.

All RuTracker HTTP traffic goes through the project Worker. Scheduled production runs use TorrServer enrichment to resolve torrent transport into concrete files and chapters.

Manual bounded probe:

```bash
python -m abred_catalog_pipeline run-rutracker \
  --forums 2387 \
  --max-topics 5 \
  --state state/rutracker.json \
  --out artifacts
```

A truncated `--max-topics` run never advances cursors.

RuTracker failure semantics:

- deterministic unsupported-audio rejects do not hold the cursor;
- transient network, Worker, mapping and chapter failures remain blocking;
- transport errors, HTTP 429 and 5xx use bounded retry;
- ordinary non-retriable 4xx responses fail immediately.

Scheduled runs upload the artifact before persisting cursor/TorrServer state.

## Feed bundle

Every successful run writes:

```text
artifacts/<run-id>/feed.json
artifacts/<run-id>/manifest.json
```

`manifest.json` contains SHA-256 and exact byte size of canonical `feed.json`.

GitHub Actions artifacts are named:

```text
audiopolka-feed-<github-run-id>
rutracker-feed-<github-run-id>
```

Production Backend `ivzaislu/abred` validates and imports these artifacts through per-source `app.feed_auto` state. Producer never writes directly to Backend production DB.

## Tests

```bash
pip install -e '.[test]'
pytest -q
```

Workflow runs execute tests before crawling/publishing.
