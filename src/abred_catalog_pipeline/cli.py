from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .audiopolka.crawler import crawl_once
from .audiopolka.parser import AudiopolkaParser
from .cursor import CrawlCursor, plan_pages
from .feed import write_feed_bundle


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:12]


async def _run_audiopolka(args: argparse.Namespace) -> int:
    cursor_path = Path(args.state)
    cursor = CrawlCursor.load(cursor_path)
    parser = AudiopolkaParser(base_url=args.base_url, delay_seconds=args.delay)
    try:
        result, next_cursor = await crawl_once(parser, cursor, backfill_pages=args.backfill_pages)
    finally:
        await parser.aclose()

    run_id = args.run_id or _run_id()
    bundle_dir = Path(args.out) / run_id
    bundle = write_feed_bundle(
        output_dir=bundle_dir,
        run_id=run_id,
        source="audiopolka",
        pages=result["pages"],
        records=result["records"],
        tombstones=result["tombstones"],
        rejected=result["rejected"],
        cursor_before=result["cursor_before"],
        cursor_after=result["cursor_after"],
    )
    next_cursor.save(cursor_path)
    print(json.dumps({
        "run_id": run_id,
        "pages": result["pages"],
        "last_page": result["last_page"],
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
    parser = argparse.ArgumentParser(prog="abred-catalog-pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run-audiopolka", help="crawl page 1 + descending backfill and emit a feed bundle")
    run.add_argument("--state", default="state/audiopolka.json")
    run.add_argument("--out", default="artifacts")
    run.add_argument("--base-url", default="https://audiopolka.club")
    run.add_argument("--backfill-pages", type=int, default=5)
    run.add_argument("--delay", type=float, default=0.35)
    run.add_argument("--run-id", default="")

    plan = sub.add_parser("plan-pages", help="show page schedule without network")
    plan.add_argument("--last-page", type=int, required=True)
    plan.add_argument("--deep-page", type=int)
    plan.add_argument("--backfill-pages", type=int, default=5)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "run-audiopolka":
        return asyncio.run(_run_audiopolka(args))
    if args.command == "plan-pages":
        pages, next_deep = plan_pages(last_page=args.last_page, deep_page=args.deep_page, backfill_pages=args.backfill_pages)
        print(json.dumps({"pages": pages, "next_deep_page": next_deep}, indent=2))
        return 0
    raise SystemExit(2)
