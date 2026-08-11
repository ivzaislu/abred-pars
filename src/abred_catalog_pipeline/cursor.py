from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CrawlCursor:
    source: str = "audiopolka"
    deep_page: int | None = None
    last_page: int | None = None
    backfill_complete: bool = False

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
            backfill_complete=bool(data.get("backfill_complete", False)),
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


def plan_pages(
    *,
    last_page: int,
    deep_page: int | None,
    backfill_pages: int = 5,
    backfill_complete: bool = False,
) -> tuple[list[int], int | None, bool]:
    """Return page 1 + finite descending bootstrap pages.

    Once page 2 has been scanned, bootstrap is permanently complete and
    subsequent runs scan page 1 only. There is deliberately no wrap back to
    the current last page.
    """
    last_page = max(1, int(last_page))
    backfill_pages = max(0, int(backfill_pages))

    if backfill_complete:
        return [1], None, True

    if last_page == 1:
        return [1], None, True

    if backfill_pages == 0:
        return [1], deep_page or last_page, False

    start = int(deep_page or last_page)
    if start < 2 or start > last_page:
        start = last_page

    backfill = list(range(start, max(1, start - backfill_pages), -1))
    backfill = [page for page in backfill if page >= 2]
    pages = [1, *backfill]

    if 2 in backfill:
        return pages, None, True

    return pages, start - len(backfill), False
