from __future__ import annotations

import re
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..cursor import CrawlCursor, plan_pages
from ..models import PreviewOnlyBookError, UnavailableBookError, book_to_feed_record
from .parser import AudiopolkaParser, parse_catalog_html

_PAGE_RE = re.compile(r"/p(\d+)/?$")


def detect_last_page(raw_html: str, base_url: str) -> int:
    soup = BeautifulSoup(raw_html, "html.parser")
    pages = {1}
    host = urlparse(base_url).netloc.casefold()
    for node in soup.select("a[href]"):
        href = node.get("href") or ""
        parsed = urlparse(href)
        if parsed.netloc and parsed.netloc.casefold() != host:
            continue
        path = parsed.path or href
        match = _PAGE_RE.search(path)
        if match:
            pages.add(int(match.group(1)))
    return max(pages)


def _merge_catalog_fallback(detail, catalog):
    if not detail.authors:
        detail.authors = list(catalog.authors)
    if not detail.narrators:
        detail.narrators = list(catalog.narrators)
    if not detail.genres:
        detail.genres = list(catalog.genres)
    if not detail.duration_seconds:
        detail.duration_seconds = int(catalog.duration_seconds or 0)
    if not detail.series_name:
        detail.series_name = catalog.series_name
    if not detail.series_external_id:
        detail.series_external_id = catalog.series_external_id
    if detail.series_position is None:
        detail.series_position = catalog.series_position
    return detail


async def crawl_once(
    parser: AudiopolkaParser,
    cursor: CrawlCursor,
    *,
    backfill_pages: int = 5,
) -> tuple[dict[str, Any], CrawlCursor]:
    page1_url = parser.base_url + "/"
    page1_html = await parser._get(page1_url)
    last_page = detect_last_page(page1_html, parser.base_url)
    if last_page == 1 and cursor.last_page and cursor.last_page > 1:
        last_page = cursor.last_page

    pages, next_deep, backfill_complete = plan_pages(
        last_page=last_page,
        deep_page=cursor.deep_page,
        backfill_pages=backfill_pages,
        backfill_complete=cursor.backfill_complete,
    )
    catalog_rows = []
    seen_ids: set[str] = set()
    for page in pages:
        if page == 1:
            rows = parse_catalog_html(page1_html, page1_url, parser.base_url)
        else:
            url = f"{parser.base_url}/p{page}/"
            rows = parse_catalog_html(await parser._get(url), url, parser.base_url)
        for row in rows:
            if row.external_id in seen_ids:
                continue
            seen_ids.add(row.external_id)
            catalog_rows.append(row)

    records: list[dict[str, Any]] = []
    tombstones: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for catalog in catalog_rows:
        try:
            detail = await parser.get_book(catalog.external_url)
            detail = _merge_catalog_fallback(detail, catalog)
            # Feed contract: Audiopolka records are playable only when at least
            # one full chapter/media URL was parsed. Metadata-only rows must not
            # reach Backend preflight, otherwise one source glitch blocks the
            # entire automatic intake stream.
            if not detail.chapters:
                rejected.append({
                    "source": parser.code,
                    "external_id": catalog.external_id,
                    "external_url": catalog.external_url,
                    "reason": "audiopolka_missing_full_chapters",
                })
                continue
            records.append(book_to_feed_record(detail, source=parser.code))
        except UnavailableBookError as exc:
            tombstones.append({
                "source": parser.code,
                "external_id": catalog.external_id,
                "external_url": catalog.external_url,
                "reason": exc.reason,
            })
        except PreviewOnlyBookError as exc:
            rejected.append({
                "source": parser.code,
                "external_id": catalog.external_id,
                "external_url": catalog.external_url,
                "reason": exc.reason,
            })
        except Exception as exc:
            rejected.append({
                "source": parser.code,
                "external_id": catalog.external_id,
                "external_url": catalog.external_url,
                "reason": "detail_fetch_or_parse_error",
                "error_type": type(exc).__name__,
                "message": str(exc)[:500],
            })

    next_cursor = CrawlCursor(
        source=parser.code,
        deep_page=next_deep,
        last_page=last_page,
        backfill_complete=backfill_complete,
    )
    result = {
        "source": parser.code,
        "pages": pages,
        "last_page": last_page,
        "catalog_rows": len(catalog_rows),
        "records": records,
        "tombstones": tombstones,
        "rejected": rejected,
        "cursor_before": asdict(cursor),
        "cursor_after": asdict(next_cursor),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    return result, next_cursor
