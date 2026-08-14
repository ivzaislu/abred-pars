import pytest

from abred_catalog_pipeline.cursor import CrawlCursor
from abred_catalog_pipeline.models import PreviewOnlyBookError, UnavailableBookError
from abred_catalog_pipeline.uknig.crawler import crawl_once, detect_last_page
from abred_catalog_pipeline.uknig.parser import (
    UknigParser,
    parse_book_html,
    parse_catalog_html,
    parse_playlist_json,
)


CATALOG_HTML = """
<html><body>
<a href="/books/815724">Больше, чем ничего</a>
<a href="/books/815724">Слушать аудиокнигу</a>
<a href="/index.php/books/703125">Наполеон</a>
<a href="/?p=2">2</a><a href="/?p=37">37</a>
</body></html>
"""

DETAIL_HTML = """
<html><head>
<meta property="og:image" content="/covers/815724.jpg">
</head><body>
<h1>Больше, чем ничего</h1>
<div>Описание книги</div><p>Тестовое описание.</p>
<div>Подробная информация</div>
<div>Автор: <a href="/authors/ekaterina-yudina">Екатерина Юдина</a></div>
<div><a href="/genres/lyubovnoe-fentezi">Любовное фэнтези</a> / <a href="/genres/gorodskoe-fentezi">Городское фэнтези</a></div>
<div>Читает <a href="/readers/marina-vysotskaya">Марина Высоцкая</a></div>
<div>Входит в серию <a href="/series/9006">Ничего</a> (#2)</div>
<div>13 часов 12 минут</div>
</body></html>
"""

BLOCKED_HTML = """
<html><body><h1>Закрытая книга</h1>
<div>Прослушивание заблокировано правообладателем</div>
</body></html>
"""

PLAYLIST = [
    {"title": "Глава 1", "file": "https://uknig.com/index.php/files/10?h=a", "id": "10"},
    {"title": "Глава 2", "file": "https://uknig.com/index.php/files/11?h=b", "id": "11"},
]


def test_catalog_extracts_unique_books_and_pages():
    rows = parse_catalog_html(CATALOG_HTML, "https://uknig.com/", "https://uknig.com")
    assert [(row.external_id, row.title) for row in rows] == [
        ("815724", "Больше, чем ничего"),
        ("703125", "Наполеон"),
    ]
    assert detect_last_page(CATALOG_HTML, "https://uknig.com") == 37


def test_detail_metadata_parser():
    book = parse_book_html(DETAIL_HTML, "https://uknig.com/books/815724", "https://uknig.com")
    assert book.external_id == "815724"
    assert book.title == "Больше, чем ничего"
    assert book.description == "Тестовое описание."
    assert book.cover_url == "https://uknig.com/covers/815724.jpg"
    assert book.authors == ["Екатерина Юдина"]
    assert book.narrators == ["Марина Высоцкая"]
    assert book.genres == ["Любовное фэнтези", "Городское фэнтези"]
    assert book.series_external_id == "9006"
    assert book.series_name == "Ничего"
    assert book.series_position == 2
    assert book.duration_seconds == 13 * 3600 + 12 * 60


def test_rights_holder_blocked_is_unavailable():
    with pytest.raises(UnavailableBookError) as error:
        parse_book_html(BLOCKED_HTML, "https://uknig.com/books/804464", "https://uknig.com")
    assert error.value.reason == "rights_holder_blocked"


def test_full_playlist_becomes_chapters():
    chapters = parse_playlist_json(PLAYLIST, "https://uknig.com/books/815724")
    assert [chapter.external_id for chapter in chapters] == ["10", "11"]
    assert chapters[0].position == 1
    assert chapters[0].media_url.endswith("/files/10?h=a")


def test_missing_full_playlist_is_preview_only():
    with pytest.raises(PreviewOnlyBookError) as error:
        parse_playlist_json([], "https://uknig.com/books/724452")
    assert error.value.reason == "preview_only"


@pytest.mark.asyncio
async def test_get_book_requires_full_playlist(monkeypatch):
    parser = UknigParser(delay_seconds=0)

    async def fake_get(url):
        return DETAIL_HTML

    async def fake_playlist(book_id, book_url):
        return []

    monkeypatch.setattr(parser, "_get", fake_get)
    monkeypatch.setattr(parser, "_get_playlist", fake_playlist)
    try:
        with pytest.raises(PreviewOnlyBookError):
            await parser.get_book("https://uknig.com/books/815724")
    finally:
        await parser.aclose()


@pytest.mark.asyncio
async def test_crawler_never_publishes_blocked_or_preview_only():
    class FakeParser:
        code = "uknig"
        base_url = "https://uknig.com"

        async def _get(self, url):
            return CATALOG_HTML

        async def get_book(self, url):
            if url.endswith("815724"):
                raise PreviewOnlyBookError(url, reason="preview_only")
            raise UnavailableBookError(url, reason="rights_holder_blocked")

    result, _ = await crawl_once(FakeParser(), CrawlCursor(source="uknig"), backfill_pages=1)
    assert result["records"] == []
    assert {row["reason"] for row in result["rejected"]} == {"preview_only"}
    assert {row["reason"] for row in result["tombstones"]} == {"rights_holder_blocked"}
