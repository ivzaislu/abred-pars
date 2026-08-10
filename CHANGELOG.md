# Changelog

## 0.1.0

- Separate GitHub catalog pipeline repository.
- Audiopolka catalog/detail parser extracted from backend production logic.
- Always scans page 1 plus five descending backfill pages.
- Persistent `deep_page`/`last_page` cursor with wrap after page 2.
- Source-native feed with no PostgreSQL IDs.
- SHA-256 manifest for Backend 0.8.3.8 importer.
- Explicit rightsholder removals are tombstones.
- Preview-only content is rejected.
- GitHub Actions schedule, artifact upload, and cursor commit.
