from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any


class PreviewOnlyBookError(RuntimeError):
    def __init__(self, external_url: str, reason: str = "preview_only"):
        super().__init__(f"{reason}: {external_url}")
        self.external_url = external_url
        self.reason = reason


class UnavailableBookError(RuntimeError):
    def __init__(self, external_url: str, reason: str = "source_unavailable"):
        super().__init__(f"{reason}: {external_url}")
        self.external_url = external_url
        self.reason = reason


@dataclass(slots=True)
class ParsedChapter:
    external_id: str
    position: int
    title: str
    duration_seconds: int = 0
    media_url: str = ""


@dataclass(slots=True)
class ParsedSeriesEntry:
    external_id: str
    external_url: str
    title: str
    position: int
    authors: list[str] = field(default_factory=list)
    narrators: list[str] = field(default_factory=list)
    cover_url: str = ""
    duration_seconds: int = 0


@dataclass(slots=True)
class ParsedSeries:
    external_id: str
    name: str
    entries: list[ParsedSeriesEntry] = field(default_factory=list)


@dataclass(slots=True)
class ParsedBook:
    external_id: str
    external_url: str
    title: str
    description: str = ""
    cover_url: str = ""
    duration_seconds: int = 0
    rating: str = ""
    authors: list[str] = field(default_factory=list)
    narrators: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    metadata_fields_present: set[str] = field(default_factory=set)
    metadata_complete: bool = True
    series_name: str = ""
    series_external_id: str = ""
    series_position: int | None = None
    series_entries: list[ParsedSeriesEntry] = field(default_factory=list)
    chapters: list[ParsedChapter] = field(default_factory=list)


class AudiobookSource(ABC):
    code: str
    name: str
    base_url: str

    @abstractmethod
    async def get_catalog(self, page: int = 1) -> list[ParsedBook]:
        raise NotImplementedError

    @abstractmethod
    async def get_book(self, external_url: str) -> ParsedBook:
        raise NotImplementedError

    async def get_series(self, external_id: str) -> ParsedSeries:
        raise NotImplementedError

    def is_preview_media_url(self, media_url: str) -> bool:
        return False

    async def aclose(self) -> None:
        return None


def book_to_feed_record(book: ParsedBook, *, source: str) -> dict[str, Any]:
    series: list[dict[str, Any]] = []
    if book.series_name or book.series_external_id:
        series.append(
            {
                "external_id": book.series_external_id or None,
                "name": book.series_name,
                "position": book.series_position,
            }
        )
    return {
        "source": source,
        "external_id": book.external_id,
        "external_url": book.external_url,
        "title": book.title,
        "description": book.description,
        "cover_url": book.cover_url,
        "duration_seconds": int(book.duration_seconds or 0),
        "rating": book.rating,
        "authors": list(book.authors),
        "narrators": list(book.narrators),
        "genres": list(book.genres),
        "series": series,
        "chapters": [asdict(chapter) for chapter in book.chapters],
    }
