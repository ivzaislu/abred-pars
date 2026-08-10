from __future__ import annotations

import asyncio
import html as html_lib
import json
import re
import unicodedata
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag

from ..models import AudiobookSource, ParsedBook, ParsedChapter, ParsedSeries, ParsedSeriesEntry, PreviewOnlyBookError, UnavailableBookError


_DURATION_HMS = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})$")
_BOOK_ID = re.compile(r"/(\d+)/?$")
_SERIES_ID = re.compile(r"/(?:series|cycle)/(\d+)/?$")
_BAD_NAME = re.compile(r"^\d+$")


def _clean(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "")
    # Remove invisible format characters such as BOM / zero-width spaces that
    # occasionally appear inside Audiopolka person names.
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Cf")
    return re.sub(r"\s+", " ", value).strip()


def _absolute(base: str, value: str) -> str:
    if not value:
        return ""
    value = value.replace("\\/", "/")
    if value.startswith("//"):
        return "https:" + value
    return urljoin(base, value)


def _external_id(url: str) -> str:
    path = urlparse(url).path
    match = _BOOK_ID.search(path)
    return match.group(1) if match else path.strip("/") or url


def _is_book_url(url: str, base_url: str) -> bool:
    parsed = urlparse(_absolute(base_url, url))
    host = parsed.netloc.lower().split(":", 1)[0]
    return host.endswith("audiopolka.club") and bool(_BOOK_ID.fullmatch(parsed.path))


def _duration_to_seconds(value: str) -> int:
    value = _clean(value).lower()
    if not value:
        return 0
    m = _DURATION_HMS.search(value)
    if m:
        h = int(m.group(1) or 0)
        return h * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    hours = re.search(r"(\d+)\s*(?:ч|час)", value)
    minutes = re.search(r"(\d+)\s*(?:мин|м)\b", value)
    seconds = re.search(r"(\d+)\s*(?:сек|с)\b", value)
    total = 0
    if hours:
        total += int(hours.group(1)) * 3600
    if minutes:
        total += int(minutes.group(1)) * 60
    if seconds:
        total += int(seconds.group(1))
    return total


def _json_unescape(value: str) -> str:
    try:
        return json.loads('"' + value.replace('"', '\\"') + '"')
    except Exception:
        try:
            return bytes(value, "utf-8").decode("unicode_escape")
        except Exception:
            return value


def _good_label(value: str) -> bool:
    value = _clean(value)
    if not value or len(value) > 160 or _BAD_NAME.fullmatch(value):
        return False
    return value.casefold() not in {
        "автор", "авторы", "диктор", "чтец", "исполнитель", "жанр", "цикл",
        "аудиополка", "следующая", "предыдущая", "аудиокнига",
    }


def _dedupe(values: list[str], limit: int | None = None) -> list[str]:
    out: list[str] = []
    for value in values:
        value = _clean(value)
        if _good_label(value) and value not in out:
            out.append(value)
            if limit is not None and len(out) >= limit:
                break
    return out


def _book_anchor_candidates(soup: BeautifulSoup, base_url: str, *, exact_url: str | None = None) -> list[Tag]:
    exact_path = urlparse(exact_url).path.rstrip("/") if exact_url else None
    result: list[Tag] = []
    for link in soup.select("a[href]"):
        href = link.get("href", "")
        if not _is_book_url(href, base_url):
            continue
        full = _absolute(base_url, href)
        if exact_path is not None and urlparse(full).path.rstrip("/") != exact_path:
            continue
        text = _clean(link.get_text(" ", strip=True))
        # Covers and pagination often point to numeric paths too. We only use textual book links.
        if not text or _BAD_NAME.fullmatch(text) or len(text) > 300 or text.casefold() == "аудиокнига":
            continue
        result.append(link)
    return result


def _next_book_anchor(anchor: Tag, book_anchors: list[Tag]) -> Tag | None:
    try:
        idx = book_anchors.index(anchor)
    except ValueError:
        return None
    return book_anchors[idx + 1] if idx + 1 < len(book_anchors) else None


