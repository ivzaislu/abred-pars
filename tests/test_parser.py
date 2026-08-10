from pathlib import Path

import pytest

from abred_catalog_pipeline.audiopolka.parser import (
    AudiopolkaParser,
    _infer_series_position,
    parse_book_html,
    parse_catalog_html,
    parse_series_html,
)
from abred_catalog_pipeline.models import PreviewOnlyBookError, UnavailableBookError

FIX = Path(__file__).parent / "fixtures"


def test_catalog_parser():
    html = (FIX / "audiopolka_catalog.html").read_text()
    books = parse_catalog_html(html, "https://audiopolka.club/", "https://audiopolka.club")
    assert len(books) == 1
    assert books[0].external_id == "7556342"
    assert books[0].title == "Тестовая книга"
    assert books[0].duration_seconds == 3723
    assert books[0].authors == ["Иван Автор"]
    assert books[0].series_name == "Тестовый цикл"
    assert books[0].series_external_id == "99"


def test_book_and_chapters_parser():
    html = (FIX / "audiopolka_book.html").read_text()
    book = parse_book_html(html, "https://audiopolka.club/7556342/", "https://audiopolka.club")
    assert book.title == "Тестовая книга"
    assert book.description == "Тестовое описание книги"
    assert len(book.chapters) == 2
    assert book.chapters[0].media_url == "https://cdn.example/01.mp3"
    assert book.duration_seconds == 186


def test_catalog_parser_scopes_metadata():
    html = (FIX / "audiopolka_scoped_catalog.html").read_text()
    books = parse_catalog_html(html, "https://audiopolka.club/", "https://audiopolka.club")
    assert [b.external_id for b in books] == ["111", "222"]
    assert books[0].authors == ["Автор один"]
    assert books[1].narrators == ["Диктор два"]


def test_book_parser_scopes_metadata_to_current_book():
    html = (FIX / "audiopolka_scoped_book.html").read_text()
    book = parse_book_html(html, "https://audiopolka.club/12345/", "https://audiopolka.club")
    assert book.title == "Правильная книга"
    assert book.authors == ["Нужный Автор"]
    assert book.narrators == ["Нужный Диктор"]
    assert book.genres == ["Нужный Жанр"]
    assert book.cover_url == "https://cdn.example/right.jpg"


def test_flat_catalog_does_not_merge_neighbor_cards():
    html = (FIX / "audiopolka_flat_catalog.html").read_text()
    books = parse_catalog_html(html, "https://audiopolka.club/", "https://audiopolka.club")
    assert [b.title for b in books] == ["Первая книга", "Вторая книга"]
    assert books[0].authors == ["Автор первый"]
    assert books[1].authors == ["Автор второй"]


def test_series_position_patterns():
    assert _infer_series_position("Как достать архимага. Книга 2", "Архимаг желает отдохнуть") == 2
    assert _infer_series_position("Лекарь Фамильяров. Том 7", "Лекарь Фамильяров") == 7
    assert _infer_series_position("Жрец Хаоса. Книга ХIII", "Зов Пустоты") == 13
    assert _infer_series_position("Сын помещика 8", "Сын помещика") == 8
    assert _infer_series_position("1999", "Другая серия") is None


def test_series_page_order_is_source_authoritative():
    html = (FIX / "audiopolka_series.html").read_text()
    series = parse_series_html(html, "https://audiopolka.club/series/99/", "https://audiopolka.club")
    assert [(x.position, x.external_id) for x in series.entries] == [(1, "101"), (2, "305"), (3, "202")]


def test_preview_only_is_rejected():
    html = (FIX / "audiopolka_preview_only.html").read_text()
    with pytest.raises(PreviewOnlyBookError):
        parse_book_html(html, "https://audiopolka.club/2898160/", "https://audiopolka.club")


def test_rightsholder_removed_is_unavailable():
    html = (FIX / "audiopolka_rightsholder_removed.html").read_text()
    with pytest.raises(UnavailableBookError):
        parse_book_html(html, "https://audiopolka.club/123456/", "https://audiopolka.club")


@pytest.mark.asyncio
async def test_path_pagination(monkeypatch):
    parser = AudiopolkaParser(delay_seconds=0)
    seen = []

    async def fake_get(url):
        seen.append(url)
        return "<html></html>"

    monkeypatch.setattr(parser, "_get", fake_get)
    await parser.get_catalog(1)
    await parser.get_catalog(2)
    await parser.get_catalog(7)
    await parser.aclose()
    assert seen == [
        "https://audiopolka.club/",
        "https://audiopolka.club/p2/",
        "https://audiopolka.club/p7/",
    ]
