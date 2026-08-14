import pytest

from abred_catalog_pipeline.audiopolka import crawler as audiopolka_crawler
from abred_catalog_pipeline.cursor import CrawlCursor
from abred_catalog_pipeline.models import ParsedBook, ParsedChapter


class _FakeParser:
    code = "audiopolka"
    base_url = "https://audiopolka.club"

    def __init__(self, detail: ParsedBook):
        self.detail = detail

    async def _get(self, url: str) -> str:
        return "<html></html>"

    async def get_book(self, external_url: str) -> ParsedBook:
        return self.detail


def _catalog_book() -> ParsedBook:
    return ParsedBook(
        external_id="9180853",
        external_url="https://audiopolka.club/9180853/",
        title="Вспышка",
        authors=["Харилал Пападжи"],
        narrators=["Nikosho"],
        duration_seconds=7800,
    )


@pytest.mark.asyncio
async def test_empty_chapters_are_rejected_before_feed_serialization(monkeypatch):
    catalog = _catalog_book()
    detail = _catalog_book()
    detail.chapters = []

    monkeypatch.setattr(audiopolka_crawler, "detect_last_page", lambda *_: 1)
    monkeypatch.setattr(audiopolka_crawler, "parse_catalog_html", lambda *_: [catalog])

    result, _ = await audiopolka_crawler.crawl_once(_FakeParser(detail), CrawlCursor())

    assert result["records"] == []
    assert result["tombstones"] == []
    assert result["rejected"] == [
        {
            "source": "audiopolka",
            "external_id": "9180853",
            "external_url": "https://audiopolka.club/9180853/",
            "reason": "audiopolka_missing_full_chapters",
        }
    ]


@pytest.mark.asyncio
async def test_book_with_full_chapter_remains_playable_record(monkeypatch):
    catalog = _catalog_book()
    detail = _catalog_book()
    detail.chapters = [
        ParsedChapter(
            external_id="1",
            position=0,
            title="Часть 1",
            duration_seconds=60,
            media_url="https://cdn.example/full.mp3",
        )
    ]

    monkeypatch.setattr(audiopolka_crawler, "detect_last_page", lambda *_: 1)
    monkeypatch.setattr(audiopolka_crawler, "parse_catalog_html", lambda *_: [catalog])

    result, _ = await audiopolka_crawler.crawl_once(_FakeParser(detail), CrawlCursor())

    assert len(result["records"]) == 1
    assert result["records"][0]["external_id"] == "9180853"
    assert result["records"][0]["chapters"][0]["media_url"] == "https://cdn.example/full.mp3"
    assert result["rejected"] == []