def _segment_after_anchor(anchor: Tag, stop_anchor: Tag | None, base_url: str) -> tuple[list[Tag], str]:
    """Return links/text following one catalog title until the next catalog title.

    Audiopolka's catalog is logically flat. Relying on a shared ancestor is unsafe because
    several cards can live under the same wrapper. Document-order boundaries keep metadata
    attached to the title that precedes it regardless of wrapper classes.
    """
    links: list[Tag] = []
    text_parts: list[str] = []
    started = False
    for node in anchor.next_elements:
        if node is anchor:
            started = True
            continue
        if stop_anchor is not None and node is stop_anchor:
            break
        if isinstance(node, Tag):
            if node.name == "a" and node.get("href"):
                # If we encounter an unexpected textual book link, stop defensively.
                if node is not anchor and _is_book_url(node.get("href", ""), base_url):
                    txt = _clean(node.get_text(" ", strip=True))
                    if txt and not _BAD_NAME.fullmatch(txt):
                        break
                links.append(node)
        elif isinstance(node, NavigableString):
            txt = _clean(str(node))
            if txt:
                text_parts.append(txt)
    return links, _clean(" ".join(text_parts))


def _links_of_type(links: list[Tag], kind: str, limit: int) -> list[str]:
    if kind == "author":
        matches = [a for a in links if "/author/" in (a.get("href") or "")]
    elif kind == "narrator":
        matches = [a for a in links if any(x in (a.get("href") or "") for x in ("/voice/", "/reader/"))]
    elif kind == "genre":
        matches = [a for a in links if "/genre/" in (a.get("href") or "")]
    elif kind == "series":
        matches = [a for a in links if any(x in (a.get("href") or "") for x in ("/cycle/", "/series/"))]
    else:
        matches = []
    return _dedupe([a.get_text(" ", strip=True) for a in matches], limit=limit)




def _series_ref(links: list[Tag], base_url: str) -> tuple[str, str, str]:
    for link in links:
        href = link.get("href") or ""
        match = _SERIES_ID.search(urlparse(_absolute(base_url, href)).path)
        if not match:
            continue
        name = _clean(link.get_text(" ", strip=True))
        if not _good_label(name):
            continue
        return name, match.group(1), _absolute(base_url, href)
    return "", "", ""

def _roman_to_int(value: str) -> int | None:
    raw = (value or "").strip().upper().replace("Х", "X").replace("В", "V")
    if not raw or not re.fullmatch(r"[IVXLCDM]+", raw):
        return None
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    prev = 0
    for ch in reversed(raw):
        current = values[ch]
        total += -current if current < prev else current
        prev = max(prev, current)
    return total if 0 < total <= 999 else None


def _infer_series_position(title: str, series_name: str) -> int | None:
    if not (title or "").strip() or not (series_name or "").strip():
        return None
    raw = _clean(title)
    # Explicit volume markers are the safest signal. This also handles mixed
    # Cyrillic/Latin roman numerals such as “Книга ХIII”.
    marker = re.search(
        r"(?i)(?:книга|том|часть|серия)\s*(?:№|#)?\s*[-–—.:]?\s*([0-9]{1,3}|[IVXLCDMХВ]{1,8})(?=\b|\s|[.:,;!?)]|$)",
        raw,
    )
    if marker:
        token = marker.group(1)
        if token.isdigit():
            value = int(token)
            return value if 0 < value <= 999 else None
        return _roman_to_int(token)

    # Many source titles encode the installment without an explicit marker:
    # “Сын помещика 8”, “Мрак 3”, “КиберМиха 3. Аз есмь царь”,
    # “Покоривший СТЕНУ 10: Чёрная дорога”. Restrict this fallback to a
    # number immediately following the visible series label, or to a trailing
    # number when the series label occurs in the title. This avoids treating
    # years/edition numbers in unrelated titles as cycle positions.
    norm_title = re.sub(r"[^0-9a-zа-яё]+", " ", raw.casefold()).strip()
    norm_series = re.sub(r"[^0-9a-zа-яё]+", " ", series_name.casefold()).strip()
    if norm_series and norm_series in norm_title:
        escaped_series = re.escape(_clean(series_name))
        after_series = re.search(
            rf"(?i)(?:^|\b){escaped_series}\s*[-–—.:]?\s*([0-9]{{1,3}})(?=\s|[.:,;!?)]|$)",
            raw,
        )
        if after_series:
            value = int(after_series.group(1))
            return value if 0 < value <= 999 else None

        trailing = re.search(r"(?:^|\s|[-–—])([0-9]{1,3})\s*$", raw)
        if trailing:
            value = int(trailing.group(1))
            return value if 0 < value <= 999 else None
    return None


