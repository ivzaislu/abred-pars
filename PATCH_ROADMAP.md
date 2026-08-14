# Дорожная карта патчей Abred Catalog Pipeline

`ivzaislu/abred-pars` — единственное место для массовых catalog crawler/parser/feed producer AudioBookRed. Backend `ivzaislu/abred` только валидирует и импортирует готовые artifacts.

## Контрольная точка 2026-08-14

```text
package version: 0.1.4
Uknig:      hourly :07 UTC
Audiopolka: hourly :17 UTC
RuTracker:  every 2h :47 UTC
```

Uknig hourly schedule уже включён в `main`; старый `manual-only` roadmap больше не актуален.

---

# P0/P1 — cursor reliability

## 1. Не продвигать deep cursor при transient detail failure

Подтверждено в `audiopolka/crawler.py` и `uknig/crawler.py`: generic `detail_fetch_or_parse_error` попадает в `rejected`, после чего `cursor_after` всё равно сохраняется workflow после artifact upload.

Для descending bootstrap это может навсегда пропустить старую книгу после одного timeout/5xx/временного parse failure.

Новый contract:

- permanent availability outcome (`rights_holder_blocked`, подтверждённый preview-only, permanent unsupported format) может двигать cursor;
- transient network/HTTP 429/5xx/timeout/temporary parse uncertainty удерживает соответствующую deep page;
- альтернативно допустима durable retry queue по `(source, external_id, page)` с гарантированным повтором до terminal classification;
- feed result должен отдельно считать `transient_failures` и `permanent_rejects`;
- workflow не коммитит cursor, если run cursor-blocked;
- regressions для одного failed detail среди успешной deep page.

RuTracker уже имеет более строгие cursor-held semantics для metadata failures; общий producer contract стоит унифицировать.

## 2. Bounded retry для Audiopolka/Uknig HTTP

Uknig/Audiopolka parser clients сейчас не имеют общего bounded retry слоя, в отличие от RuTracker Worker client.

Добавить консервативный retry для:

```text
transport error
HTTP 429
HTTP 5xx
```

Без retry для обычных non-retriable 4xx. Retry не должен превращать permanent preview/rights-holder outcome в transient loop.

---

# P1 — source transport/data policy

## 3. Централизовать Uknig media URL policy в parser

Сейчас parser `_media_url()` принимает любой syntactically valid `http/https`, а production workflow и Backend уже требуют HTTPS host `uknig.com`.

Нужно:

- parser-level allowlist `https + uknig.com`;
- reject/terminal classification для foreign host / cleartext media URL;
- unit regressions на `http://uknig.com`, foreign host и malformed composite playlist field;
- workflow audit оставить defense-in-depth, а не первым местом, где enforced transport contract становится известен.

## 4. Общий feed source policy helper

Повторяющиеся source invariants (`requires chapters`, allowed hosts, torrent requirements, permanent/transient error classes) постепенно вынести в явный producer-side policy layer. Это уменьшит расхождение parser/crawler/workflow audit/Backend policy.

---

# P1 — workflows/operations

## 5. Cleanup catalog artifacts должен знать Uknig

`.github/workflows/cleanup-catalog-artifacts.yml` сейчас удаляет только:

```text
audiopolka-feed-*
rutracker-feed-*
```

Добавить `uknig-feed-*` и переименовать confirmation description с двух sources на все catalog feed artifacts. Желательно предусмотреть optional source filter вместо only-delete-all.

## 6. Разделить deterministic PR CI и live-source canary

`uknig-canary.yml` запускается на pull_request и делает реальный network crawl/known-book check.

Live canary полезен, но внешний сайт может измениться/быть временно недоступен независимо от качества PR.

План:

- PR gate: deterministic unit/fixture tests + static feed-contract tests;
- live Uknig canary: scheduled/manual или non-blocking separate check;
- при изменении source markup live canary должен давать диагностический artifact, а не скрывать deterministic regression result.

Аналогично сохранить dual TorrServer canary как integration verification, не подменяя unit suite.

## 7. Operational state не должен бесконечно шуметь в `main`

Audiopolka/Uknig/RuTracker workflows коммитят cursor/cache state прямо в `main`. Это durable и collision-safe через rebase/retry, но смешивает runtime state updates и code history.

Исследовать перенос state в:

- dedicated `catalog-state` branch;
- release/Actions artifact with durable restore;
- external small KV/object storage.

Требования: atomic update, conflict detection, easy manual recovery, no hidden dependency on ephemeral Actions cache.

До миграции текущий Git-state подход сохранять; не менять state storage без migration/recovery plan.

---

# P1/P2 — code cleanup

## 8. Убрать import-time monkeypatch в RuTracker package

`rutracker/__init__.py` заменяет `parser.RuTrackerWorkerClient`, `_cover_from_post` и `_normalize_series_name` во время import.

Это работает, но усложняет reasoning/import-order и tests.

План:

- перенести retry/cover/series helpers в явные imports/composition;
- parser/crawler должны зависеть от конкретных helper interfaces, а не package side effects;
- сохранить compatibility exports без mutation module globals.

## 9. Проверить и удалить one-shot patch tool

`tools/apply_rutracker_title_cleanup.py` содержит текстовый source patcher для уже существующей parser/test логики.

Перед удалением:

- подтвердить, что все целевые parser blocks уже присутствуют в `main`;
- подтвердить regression tests;
- удалить one-shot script, чтобы его случайно не запустили повторно против нового parser.

## 10. Декомпозировать большой RuTracker parser

`rutracker/parser.py` уже концентрирует parsing, title cleanup, worker transport, torrent helpers и series logic.

Не делать большой rewrite. Выносить по одному покрытому regression набору:

```text
title/metadata normalization
worker HTTP client
series parsing
magnet/torrent primitives
```

Цель — уменьшить blast radius следующих source fixes.

---

# P2 — bootstrap completeness/observability

## 11. Audit после `backfill_complete=true`

`plan_pages()` после завершения descending bootstrap намеренно сканирует только page 1.

Добавить periodic audit/overlap strategy, чтобы обнаруживать:

- pagination reordering;
- позднее изменение availability старых книг;
- записи, пропущенные во время transient failure до cursor-hardening.

Варианты: редкий overlap scan первых N страниц, sampled old-page audit или retry inventory. Не возвращаться к бесконечному full crawl каждый час.

## 12. Producer health summary

Для каждого scheduled run публиковать легко сравнимые поля:

```text
pages
catalog_rows
records
tombstones
permanent_rejects
transient_failures
cursor_advanced / cursor_held
backfill_complete
artifact sha256
```

Добавить anomaly thresholds/alerts для внезапного `records=0`, резкого роста rejected/transient failures или долгого отсутствия cursor progress.

---

# Release discipline

Следующий package release после `0.1.4` должен оформлять не просто номер, а закрытый набор producer invariants:

```text
regression tests
→ deterministic CI green
→ live canary/integration evidence
→ artifact before state persistence
→ cursor failure semantics verified
→ README/CHANGELOG/ROADMAP current
→ Backend compatibility dry-run
```

Не считать producer patch завершённым только по успешному GitHub Action: для изменений feed semantics нужен Backend validation/import canary.
