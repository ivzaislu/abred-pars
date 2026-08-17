from __future__ import annotations

import asyncio
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator
from uuid import uuid4

from ..audiopolka.crawler import crawl_once as crawl_audiopolka_once
from ..audiopolka.parser import AudiopolkaParser
from ..cursor import CrawlCursor
from ..feed import write_feed_bundle
from ..rutracker.crawler import RuTrackerState, crawl_once as crawl_rutracker_once, parse_forum_ids
from ..rutracker.parser import RuTrackerWorkerClient
from ..rutracker.torrserver import TorrServerPool
from ..uknig.crawler import crawl_once as crawl_uknig_once
from ..uknig.parser import UknigParser
from . import SUPPORTED_SOURCES
from .config import ServerSettings
from .storage import FeedRecord, ServerStorage

try:
    import fcntl
except ImportError:  # pragma: no cover - server image is Linux
    fcntl = None  # type: ignore[assignment]


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:12]


@contextmanager
def _source_file_lock(path: Path) -> Iterator[bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    acquired = True
    try:
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                acquired = False
        yield acquired
    finally:
        if acquired and fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


class ParserRunner:
    def __init__(self, settings: ServerSettings, storage: ServerStorage):
        self.settings = settings
        self.storage = storage
        self._process_locks = {source: asyncio.Lock() for source in SUPPORTED_SOURCES}

    async def run_source(self, source: str) -> dict[str, Any]:
        if source not in SUPPORTED_SOURCES:
            raise ValueError(f"unsupported source: {source}")
        self.settings.validate_source(source)
        async with self._process_locks[source]:
            with _source_file_lock(self.settings.locks_dir / f"{source}.lock") as acquired:
                if not acquired:
                    return {"source": source, "status": "LOCKED"}
                return await self._run_locked(source)

    async def _run_locked(self, source: str) -> dict[str, Any]:
        run_id = _run_id()
        staging = self.settings.staging_dir / f"{source}-{run_id}"
        staging.mkdir(parents=True, exist_ok=False)
        self.storage.start_run(run_id=run_id, source=source)
        try:
            if source == "audiopolka":
                record, stats = await self._run_audiopolka(run_id, staging)
            elif source == "uknig":
                record, stats = await self._run_uknig(run_id, staging)
            else:
                record, stats = await self._run_rutracker(run_id, staging)
            self.storage.finish_run(
                run_id=run_id,
                status="completed",
                feed_id=record.feed_id,
                stats=stats,
            )
            return {
                "source": source,
                "status": "COMPLETED",
                "run_id": run_id,
                "feed": record.public_dict(),
                "stats": stats,
            }
        except Exception as exc:
            self.storage.finish_run(
                run_id=run_id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _publish(
        self,
        *,
        staging: Path,
        run_id: str,
        source: str,
        result: dict[str, Any],
        save_cursor: Callable[[], None],
    ) -> FeedRecord:
        write_feed_bundle(
            output_dir=staging,
            run_id=run_id,
            source=source,
            pages=list(result.get("pages") or []),
            records=list(result.get("records") or []),
            tombstones=list(result.get("tombstones") or []),
            rejected=list(result.get("rejected") or []),
            cursor_before=dict(result.get("cursor_before") or {}),
            cursor_after=dict(result.get("cursor_after") or {}),
        )
        record = self.storage.publish_bundle(
            staging_dir=staging,
            source=source,
            run_id=run_id,
            feeds_dir=self.settings.feeds_dir,
        )
        # Feed publication is durable before cursor advancement. A crash before
        # this point cannot make the producer skip unseen source data.
        save_cursor()
        return record

    async def _run_audiopolka(self, run_id: str, staging: Path) -> tuple[FeedRecord, dict[str, Any]]:
        state_path = self.settings.state_dir / "audiopolka.json"
        cursor = CrawlCursor.load(state_path)
        parser = AudiopolkaParser(
            base_url=self.settings.audiopolka_base_url,
            delay_seconds=self.settings.audiopolka_delay_seconds,
        )
        try:
            result, next_cursor = await crawl_audiopolka_once(
                parser,
                cursor,
                backfill_pages=self.settings.audiopolka_backfill_pages,
            )
        finally:
            await parser.aclose()
        record = self._publish(
            staging=staging,
            run_id=run_id,
            source="audiopolka",
            result=result,
            save_cursor=lambda: next_cursor.save(state_path),
        )
        stats = {
            "pages": result.get("pages") or [],
            "last_page": result.get("last_page"),
            "records": len(result.get("records") or []),
            "tombstones": len(result.get("tombstones") or []),
            "rejected": len(result.get("rejected") or []),
            "cursor": result.get("cursor_after") or {},
        }
        return record, stats

    async def _run_uknig(self, run_id: str, staging: Path) -> tuple[FeedRecord, dict[str, Any]]:
        state_path = self.settings.state_dir / "uknig.json"
        loaded = CrawlCursor.load(state_path)
        cursor = CrawlCursor(
            source="uknig",
            deep_page=loaded.deep_page,
            last_page=loaded.last_page,
            backfill_complete=loaded.backfill_complete,
        )
        parser = UknigParser(
            base_url=self.settings.uknig_base_url,
            delay_seconds=self.settings.uknig_delay_seconds,
        )
        try:
            result, next_cursor = await crawl_uknig_once(
                parser,
                cursor,
                backfill_pages=self.settings.uknig_backfill_pages,
            )
        finally:
            await parser.aclose()
        record = self._publish(
            staging=staging,
            run_id=run_id,
            source="uknig",
            result=result,
            save_cursor=lambda: next_cursor.save(state_path),
        )
        stats = {
            "pages": result.get("pages") or [],
            "last_page": result.get("last_page"),
            "catalog_rows": result.get("catalog_rows"),
            "records": len(result.get("records") or []),
            "tombstones": len(result.get("tombstones") or []),
            "rejected": len(result.get("rejected") or []),
            "cursor": result.get("cursor_after") or {},
        }
        return record, stats

    async def _run_rutracker(self, run_id: str, staging: Path) -> tuple[FeedRecord, dict[str, Any]]:
        state_path = self.settings.state_dir / "rutracker.json"
        cursor = RuTrackerState.load(state_path)
        parser = RuTrackerWorkerClient(
            worker_url=self.settings.rutracker_worker_url,
            worker_token=self.settings.rutracker_worker_token,
            worker_token_header=self.settings.rutracker_worker_token_header,
            worker_mode=self.settings.rutracker_worker_mode,
            base_url=self.settings.rutracker_base_url,
            delay_seconds=self.settings.rutracker_delay_seconds,
            page_size=50,
        )
        torrserver = None
        if self.settings.rutracker_torrserver_enrich:
            torrserver = TorrServerPool.from_urls(
                list(self.settings.torrserver_urls),
                username=self.settings.torrserver_username,
                password=self.settings.torrserver_password,
                timeout_seconds=self.settings.torrserver_timeout_seconds,
                poll_interval_seconds=self.settings.torrserver_poll_interval_seconds,
            )
        try:
            result, next_cursor = await crawl_rutracker_once(
                parser,
                cursor,
                forum_ids=parse_forum_ids(self.settings.rutracker_forums),
                backfill_pages=self.settings.rutracker_backfill_pages,
                max_topics=0,
                download_torrents=False,
                torrserver=torrserver,
                torrserver_max_new=0,
                torrserver_replay_successes=self.settings.torrserver_replay_successes,
                advance_cursor=True,
            )
        finally:
            await parser.aclose()
            if torrserver is not None:
                await torrserver.aclose()

        def save_cursor() -> None:
            if result.get("truncated"):
                raise RuntimeError("server RuTracker run was unexpectedly truncated; cursor not advanced")
            next_cursor.save(state_path)

        record = self._publish(
            staging=staging,
            run_id=run_id,
            source="rutracker",
            result=result,
            save_cursor=save_cursor,
        )
        stats = {
            "forums": list(parse_forum_ids(self.settings.rutracker_forums)),
            "pages": result.get("pages") or [],
            "records": len(result.get("records") or []),
            "tombstones": len(result.get("tombstones") or []),
            "rejected": len(result.get("rejected") or []),
            "topics_seen": result.get("topics_seen"),
            "cursor_held_for_metadata": result.get("cursor_held_for_metadata"),
            "cursor_advanced": result.get("cursor_advanced"),
            "torrent_metadata": result.get("torrent_metadata") or {},
            "cursor": result.get("cursor_after") or {},
        }
        return record, stats