def _title_from_page(soup: BeautifulSoup) -> str:
    # Explicit title nodes first.
    for selector in (
        ".book-page-title",
        ".book-page-name",
        "h1[itemprop='name']",
        "h1",
    ):
        element = soup.select_one(selector)
        if element:
            text = _clean(element.get_text(" ", strip=True))
            if _good_label(text):
                return text

    # SEO metadata is generally more stable than surrounding page wrappers.
    for selector, attr in (("meta[property='og:title']", "content"), ("meta[name='twitter:title']", "content")):
        meta = soup.select_one(selector)
        raw = _clean(meta.get(attr, "")) if meta else ""
        if raw:
            raw = re.sub(r"\s*[|—-]\s*Аудиополка.*$", "", raw, flags=re.I)
            raw = re.sub(r"\s*[-—|]?\s*Аудиокнига(?:\s+бесплатно!?|\s+слушать.*|\s+онлайн.*)?$", "", raw, flags=re.I)
            raw = _clean(raw)
            if _good_label(raw):
                return raw

    if soup.title:
        raw = _clean(soup.title.get_text(" ", strip=True))
        raw = re.sub(r"\s*[|—-]\s*Аудиополка.*$", "", raw, flags=re.I)
        raw = re.sub(r"\s*[-—|]?\s*Аудиокнига(?:\s+бесплатно!?|\s+слушать.*|\s+онлайн.*)?$", "", raw, flags=re.I)
        raw = _clean(raw)
        if _good_label(raw):
            return raw
    return "Аудиокнига"


def _cover_from_page(soup: BeautifulSoup, base_url: str) -> str:
    # Only page-level metadata / explicit cover elements. Never scan arbitrary recommendation images.
    meta = soup.select_one("meta[property='og:image']")
    if meta and meta.get("content"):
        return _absolute(base_url, meta.get("content", ""))
    for selector in ("#book-page-cover-image", "img#book-cover-image", ".book-page-cover img", "img[itemprop='image']"):
        img = soup.select_one(selector)
        if img:
            src = img.get("data-src") or img.get("src") or ""
            if src:
                return _absolute(base_url, src)
    return ""


def _is_audiopolka_preview_media_url(media_url: str) -> bool:
    """Recognize third-party trial streams that are not the full audiobook."""
    if not media_url:
        return False
    parsed = urlparse(media_url.replace("\\/", "/"))
    host = parsed.netloc.lower().split(":", 1)[0]
    path = parsed.path.lower()
    return (host == "litres.ru" or host.endswith(".litres.ru")) and "/audiotrial" in path


def _has_full_listen_purchase_cta(soup: BeautifulSoup) -> bool:
    for node in soup.select("#book_buy, #buy_wrap button, .book_buy, .book_buy_wrap button"):
        text = _clean(node.get_text(" ", strip=True)).casefold()
        if "слушать полностью" in text or "купить" in text:
            return True
    return False


