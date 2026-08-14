# Дорожная карта патчей Abred Catalog Pipeline

`ivzaislu/abred-pars` — единственное место для массовых catalog crawler/parser/feed producer AudioBookRed. Backend `ivzaislu/abred` только валидирует и импортирует готовые artifacts.

## `0.1.4` — готово в `main`

Общий release train:

```text
abred-pars 0.1.4 — готово
→ Backend 0.8.3.8.3
→ Uknig production cutover
→ Android 0.5.3.6 / code 59
```

Merge release PR #15: `136d13735decfed4a70c9de0f4eda15e6a71df0f`.

## Uknig

Готово:

- parser/crawler для `https://uknig.com/`;
- stable source ID из `/books/<id>`;
- pagination `/?p=<N>`;
- title, description, cover, authors, narrators, genres, series/position, duration;
- full playlist `/index.php/books/<id>/playlist.txt` → chapters/media;
- `state/uknig.json`;
- ручной CLI;
- unit/regression tests;
- read-only live canary;
- production artifact workflow `.github/workflows/uknig.yml` с порядком `tests → crawl → audit → artifact upload → state commit`.

Жёсткие фильтры:

- `Прослушивание заблокировано правообладателем` → `rights_holder_blocked`, playable record не создаётся;
- только ознакомительный фрагмент без полной версии → `preview_only`;
- пустой/недоступный/невалидный full playlist → `preview_only`;
- playlist вида `stream URL or download URL` нормализуется до одного HTTPS media URL.

Uknig production workflow пока manual-only. Cron включается только после production Backend `0.8.3.8.3` с поддержкой `source=uknig` и успешного cutover canary.

## RuTracker: два TorrServer

Готов pool из двух серверов:

```text
TORRSERVER_URL
TORRSERVER_URL_2
TORRSERVER_USERNAME
TORRSERVER_PASSWORD
```

`TORRSERVER_USERNAME` / `TORRSERVER_PASSWORD` общие для обоих TorrServer.

Поведение:

- до двух параллельных metadata jobs;
- RuTracker/Worker HTML-запросы остаются последовательными;
- least-in-flight scheduling распределяет разные `info_hash` между серверами;
- один hash не отправляется одновременно на оба сервера;
- timeout/network/HTTP `429`/`5xx` допускают один последовательный failover;
- structural metadata failure остаётся blocking;
- permanent unsupported-audio остаётся non-blocking;
- cursor/replay/confirmation semantics сохранены;
- run statistics содержат per-server `attempted/enriched/failed/in_flight` и общий `failovers`.

Live canary `31803496339`:

```text
records: 4
server #1: attempted 2 / enriched 2 / failed 0
server #2: attempted 2 / enriched 2 / failed 0
failovers: 0
```

## RuTracker: качество данных

### Обложки

Готов безопасный cover selector:

- static assets, smiles, badges и маленькие изображения отбрасываются;
- явно широкие декоративные изображения не становятся cover;
- при известных размерах предпочитается portrait/book-like aspect ratio;
- если безопасного кандидата нет, `cover_url` остаётся пустым;
- fixture corpus покрывает обычную обложку, несколько картинок, широкий декор перед обложкой и post без cover.

Android `0.5.3.6` дополнительно имеет client-side fallback для аномального aspect ratio.

### `series_name`

Исправлен production incident 2026-08-14: malformed RuTracker markup мог вложить следующие metadata-поля внутрь значения `Цикл/серия`, например:

```text
Перья Номер книги: 2 Жанр: ... Издательство: ... Описание: ...
```

Это приводило Backend к `StringDataRightTruncation` на `VARCHAR(512)` и блокировало automatic intake. Теперь parser заканчивает series field на следующем известном metadata-label и получает `series_name="Перья"`. Если значение остаётся аномально длинным и надёжно разделить его нельзя, серия отбрасывается вместо отправки структурно невалидной строки. Добавлен regression fixture на повреждённую вложенную разметку.

## State/artifact safety

Для Audiopolka, RuTracker и Uknig сохраняется порядок:

```text
tests → crawl → feed/manifest → upload artifact → save state
```

State не продвигается до успешной публикации artifact.

## Release gate `0.1.4`

Выполнено:

```text
pytest green
→ Audiopolka regression green
→ RuTracker regression green
→ Uknig canary green
→ dual TorrServer live canary использует оба сервера
→ cover fixture corpus green
→ malformed series_name regression green
→ artifact публикуется до state commit
→ README/CHANGELOG синхронизированы
```

Следующий этап общего release train — Backend `0.8.3.8.3`, затем Uknig production cutover.
