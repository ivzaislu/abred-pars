from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CrawlCursor:
    source: str = "audiopolka"
    deep_page: int | None = None
    last_page: int | None = None

    @classmethod
    def load(cls, path: str | Path) -> "CrawlCursor":
        path = Path(path)
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            source=str(data.get("source") or "audiopolka"),
            deep_page=_positive_or_none(data.get("deep_page")),
            last_page=_positive_or_none(data.get("last_page")),
        )

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)


def _positive_or_none(value: object) -> int | None:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 1 else None


def plan_pages(*, last_page: int, deep_page: int | None, backfill_pages: int = 5) -> tuple[list[int], int]:
    """Return [page 1 + descending backfill] and the next deep cursor.

    Page 1 is always scanned. Backfill never includes page 1. Reaching page 2
    wraps the next run back to the current last page.
    """
    last_page = max(1, int(last_page))
    backfill_pages = max(0, int(backfill_pages))
    if last_page == 1 or backfill_pages == 0:
        return [1], last_page

    start = int(deep_page or last_page)
    if start < 2 or start > last_page:
        start = last_page

    backfill = list(range(start, max(1, start - backfill_pages), -1))
    backfill = [page for page in backfill if page >= 2]
    pages = [1, *backfill]

    next_deep = start - len(backfill)
    if next_deep < 2:
        next_deep = last_page
    return pages, next_deep