def _is_explicitly_unavailable_page(soup: BeautifulSoup) -> bool:
    """Detect Audiopolka pages that explicitly state the audio was removed/unavailable.

    Do not reject generic empty playlists: a parser/source glitch should remain repairable.
    Only source-authored unavailability language is authoritative here.
    """
    candidates = []
    for selector in (
        ".book-page-description",
        ".book-page-annotation",
        ".book-page-meta",
        "#book-page-main-inner",
    ):
        node = soup.select_one(selector)
        if node is not None:
            candidates.append(_clean(node.get_text(" ", strip=True)).casefold())
    if not candidates:
        body = soup.body or soup
        candidates.append(_clean(body.get_text(" ", strip=True)).casefold())

    text = " \n".join(candidates)
    explicit_phrases = (
        "аудиокнига удалена по требованию правообладателя",
        "аудиокнига удалена по требованию правообладателей",
        "аудиозапись удалена по требованию правообладателя",
        "аудиозапись удалена по требованию правообладателей",
        "контент удален по требованию правообладателя",
        "контент удалён по требованию правообладателя",
    )
    if any(phrase in text for phrase in explicit_phrases):
        return True

    # Keep a narrow linguistic fallback for minor wording changes on the source.
    return (
        "правообладател" in text
        and ("аудиокнига" in text or "аудиозапись" in text)
        and ("удален" in text or "удалён" in text or "недоступ" in text)
    )


def _is_preview_only_page(soup: BeautifulSoup, chapters: list[ParsedChapter]) -> bool:
    if not chapters:
        return False

    # Strongest signal seen on Audiopolka preview-only pages: every playable item
    # points to LitRes /audiotrial rather than to a complete source stream.
    if all(_is_audiopolka_preview_media_url(ch.media_url) for ch in chapters):
        return True

    # Defensive fallback for the same page shape if the external trial URL changes:
    # an explicit "Слушать полностью" purchase CTA plus a single zero-duration
    # teaser named "Начало"/"Фрагмент" is not a complete audiobook.
    if _has_full_listen_purchase_cta(soup) and len(chapters) == 1:
        chapter = chapters[0]
        title = _clean(chapter.title).casefold()
        teaser_titles = {"начало", "фрагмент", "ознакомительный фрагмент", "демо", "пробный фрагмент"}
        if title in teaser_titles and int(chapter.duration_seconds or 0) <= 0:
            return True
    return False


def parse_chapters_from_html(raw_html: str, base_url: str) -> list[ParsedChapter]:
    chapters: list[ParsedChapter] = []
    seen_urls: set[str] = set()

    object_re = re.compile(r"\{[^{}]{0,4000}?\"fileId\"\s*:\s*(\d+)[^{}]{0,4000}?\}", re.DOTALL)
    for idx, match in enumerate(object_re.finditer(raw_html), start=1):
        chunk = match.group(0)
        file_id = match.group(1)
        title_m = re.search(r'\"title\"\s*:\s*\"((?:\\.|[^\"\\])*)\"', chunk, re.DOTALL)
        src_m = re.search(r'\"src\"\s*:\s*\"((?:\\.|[^\"\\])*)\"', chunk, re.DOTALL)
        duration_m = re.search(r'\"duration\"\s*:\s*(\d+)', chunk)
        if not src_m:
            continue
        src = _absolute(base_url, _json_unescape(src_m.group(1)).replace("\\/", "/"))
        if not src or src in seen_urls:
            continue
        seen_urls.add(src)
        title = _json_unescape(title_m.group(1)) if title_m else f"Часть {idx}"
        chapters.append(
            ParsedChapter(
                external_id=file_id,
                position=len(chapters),
                title=_clean(html_lib.unescape(title)) or f"Часть {idx}",
                duration_seconds=int(duration_m.group(1)) if duration_m else 0,
                media_url=src,
            )
        )

    if chapters:
        return chapters

    mp3_re = re.compile(r"[\"']((?:https?:)?(?:\\?/\\?/)[^\"'\s]+?\.mp3(?:\?[^\"'\s]*)?)[\"']", re.I)
    for m in mp3_re.finditer(raw_html):
        src = _absolute(base_url, m.group(1).replace("\\/", "/"))
        if not src or src in seen_urls:
            continue
        seen_urls.add(src)
        chapters.append(
            ParsedChapter(
                external_id=str(len(chapters) + 1),
                position=len(chapters),
                title=f"Часть {len(chapters) + 1}",
                media_url=src,
            )
        )
    return chapters


