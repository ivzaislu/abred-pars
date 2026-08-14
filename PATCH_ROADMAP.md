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

### Уже готово в `main`

Первый Uknig-компонент смёржен через PR #14:

- parser и crawler для `https://uknig.com/`;
- stable source ID из `/books/<id>`;
- pagination `/?p=<N>`;
- metadata: title, description, cover, authors, narrators, genres, series/position, duration;
- full playlist `/index.php/books/<id>/playlist.txt` → chapters/media;
- state `state/uknig.json`;
- ручной CLI `python -m abred_catalog_pipeline.uknig`;
- unit/regression tests;
- read-only live canary без persistent state и без schedule.

Жёсткие фильтры:

- `Прослушивание заблокировано правообладателем` → `rights_holder_blocked`, playable record не создаётся;
- страница только с ознакомительным фрагментом без `Полная версия аудиокниги` → `preview_only`, playable record не создаётся;
- пустой/недоступный/невалидный full playlist → `preview_only`;
- playlist вида `stream URL or download URL` нормализуется до одного HTTPS media URL.

Live canary `31792720994` прошёл: текущая page 1 дала 24 playable records / 670 chapters, feed contract audit green; известная rights-holder-blocked книга была корректно отклонена.

### Осталось до production producer

- добавить production workflow/artifact `uknig-feed-<github-run-id>` с `feed.json`/`manifest.json`;
- сохранять cursor/state только после успешного upload artifact;
- выбрать безопасный bootstrap rate для каталога примерно в несколько тысяч страниц;
- scheduled запуск включать только после готовности Backend `0.8.3.8.3` к `source=uknig`;
- перед cutover повторить небольшой canary и ручной audit transport-контракта;
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
