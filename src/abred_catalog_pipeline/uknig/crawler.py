from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from ..cursor import CrawlCursor, plan_pages
from ..models import PreviewOnlyBookError, UnavailableBookError, book_to_feed_record
from .parser import UknigParser, parse_catalog_html


def detect_last_page(raw_html: str, base_url: str) -> int:
    soup = BeautifulSoup(raw_html, "html.parser")
    host = urlparse(base_url).netloc.casefold()
    pages = {1}
    for anchor in soup.select("a[href]"):
        href = anchor.get("href") or ""
        parsed = urlparse(href)
        if parsed.netloc and parsed.netloc.casefold() != host:
            continue
        values = parse_qs(parsed.query).get("p") or []
        if values and values[0].isdigit():
            pages.add(int(values[0]))
    return max(pages)


async def crawl_once(
    parser: UknigParser,
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
        page_url = page1_url if page == 1 else f"{parser.base_url}/?p={page}"
        html = page1_html if page == 1 else await parser._get(page_url)
        for row in parse_catalog_html(html, page_url, parser.base_url):
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
            records.append(book_to_feed_record(detail, source=parser.code))
        except (UnavailableBookError, PreviewOnlyBookError) as exc:
            # Для каталога обе ситуации означают одно: полной версии больше
            # нельзя предлагать пользователю. Tombstone также корректно снимает
            # уже импортированную книгу, если она позже стала preview-only.
            tombstones.append({
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
