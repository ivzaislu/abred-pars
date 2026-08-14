from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ..models import AudiobookSource, ParsedBook, ParsedChapter, PreviewOnlyBookError, UnavailableBookError

_BOOK_RE = re.compile(r"(?:^|/)books/(\d+)(?:/|$)")
_MEDIA_URL_RE = re.compile(r"https?://[^\s]+", re.I)
_RIGHTS_BLOCKED = "прослушивание заблокировано правообладателем"
_PREVIEW_MARKERS = ("ознакомительный фрагмент", "фрагмент аудиокниги")


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _book_id(value: str) -> str:
    match = _BOOK_RE.search(urlparse(value).path)
    return match.group(1) if match else ""


def _media_url(value: object) -> str:
    """Return the primary playable URL from Uknig's playlist field.

    Some playlist rows expose `stream URL or download URL`. The feed contract
    accepts one URL only, so prefer the first valid HTTP(S) candidate and keep
    the `?d=1` alternative out of playback metadata.
    """
    raw = str(value or "").strip()
    for candidate in _MEDIA_URL_RE.findall(raw):
        candidate = candidate.rstrip(".,;)")
        parsed = urlparse(candidate)
        if parsed.scheme.casefold() in {"http", "https"} and parsed.netloc:
            return candidate
    return ""


def _duration_seconds(value: str) -> int:
    text = _clean(value).casefold()
    if not text:
        return 0
    hours = minutes = seconds = 0
    match = re.search(r"(\d+)\s*(?:час|часа|часов|ч\.)", text)
    if match:
        hours = int(match.group(1))
    match = re.search(r"(\d+)\s*(?:минута|минуты|минут|мин\.)", text)
    if match:
        minutes = int(match.group(1))
    match = re.search(r"(\d+)\s*(?:секунда|секунды|секунд|сек\.)", text)
    if match:
        seconds = int(match.group(1))
    return hours * 3600 + minutes * 60 + seconds


