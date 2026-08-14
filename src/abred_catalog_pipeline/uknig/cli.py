from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..cursor import CrawlCursor
from ..feed import write_feed_bundle
from .crawler import crawl_once
from .parser import UknigParser


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:12]


def _load_uknig_cursor(path: Path) -> CrawlCursor:
    loaded = CrawlCursor.load(path)
    return CrawlCursor(
        source="uknig",
        deep_page=loaded.deep_page,
        last_page=loaded.last_page,
        backfill_complete=loaded.backfill_complete,
    )


async def _run(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    cursor = _load_uknig_cursor(state_path)

    parser = UknigParser(base_url=args.base_url, delay_seconds=args.delay)
    try:
        result, next_cursor = await crawl_once(
            parser,
            cursor,
            backfill_pages=max(0, int(args.backfill_pages)),
        )
    finally:
        await parser.aclose()

    run_id = args.run_id or _run_id()
    bundle_dir = Path(args.out) / run_id
    bundle = write_feed_bundle(
        output_dir=bundle_dir,
        run_id=run_id,
        source="uknig",
        pages=result["pages"],
        records=result["records"],
        tombstones=result["tombstones"],
        rejected=result["rejected"],
        cursor_before=result["cursor_before"],
        cursor_after=result["cursor_after"],
    )
    next_cursor.save(state_path)

    print(json.dumps({
        "run_id": run_id,
        "pages": result["pages"],
        "last_page": result["last_page"],
        "catalog_rows": result["catalog_rows"],
        "records": len(result["records"]),
        "tombstones": len(result["tombstones"]),
        "rejected": len(result["rejected"]),
        "cursor": result["cursor_after"],
        "feed": bundle["feed_path"],
        "manifest": bundle["manifest_path"],
        "sha256": bundle["manifest"]["sha256"],
    }, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="abred-uknig")
    parser.add_argument("--state", default="state/uknig.json")
    parser.add_argument("--out", default="artifacts")
    parser.add_argument("--base-url", default="https://uknig.com")
    parser.add_argument("--backfill-pages", type=int, default=5)
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("--run-id", default="")
    return parser


def main() -> int:
    return asyncio.run(_run(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