def parse_catalog_html(raw_html: str, page_url: str, base_url: str) -> list[ParsedBook]:
    soup = BeautifulSoup(raw_html, "html.parser")
    anchors = _book_anchor_candidates(soup, base_url)

    # Keep one textual title anchor per external id, preserving document order.
    unique_anchors: list[Tag] = []
    seen_anchor_ids: set[str] = set()
    for anchor in anchors:
        url = _absolute(base_url, anchor.get("href", ""))
        external_id = _external_id(url)
        if external_id in seen_anchor_ids:
            continue
        seen_anchor_ids.add(external_id)
        unique_anchors.append(anchor)

    books: list[ParsedBook] = []
    for idx, anchor in enumerate(unique_anchors):
        url = _absolute(base_url, anchor.get("href", ""))
        external_id = _external_id(url)
        title = _clean(anchor.get_text(" ", strip=True))
        if not _good_label(title):
            continue

        stop = unique_anchors[idx + 1] if idx + 1 < len(unique_anchors) else None
        links, segment_text = _segment_after_anchor(anchor, stop, base_url)
        authors = _links_of_type(links, "author", limit=5)
        narrators = _links_of_type(links, "narrator", limit=8)
        genres = _links_of_type(links, "genre", limit=5)
        series = _links_of_type(links, "series", limit=1)
        series_name, series_external_id, _series_url = _series_ref(links, base_url)
        if not series_name and series:
            series_name = series[0]

        # A real catalog card should have at least one metadata signal. This removes unrelated
        # numeric-path links without assuming any CSS class names.
        duration_seconds = _duration_to_seconds(segment_text)
        if not (authors or narrators or genres or duration_seconds):
            continue

        books.append(
            ParsedBook(
                external_id=external_id,
                external_url=url,
                title=title,
                duration_seconds=duration_seconds,
                authors=authors,
                narrators=narrators,
                genres=genres,
                series_name=series_name,
                series_external_id=series_external_id,
                series_position=_infer_series_position(title, series_name),
            )
        )
    return books


def parse_book_html(raw_html: str, book_url: str, base_url: str) -> ParsedBook:
    soup = BeautifulSoup(raw_html, "html.parser")
    title = _title_from_page(soup)
    cover = _cover_from_page(soup, base_url)

    description = ""
    for selector in (".book-page-description", ".book-description", "[itemprop='description']", "[class*='annotation']"):
        el = soup.select_one(selector)
        text = _clean(el.get_text(" ", strip=True)) if el else ""
        if len(text) > len(description):
            description = text
    if not description:
        meta = soup.select_one("meta[property='og:description']") or soup.select_one("meta[name='description']")
        if meta:
            description = _clean(meta.get("content", ""))

    # Detail metadata is intentionally conservative. Prefer a known book-detail wrapper when
    # available and never traverse recommendation grids for metadata. Catalog metadata remains
    # authoritative during sync.
    metadata_root: Tag = (
        soup.select_one(".book-page-main")
        or soup.select_one(".book-page-meta")
        or soup.select_one("[class*='book-page'][class*='main']")
        or soup
    )
    authors = _dedupe([x.get_text(" ", strip=True) for x in metadata_root.select("a[href*='/author/']")], limit=5)
    narrators = _dedupe(
        [x.get_text(" ", strip=True) for x in metadata_root.select("a[href*='/voice/'],a[href*='/reader/']")],
        limit=8,
    )
    genres = _dedupe([x.get_text(" ", strip=True) for x in metadata_root.select("a[href*='/genre/']")], limit=5)
    series_links = list(metadata_root.select("a[href*='/cycle/'],a[href*='/series/']"))
    series_values = _dedupe([x.get_text(" ", strip=True) for x in series_links], limit=1)
    series_name, series_external_id, _series_url = _series_ref(series_links, base_url)
    if not series_name and series_values:
        series_name = series_values[0]

    if _is_explicitly_unavailable_page(soup):
        raise UnavailableBookError(book_url, reason="audiopolka_rightsholder_removed")

    chapters = parse_chapters_from_html(raw_html, base_url)
    if _is_preview_only_page(soup, chapters):
        raise PreviewOnlyBookError(book_url, reason="audiopolka_preview_only")

    duration_seconds = _duration_to_seconds(_clean(metadata_root.get_text(" ", strip=True)))
    if duration_seconds == 0:
        duration_seconds = sum(c.duration_seconds for c in chapters if c.duration_seconds)

    return ParsedBook(
        external_id=_external_id(book_url),
        external_url=book_url,
        title=title,
        description=description,
        cover_url=cover,
        duration_seconds=duration_seconds,
        authors=authors,
        narrators=narrators,
        genres=genres,
        series_name=series_name,
        series_external_id=series_external_id,
        series_position=_infer_series_position(title, series_name),
        chapters=chapters,
    )


