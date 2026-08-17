from __future__ import annotations

import argparse
import asyncio
import json

import uvicorn

from . import SUPPORTED_SOURCES
from .api import create_app
from .config import ServerSettings
from .runner import ParserRunner
from .scheduler import ParserScheduler
from .storage import ServerStorage


def _runtime() -> tuple[ServerSettings, ServerStorage, ParserRunner]:
    settings = ServerSettings.from_env()
    settings.ensure_directories()
    storage = ServerStorage(db_path=settings.db_path, data_dir=settings.data_dir)
    storage.initialize()
    return settings, storage, ParserRunner(settings, storage)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="abred-parser-server")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="run read-only feed API and optional built-in scheduler")

    run = sub.add_parser("run", help="run one parser source immediately")
    run.add_argument("source", choices=SUPPORTED_SOURCES)

    sub.add_parser("scheduler", help="run scheduler without HTTP API")

    status = sub.add_parser("status", help="print parser source status")
    status.add_argument("source", choices=SUPPORTED_SOURCES, nargs="?")

    feeds = sub.add_parser("list-feeds", help="list published immutable feeds")
    feeds.add_argument("--source", choices=SUPPORTED_SOURCES)
    feeds.add_argument("--after", type=int, default=0)
    feeds.add_argument("--limit", type=int, default=50)
    return parser


async def _run_once(source: str) -> int:
    _, _, runner = _runtime()
    result = await runner.run_source(source)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


async def _scheduler_only() -> int:
    settings, storage, runner = _runtime()
    scheduler = ParserScheduler(settings, storage, runner)
    await scheduler.run_forever()
    return 0


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "serve":
        settings = ServerSettings.from_env()
        settings.validate_api()
        app = create_app(settings)
        uvicorn.run(app, host=settings.host, port=settings.port, workers=1)
        return 0
    if args.command == "run":
        return asyncio.run(_run_once(args.source))
    if args.command == "scheduler":
        return asyncio.run(_scheduler_only())

    settings, storage, _ = _runtime()
    if args.command == "status":
        if args.source:
            payload = storage.source_status(args.source)
        else:
            payload = {"sources": [storage.source_status(source) for source in SUPPORTED_SOURCES]}
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "list-feeds":
        limit = max(1, min(int(args.limit), 100))
        rows = storage.list_feeds(source=args.source, after=max(0, int(args.after)), limit=limit)
        print(json.dumps([row.public_dict() for row in rows], ensure_ascii=False, indent=2))
        return 0
    raise SystemExit(2)


if __name__ == "__main__":
    raise SystemExit(main())
