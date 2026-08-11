from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .audiopolka.crawler import crawl_once
from .audiopolka.parser import AudiopolkaParser
from .cursor import CrawlCursor, plan_pages
from .feed import write_feed_bundle
from .rutracker.crawler import RuTrackerState, crawl_once as crawl_rutracker_once, parse_forum_ids
from .rutracker.parser import RuTrackerWorkerClient
from .rutracker.torrserver import TorrServerClient


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


async def _run_rutracker(args: argparse.Namespace) -> int:
    if args.torrserver_enrich and args.download_torrents:
        raise SystemExit("--torrserver-enrich and --download-torrents are mutually exclusive")
    if args.torrserver_enrich and not (args.torrserver_url or "").strip():
        raise SystemExit("--torrserver-enrich requires --torrserver-url or TORRSERVER_URL")

    cursor_path = Path(args.state)
    cursor = RuTrackerState.load(cursor_path)
    parser = RuTrackerWorkerClient(
        worker_url=args.worker_url,
        worker_token=args.worker_token,
        worker_token_header=args.worker_token_header,
        worker_mode=args.worker_mode,
        base_url=args.base_url,
        delay_seconds=args.delay,
        page_size=args.page_size,
    )
    torrserver = None
    if args.torrserver_enrich:
        torrserver = TorrServerClient(
            base_url=args.torrserver_url,
            username=args.torrserver_username,
            password=args.torrserver_password,
            timeout_seconds=args.torrserver_timeout,
            poll_interval_seconds=args.torrserver_poll_interval,
        )
    try:
        result, next_cursor = await crawl_rutracker_once(
            parser,
            cursor,
            forum_ids=parse_forum_ids(args.forums),
            backfill_pages=args.backfill_pages,
            max_topics=max(0, int(args.max_topics or 0)),
            download_torrents=bool(args.download_torrents),
            torrserver=torrserver,
            torrserver_max_new=max(0, int(args.torrserver_max_new or 0)),
            torrserver_replay_successes=max(1, int(args.torrserver_replay_successes or 1)),
            advance_cursor=bool(args.advance_cursor),
        )
    finally:
        await parser.aclose()
        if torrserver is not None:
            await torrserver.aclose()

    run_id = args.run_id or _run_id()
    bundle_dir = Path(args.out) / run_id
    bundle = write_feed_bundle(
        output_dir=bundle_dir,
        run_id=run_id,
        source="rutracker",
        pages=result["pages"],
        records=result["records"],
        tombstones=result["tombstones"],
        rejected=result["rejected"],
        cursor_before=result["cursor_before"],
        cursor_after=result["cursor_after"],
    )
    if args.advance_cursor and not result["truncated"]:
        next_cursor.save(cursor_path)
    print(json.dumps({
        "run_id": run_id,
        "forums": list(parse_forum_ids(args.forums)),
        "pages": result["pages"],
        "records": len(result["records"]),
        "tombstones": len(result["tombstones"]),
        "rejected": len(result["rejected"]),
        "topics_seen": result["topics_seen"],
        "truncated": result["truncated"],
        "cursor_held_for_metadata": result["cursor_held_for_metadata"],
        "cursor_advanced": result["cursor_advanced"],
        "torrent_metadata": result["torrent_metadata"],
        "cursor": result["cursor_after"],
        "feed": bundle["feed_path"],
        "manifest": bundle["manifest_path"],
        "sha256": bundle["manifest"]["sha256"],
    }, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="abred-catalog-pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser(
        "run-audiopolka",
        help="crawl page 1 + finite descending bootstrap and emit a feed bundle",
    )
    run.add_argument("--state", default="state/audiopolka.json")
    run.add_argument("--out", default="artifacts")
    run.add_argument("--base-url", default="https://audiopolka.club")
    run.add_argument("--backfill-pages", type=int, default=5)
    run.add_argument("--delay", type=float, default=0.35)
    run.add_argument("--run-id", default="")

    rt = sub.add_parser(
        "run-rutracker",
        help="crawl RuTracker viewforum/topic pages through Cloudflare Worker and emit a feed bundle",
    )
    rt.add_argument("--state", default="state/rutracker.json")
    rt.add_argument("--out", default="artifacts")
    rt.add_argument("--base-url", default="https://rutracker.org")
    rt.add_argument("--worker-url", default=os.environ.get("RUTRACKER_WORKER_URL", ""))
    rt.add_argument("--worker-token", default=os.environ.get("RUTRACKER_WORKER_TOKEN", ""))
    rt.add_argument(
        "--worker-token-header",
        default=os.environ.get("RUTRACKER_WORKER_TOKEN_HEADER", "X-Proxy-Token"),
    )
    rt.add_argument(
        "--worker-mode",
        choices=("mirror", "fetch"),
        default=os.environ.get("RUTRACKER_WORKER_MODE", "mirror"),
    )
    rt.add_argument("--forums", default="")
    rt.add_argument("--page-size", type=int, default=50)
    rt.add_argument("--backfill-pages", type=int, default=1)
    rt.add_argument(
        "--max-topics",
        type=int,
        default=0,
        help="manual probe bound; truncated runs never advance cursors",
    )
    torrent_mode = rt.add_mutually_exclusive_group()
    torrent_mode.add_argument(
        "--download-torrents",
        dest="download_torrents",
        action="store_true",
        help="optional enrichment: fetch raw .torrent metainfo through the Worker",
    )
    torrent_mode.add_argument(
        "--no-torrent-download",
        dest="download_torrents",
        action="store_false",
        help=argparse.SUPPRESS,
    )
    rt.set_defaults(download_torrents=False)
    rt.add_argument(
        "--torrserver-enrich",
        action="store_true",
        help="enrich new RuTracker info hashes through TorrServer using magnet metadata",
    )
    rt.add_argument("--torrserver-url", default=os.environ.get("TORRSERVER_URL", ""))
    rt.add_argument("--torrserver-username", default=os.environ.get("TORRSERVER_USERNAME", ""))
    rt.add_argument("--torrserver-password", default=os.environ.get("TORRSERVER_PASSWORD", ""))
    rt.add_argument(
        "--torrserver-timeout",
        type=float,
        default=float(os.environ.get("TORRSERVER_TIMEOUT_SECONDS", "30") or 30),
    )
    rt.add_argument(
        "--torrserver-poll-interval",
        type=float,
        default=float(os.environ.get("TORRSERVER_POLL_INTERVAL_SECONDS", "1") or 1),
    )
    rt.add_argument(
        "--torrserver-max-new",
        type=int,
        default=int(os.environ.get("TORRSERVER_MAX_NEW", "0") or 0),
        help="maximum unconfirmed hashes to ask TorrServer for in one run; 0 = unlimited",
    )
    rt.add_argument(
        "--torrserver-replay-successes",
        type=int,
        default=int(os.environ.get("TORRSERVER_REPLAY_SUCCESSES", "2") or 2),
        help="successful enriched deliveries required before a hash becomes metadata-known",
    )
    rt.add_argument("--advance-cursor", action="store_true")
    rt.add_argument("--delay", type=float, default=0.15)
    rt.add_argument("--run-id", default="")

    plan = sub.add_parser("plan-pages", help="show finite page schedule without network")
    plan.add_argument("--last-page", type=int, required=True)
    plan.add_argument("--deep-page", type=int)
    plan.add_argument("--backfill-pages", type=int, default=5)
    plan.add_argument("--backfill-complete", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "run-audiopolka":
        return asyncio.run(_run_audiopolka(args))
    if args.command == "run-rutracker":
        return asyncio.run(_run_rutracker(args))
    if args.command == "plan-pages":
        pages, next_deep, backfill_complete = plan_pages(
            last_page=args.last_page,
            deep_page=args.deep_page,
            backfill_pages=args.backfill_pages,
            backfill_complete=args.backfill_complete,
        )
        print(json.dumps({
            "pages": pages,
            "next_deep_page": next_deep,
            "backfill_complete": backfill_complete,
        }, indent=2))
        return 0
    raise SystemExit(2)