def parse_series_html(raw_html: str, series_url: str, base_url: str) -> ParsedSeries:
    soup = BeautifulSoup(raw_html, "html.parser")
    match = _SERIES_ID.search(urlparse(series_url).path)
    external_id = match.group(1) if match else urlparse(series_url).path.strip("/")

    name = ""
    title_link = soup.select_one(".page-title-name a[href*='/series/'], .page-title-name a[href*='/cycle/']")
    if title_link:
        name = _clean(title_link.get_text(" ", strip=True))
    if not name:
        title_node = soup.select_one(".page-title-name")
        name = _clean(title_node.get_text(" ", strip=True)) if title_node else ""
    if not name and soup.title:
        raw_title = _clean(soup.title.get_text(" ", strip=True))
        name = re.sub(r"(?i)^Серия книг\s+", "", raw_title)
        name = re.sub(r"(?i)\s*[-—|]\s*слушать.*$", "", name).strip()
    name = name or f"Серия {external_id}"

    # Audiopolka's series page already lists books in canonical source order.
    # Preserve DOM order exactly instead of guessing from digits in titles.
    parsed_books = parse_catalog_html(raw_html, series_url, base_url)
    entries: list[ParsedSeriesEntry] = []
    seen: set[str] = set()
    for parsed in parsed_books:
        if parsed.external_id in seen:
            continue
        seen.add(parsed.external_id)
        entries.append(
            ParsedSeriesEntry(
                external_id=parsed.external_id,
                external_url=parsed.external_url,
                title=parsed.title,
                position=len(entries) + 1,
                authors=list(parsed.authors),
                narrators=list(parsed.narrators),
                duration_seconds=parsed.duration_seconds,
            )
        )

    return ParsedSeries(external_id=external_id, name=name, entries=entries)


class AudiopolkaParser(AudiobookSource):
    code = "audiopolka"
    name = "Audiopolka"

    def __init__(self, base_url: str = "https://audiopolka.club", delay_seconds: float = 0.35):
        self.base_url = base_url.rstrip("/")
        self.delay_seconds = max(delay_seconds, 0.0)
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0),
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/125 Safari/537.36 AbredCrawler/1.2",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
            },
        )

    async def _get(self, url: str) -> str:
        response = await self.client.get(url, headers={"Referer": self.base_url + "/"})
        response.raise_for_status()
        await asyncio.sleep(self.delay_seconds)
        return response.text

    async def get_catalog(self, page: int = 1) -> list[ParsedBook]:
        page = max(1, page)
        # Audiopolka uses path pagination: /p2/, /p3/, ... .
        # The old ?page=N form is ignored by the site and simply returns page 1,
        # which made multi-page sync re-process the same catalog page.
        url = self.base_url + "/" if page == 1 else f"{self.base_url}/p{page}/"
        return parse_catalog_html(await self._get(url), url, self.base_url)

    async def get_book(self, external_url: str) -> ParsedBook:
        return parse_book_html(await self._get(external_url), external_url, self.base_url)

    async def get_series(self, external_id: str) -> ParsedSeries:
        url = f"{self.base_url}/series/{external_id}/"
        return parse_series_html(await self._get(url), url, self.base_url)

    def is_preview_media_url(self, media_url: str) -> bool:
        return _is_audiopolka_preview_media_url(media_url)

    async def aclose(self) -> None:
        await self.client.aclose()
