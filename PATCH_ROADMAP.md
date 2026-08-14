# Дорожная карта патчей Abred Catalog Pipeline

`ivzaislu/abred-pars` — единственное место для массовых catalog crawler/parser/feed producer AudioBookRed. Backend `ivzaislu/abred` только валидирует и импортирует готовые artifacts.

## Следующий patch: `0.1.4`

Порядок общего release train:

```text
abred-pars 0.1.4
→ Backend 0.8.3.8.3
→ Uknig production cutover
→ Android 0.5.3.6 / code 59
```

## Uknig

Добавить `https://uknig.com/` как третий producer source:

- parser, crawler, state, CLI и GitHub Actions workflow;
- artifact `uknig-feed-<github-run-id>` с `feed.json`/`manifest.json`;
- fixtures/tests для ID, pagination, authors, narrators, genres, series, duration, chapters/media и unavailable/tombstone semantics;
- cursor/state сохранять только после успешного upload artifact;
- до production cutover провести небольшой canary и ручной audit transport-контракта;
- использовать только штатно доступные данные, без обхода авторизации/DRM/ограничений доступа.

## RuTracker: два TorrServer

Перейти с одного TorrServer на pool из двух серверов для metadata enrichment.

Требования:

- разные `info_hash` обрабатываются параллельно двумя workers;
- один hash не отправляется одновременно на оба сервера;
- deterministic round-robin или least-in-flight scheduling;
- transient timeout/network/429/5xx допускает один failover на второй сервер;
- permanent unsupported-audio остаётся non-blocking;
- structural metadata failure остаётся blocking;
- существующие cursor/replay/confirmation semantics сохраняются;
- в run statistics добавить per-server `attempted/enriched/failed` и общий `failovers`;
- оба набора credentials хранятся только в Actions secrets/variables.

## RuTracker: выбор обложки

Исправить `cover_url`: parser не должен автоматически считать первый `postImg`/`img` обложкой.

Нужно:

- отбрасывать static assets, smiles, badges и декоративные изображения;
- учитывать размеры/aspect ratio, если они доступны;
- предпочитать portrait/book-like кандидаты;
- явно широкую низкую картинку не использовать как cover;
- лучше вернуть пустой cover, чем ложную декоративную полоску;
- добавить fixtures: обычная обложка, несколько картинок, горизонтальный декор перед обложкой, post без cover.

Android `0.5.3.6` отдельно добавит client-side fallback для аномального aspect ratio.

## State/artifact safety

Для Audiopolka, RuTracker и Uknig сохраняем порядок:

```text
tests → crawl → feed/manifest → upload artifact → save state
```

Пустой state означает явный bootstrap с нуля. State не продвигается до успешной публикации artifact.

## Документация и версия

Перед release:

- bump `0.1.3 → 0.1.4`;
- обновить `README.md` и `CHANGELOG.md`;
- описать Uknig workflow/state/artifact;
- описать dual-TorrServer config и статистику;
- вся актуальная документация остаётся на русском языке.

## Release gate `0.1.4`

```text
pytest green
→ Audiopolka regression green
→ RuTracker regression green
→ Uknig canary green
→ dual TorrServer canary использует оба сервера
→ cover fixture corpus green
→ artifact публикуется до state commit
→ README/CHANGELOG синхронизированы
```

После этого Backend `0.8.3.8.3` может включать Uknig feed contract и проводить production cutover.