def _links_by_prefix(soup: BeautifulSoup, prefixes: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        path = urlparse(anchor.get("href") or "").path
        if not any(path.startswith(prefix) or path.startswith("/index.php" + prefix) for prefix in prefixes):
            continue
        name = _clean(anchor.get_text(" ", strip=True))
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def _section_text(soup: BeautifulSoup, start_label: str, end_label: str) -> str:
    collecting = False
    parts: list[str] = []
    for raw in soup.stripped_strings:
        text = _clean(str(raw))
        if not collecting:
            if text.casefold() == start_label.casefold():
                collecting = True
            continue
        if text.casefold() == end_label.casefold():
            break
        parts.append(text)
    return _clean(" ".join(parts))


def _series(soup: BeautifulSoup) -> tuple[str, str, int | None]:
    for anchor in soup.select("a[href]"):
        path = urlparse(anchor.get("href") or "").path
        match = re.search(r"/(?:index\.php/)?series/(\d+)(?:/|$)", path)
        if not match:
            continue
        name = _clean(anchor.get_text(" ", strip=True))
        parent_text = _clean(anchor.parent.get_text(" ", strip=True)) if anchor.parent else name
        pos_match = re.search(r"\(#\s*(\d+)\)", parent_text)
        return match.group(1), name, int(pos_match.group(1)) if pos_match else None
    return "", "", None


def parse_catalog_html(html: str, page_url: str, base_url: str) -> list[ParsedBook]:
    soup = BeautifulSoup(html, "html.parser")
    books: list[ParsedBook] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = anchor.get("href") or ""
        absolute = urljoin(page_url, href)
        external_id = _book_id(absolute)
        if not external_id or external_id in seen:
            continue
        title = _clean(anchor.get_text(" ", strip=True))
        if not title or title.casefold() == "слушать аудиокнигу":
            continue
        seen.add(external_id)
        books.append(
            ParsedBook(
                external_id=external_id,
                external_url=urljoin(base_url.rstrip("/") + "/", f"books/{external_id}"),
                title=title,
                metadata_complete=False,
            )
        )
    return books


def parse_book_html(html: str, book_url: str, base_url: str) -> ParsedBook:
    soup = BeautifulSoup(html, "html.parser")
    page_text = _clean(soup.get_text(" ", strip=True))
    page_fold = page_text.casefold()
    if _RIGHTS_BLOCKED in page_fold:
        raise UnavailableBookError(book_url, reason="rights_holder_blocked")
    if any(marker in page_fold for marker in _PREVIEW_MARKERS):
        raise PreviewOnlyBookError(book_url, reason="preview_only")

    external_id = _book_id(book_url)
    if not external_id:
        raise ValueError(f"cannot determine Uknig book id: {book_url}")

    title_node = soup.select_one("h1")
    title = _clean(title_node.get_text(" ", strip=True)) if title_node else ""
    if not title:
        meta_title = soup.select_one('meta[property="og:title"][content]')
        title = _clean(meta_title.get("content") or "") if meta_title else ""
    if not title:
        raise ValueError(f"missing Uknig title: {book_url}")

    cover = ""
    meta_image = soup.select_one('meta[property="og:image"][content]')
    if meta_image:
        cover = urljoin(base_url.rstrip("/") + "/", meta_image.get("content") or "")

    authors = _links_by_prefix(soup, ("/authors/",))
    narrators = _links_by_prefix(soup, ("/readers/",))
    genres = _links_by_prefix(soup, ("/genres/",))
    series_external_id, series_name, series_position = _series(soup)

    duration = 0
    for text in soup.stripped_strings:
        candidate = _clean(str(text))
        if re.search(r"\b(?:час|часа|часов|минута|минуты|минут|секунда|секунды|секунд)\b", candidate.casefold()):
            parsed = _duration_seconds(candidate)
            if parsed > duration:
                duration = parsed

    description = _section_text(soup, "Описание книги", "Подробная информация")
    rating = ""
    for node in soup.select('[itemprop="ratingValue"], meta[itemprop="ratingValue"]'):
        value = node.get("content") if getattr(node, "get", None) else None
        value = value or node.get_text(" ", strip=True)
        if re.fullmatch(r"\d+(?:[.,]\d+)?", _clean(value)):
            rating = _clean(value).replace(",", ".")
            break

    present = {"title"}
    if description:
        present.add("description")
    if cover:
        present.add("cover_url")
    if duration:
        present.add("duration_seconds")
    if authors:
        present.add("authors")
    if narrators:
        present.add("narrators")
    if genres:
        present.add("genres")
    if series_name:
        present.add("series")

    return ParsedBook(
        external_id=external_id,
        external_url=urljoin(base_url.rstrip("/") + "/", f"books/{external_id}"),
        title=title,
        description=description,
        cover_url=cover,
        duration_seconds=duration,
        rating=rating,
        authors=authors,
        narrators=narrators,
        genres=genres,
        metadata_fields_present=present,
        metadata_complete=bool(authors or narrators or genres),
        series_name=series_name,
        series_external_id=series_external_id,
        series_position=series_position,
    )


def parse_playlist_json(payload: object, book_url: str) -> list[ParsedChapter]:
    if not isinstance(payload, list) or not payload:
        raise PreviewOnlyBookError(book_url, reason="preview_only")
    chapters: list[ParsedChapter] = []
    for position, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            continue
        media_url = _media_url(item.get("file"))
        external_id = _clean(str(item.get("id") or ""))
        if not media_url or not external_id:
            continue
        title = _clean(str(item.get("title") or "")) or f"Глава {position}"
        chapters.append(
            ParsedChapter(
                external_id=external_id,
                position=position,
                title=title,
                media_url=media_url,
            )
        )
    if not chapters:
        raise PreviewOnlyBookError(book_url, reason="preview_only")
    return chapters


class UknigParser(AudiobookSource):
    code = "uknig"
    name = "уКниг"

    def __init__(self, *, base_url: str = "https://uknig.com", delay_seconds: float = 0.25):
        self.base_url = base_url.rstrip("/")
        self.delay_seconds = max(0.0, float(delay_seconds))
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=30.0,
            headers={
                "User-Agent": "AbredCatalogPipeline/0.1 (+catalog metadata import)",
                "Accept-Language": "ru,en;q=0.5",
            },
        )

    async def _get(self, url: str) -> str:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        response = await self._client.get(url)
        response.raise_for_status()
        return response.text

    async def _get_playlist(self, book_id: str, book_url: str) -> object:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        url = f"{self.base_url}/index.php/books/{book_id}/playlist.txt"
        response = await self._client.get(url, headers={"Referer": book_url, "Accept": "application/json,text/plain,*/*"})
        if response.status_code in {401, 403, 404, 410}:
            raise PreviewOnlyBookError(book_url, reason="preview_only")
        response.raise_for_status()
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise PreviewOnlyBookError(book_url, reason="preview_only") from exc

    async def get_catalog(self, page: int = 1) -> list[ParsedBook]:
        page = max(1, int(page))
        url = self.base_url + "/" if page == 1 else f"{self.base_url}/?p={page}"
        return parse_catalog_html(await self._get(url), url, self.base_url)

    async def get_book(self, external_url: str) -> ParsedBook:
        book_url = urljoin(self.base_url + "/", external_url)
        detail = parse_book_html(await self._get(book_url), book_url, self.base_url)
        payload = await self._get_playlist(detail.external_id, detail.external_url)
        detail.chapters = parse_playlist_json(payload, detail.external_url)
        detail.metadata_fields_present.add("chapters")
        detail.metadata_complete = True
        return detail

    async def aclose(self) -> None:
        await self._client.aclose()
