from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..cursor import plan_pages
from ..models import ParsedBook, ParsedTorrent, book_to_feed_record
from .parser import (
    DEFAULT_AUDIOBOOK_FORUM_IDS,
    RuTrackerWorkerClient,
    TrackerRow,
    detect_last_forum_page,
    hydrate_book_from_torrent,
    parse_forum_html,
    parse_topic_html,
    parse_torrent_bytes,
)
from .torrserver import TorrServerClient, TorrServerPool


@dataclass(frozen=True, slots=True)
class ForumCursor:
    deep_page: int | None = None
    last_page: int | None = None
    backfill_complete: bool = False


def _hash40(value: object) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 40:
        return ""
    try:
        int(text, 16)
    except ValueError:
        return ""
    return text


@dataclass(frozen=True, slots=True)
class RetryTopic:
    topic_id: str
    topic_url: str
    torrent_url: str
    title: str = ""
    forum_id: str = ""
    forum_name: str = ""
    size_bytes: int = 0
    seeders: int = 0
    leechers: int = 0
    attempts: int = 0
    last_error: str = ""
    last_attempt_at: str = ""

    def to_row(self) -> TrackerRow:
        return TrackerRow(
            topic_id=self.topic_id,
            topic_url=self.topic_url,
            torrent_url=self.torrent_url,
            title=self.title,
            forum_id=self.forum_id,
            forum_name=self.forum_name,
            size_bytes=self.size_bytes,
            seeders=self.seeders,
            leechers=self.leechers,
        )


@dataclass(slots=True)
class RuTrackerState:
    source: str = "rutracker"
    forums: dict[str, ForumCursor] = field(default_factory=dict)
    # Hashes that have already completed the confirmation/replay policy.
    # They are still resolved through TorrServer on every crawl so each feed
    # record always contains the torrent file list and playable chapters.
    torrent_metadata_hashes: set[str] = field(default_factory=set)
    # Successful enriched deliveries that still need one or more replay passes.
    torrent_metadata_pending: dict[str, int] = field(default_factory=dict)
    # Per-topic transient failures. The deep cursor is allowed to move on; these
    # topics are retried directly on subsequent runs until they succeed or are
    # classified as permanently non-playable.
    topic_retry_queue: dict[str, RetryTopic] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "RuTrackerState":
        path = Path(path)
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        forums: dict[str, ForumCursor] = {}
        for key, value in (raw.get("forums") or {}).items():
            value = value or {}
            forums[str(key)] = ForumCursor(
                deep_page=_positive_or_none(value.get("deep_page")),
                last_page=_positive_or_none(value.get("last_page")),
                backfill_complete=bool(value.get("backfill_complete", False)),
            )
        hashes = {
            parsed
            for value in (raw.get("torrent_metadata_hashes") or [])
            if (parsed := _hash40(value))
        }
        pending: dict[str, int] = {}
        for key, value in (raw.get("torrent_metadata_pending") or {}).items():
            parsed = _hash40(key)
            if not parsed or parsed in hashes:
                continue
            try:
                count = max(0, int(value))
            except (TypeError, ValueError):
                continue
            if count:
                pending[parsed] = count

        retry_queue: dict[str, RetryTopic] = {}
        for key, value in (raw.get("topic_retry_queue") or {}).items():
            if not isinstance(value, dict):
                continue
            topic_id = str(value.get("topic_id") or key or "").strip()
            if not topic_id:
                continue
            topic_url = str(value.get("topic_url") or "").strip()
            torrent_url = str(value.get("torrent_url") or "").strip()
            try:
                attempts = max(0, int(value.get("attempts") or 0))
            except (TypeError, ValueError):
                attempts = 0
            retry_queue[topic_id] = RetryTopic(
                topic_id=topic_id,
                topic_url=topic_url,
                torrent_url=torrent_url,
                title=str(value.get("title") or ""),
                forum_id=str(value.get("forum_id") or ""),
                forum_name=str(value.get("forum_name") or ""),
                size_bytes=_nonnegative_int(value.get("size_bytes")),
                seeders=_nonnegative_int(value.get("seeders")),
                leechers=_nonnegative_int(value.get("leechers")),
                attempts=attempts,
                last_error=str(value.get("last_error") or "")[:500],
                last_attempt_at=str(value.get("last_attempt_at") or ""),
            )
        return cls(
            source="rutracker",
            forums=forums,
            torrent_metadata_hashes=hashes,
            torrent_metadata_pending=pending,
            topic_retry_queue=retry_queue,
        )

    def as_dict(self) -> dict:
        # Feed cursor metadata stays compact and backward-compatible.
        return {
            "source": "rutracker",
            "forums": {
                key: asdict(value)
                for key, value in sorted(self.forums.items(), key=lambda item: int(item[0]))
            },
        }

    def storage_dict(self) -> dict:
        out = self.as_dict()
        out["torrent_metadata_hashes"] = sorted(self.torrent_metadata_hashes)
        out["torrent_metadata_pending"] = {
            key: int(value)
            for key, value in sorted(self.torrent_metadata_pending.items())
            if key not in self.torrent_metadata_hashes and int(value) > 0
        }
        out["topic_retry_queue"] = {
            key: asdict(value)
            for key, value in sorted(self.topic_retry_queue.items())
        }
        return out

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self.storage_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)


def _positive_or_none(value: object) -> int | None:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 1 else None


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def parse_forum_ids(raw: str | None) -> tuple[int, ...]:
    if not raw:
        return tuple(DEFAULT_AUDIOBOOK_FORUM_IDS)
    out: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        value = int(token)
        if value not in out:
            out.append(value)
    return tuple(out)


class _PermanentTopicReject(RuntimeError):
    """Content is permanently non-playable and must not enter the retry queue."""

    def __init__(self, message: str, *, reason: str = "rutracker_topic_permanent_reject"):
        super().__init__(message)
        self.reason = reason


def _assert_torrserver_ready(book: ParsedBook) -> None:
    torrent = book.torrent
    if torrent is None or not torrent.info_hash:
        raise RuntimeError("topic has no usable info_hash")
    audio_indexes = {
        item.index
        for item in torrent.files
        if item.media_type == "audio"
    }
    if not audio_indexes:
        raise _PermanentTopicReject(
            f"RuTracker torrent metadata has no supported audio files: {book.external_url}",
            reason="rutracker_unsupported_audio",
        )
    if not book.chapters:
        raise RuntimeError(
            f"RuTracker torrent metadata produced no chapters: {book.external_url}"
        )
    for chapter in book.chapters:
        try:
            file_index = int(chapter.external_id)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"RuTracker chapter has invalid torrent file index: {book.external_url}"
            ) from exc
        if file_index not in audio_indexes:
            raise RuntimeError(
                f"RuTracker chapter points to a missing audio file: {book.external_url}"
            )
        expected_url = f"torrent://{torrent.info_hash}/{file_index}"
        if chapter.media_url != expected_url:
            raise RuntimeError(
                f"RuTracker chapter media_url/info_hash mismatch: {book.external_url}"
            )


def _append_record(
    records: list[dict],
    *,
    book: ParsedBook,
    row: TrackerRow,
    forum_key: str,
    torrent_status: str,
    metadata_attempted: bool,
    torrent_error: str = "",
) -> None:
    if not book.torrent or not book.torrent.info_hash:
        raise RuntimeError("topic has no usable info_hash")
    record = book_to_feed_record(book, source="rutracker")
    record["rutracker"] = {
        "forum_id": row.forum_id or forum_key,
        "forum_name": row.forum_name,
        "seeders": row.seeders,
        "leechers": row.leechers,
        "listed_size_bytes": row.size_bytes,
        "torrent_metadata_status": torrent_status,
        "torrent_metadata_attempted": metadata_attempted,
        "torrent_metadata_error": torrent_error or None,
    }
    if book.series_entries and record.get("series"):
        record["series"][0]["entries"] = [
            asdict(entry)
            for entry in book.series_entries
        ]
    records.append(record)


def _append_rejection(
    rejected: list[dict],
    row: TrackerRow,
    exc: Exception,
    *,
    retry_queued: bool = False,
) -> None:
    permanent_reject = isinstance(exc, _PermanentTopicReject)
    if permanent_reject:
        reason = exc.reason
    elif retry_queued:
        reason = "rutracker_topic_retry_queued"
    else:
        reason = "rutracker_topic_rejected"
    rejected.append({
        "source": "rutracker",
        "external_id": row.topic_id,
        "external_url": row.topic_url,
        "reason": reason,
        "detail": str(exc)[:500],
        "non_blocking": permanent_reject or retry_queued,
        "retry_queued": retry_queued,
    })


def _queue_retry(
    queue: dict[str, RetryTopic],
    row: TrackerRow,
    *,
    error: str,
    increment_attempt: bool = True,
) -> bool:
    previous = queue.get(row.topic_id)
    attempts = (previous.attempts if previous is not None else 0) + (1 if increment_attempt else 0)
    queue[row.topic_id] = RetryTopic(
        topic_id=row.topic_id,
        topic_url=row.topic_url or (previous.topic_url if previous else ""),
        torrent_url=row.torrent_url or (previous.torrent_url if previous else ""),
        title=row.title or (previous.title if previous else ""),
        forum_id=row.forum_id or (previous.forum_id if previous else ""),
        forum_name=row.forum_name or (previous.forum_name if previous else ""),
        size_bytes=row.size_bytes or (previous.size_bytes if previous else 0),
        seeders=row.seeders if row.seeders or previous is None else previous.seeders,
        leechers=row.leechers if row.leechers or previous is None else previous.leechers,
        attempts=attempts,
        last_error=error[:500],
        last_attempt_at=datetime.now(timezone.utc).isoformat(),
    )
    return previous is None


@dataclass(slots=True)
class _PendingMetadata:
    row: TrackerRow
    forum_key: str
    book: ParsedBook
    torrent_ref: ParsedTorrent
    info_hash: str
    was_known: bool
    from_retry: bool
    task: asyncio.Task[ParsedTorrent]


async def crawl_once(
    parser: RuTrackerWorkerClient,
    state: RuTrackerState,
    *,
    forum_ids: tuple[int, ...],
    backfill_pages: int = 1,
    max_topics: int = 0,
    download_torrents: bool = False,
    torrserver: TorrServerClient | TorrServerPool | None = None,
    torrserver_max_new: int = 0,
    torrserver_replay_successes: int = 1,
    advance_cursor: bool = True,
) -> tuple[dict, RuTrackerState]:
    if download_torrents and torrserver is not None:
        raise ValueError("download_torrents and torrserver enrichment are mutually exclusive")

    records: list[dict] = []
    rejected: list[dict] = []
    tombstones: list[dict] = []
    seen_topics: set[str] = set()
    page_trace: list[dict] = []
    next_forums = dict(state.forums)
    next_metadata_hashes = set(state.torrent_metadata_hashes)
    next_metadata_pending = dict(state.torrent_metadata_pending)
    next_retry_queue = dict(state.topic_retry_queue)
    truncated = False
    torrserver_max_new = max(0, int(torrserver_max_new or 0))
    torrserver_replay_successes = max(1, int(torrserver_replay_successes or 1))
    metadata_parallelism = max(1, min(2, int(getattr(torrserver, "size", 1) or 1)))
    unconfirmed_attempted = 0
    metadata_stats: dict = {
        "attempted": 0,
        "enriched": 0,
        "confirmed": 0,
        "replay_pending": 0,
        "known": 0,
        "deferred": 0,
        "failed": 0,
        "servers": [],
        "failovers": 0,
    }
    retry_stats = {
        "attempted": 0,
        "resolved": 0,
        "failed": 0,
        "permanent_rejected": 0,
        "newly_queued": 0,
        "pending": len(next_retry_queue),
    }
    active_metadata_tasks: dict[str, asyncio.Task[ParsedTorrent]] = {}

    def handle_topic_failure(row: TrackerRow, exc: Exception, *, from_retry: bool) -> None:
        if isinstance(exc, _PermanentTopicReject):
            next_retry_queue.pop(row.topic_id, None)
            if from_retry:
                retry_stats["permanent_rejected"] += 1
            _append_rejection(rejected, row, exc)
            return
        if torrserver is not None:
            is_new = _queue_retry(next_retry_queue, row, error=str(exc), increment_attempt=True)
            if is_new:
                retry_stats["newly_queued"] += 1
            if from_retry:
                retry_stats["failed"] += 1
            _append_rejection(rejected, row, exc, retry_queued=True)
            return
        _append_rejection(rejected, row, exc)

    async def prepare_metadata(
        row: TrackerRow,
        *,
        forum_key: str,
        from_retry: bool,
    ) -> _PendingMetadata:
        nonlocal unconfirmed_attempted
        topic_url = row.topic_url or parser.topic_url(row.topic_id)
        topic_html = await parser.get_html(topic_url)
        book = parse_topic_html(topic_html, topic_url, parser.base_url)
        torrent_ref = book.torrent or ParsedTorrent(
            info_hash="",
            torrent_url=row.torrent_url,
        )
        torrent_ref.seeders = row.seeders
        torrent_ref.leechers = row.leechers
        if not torrent_ref.total_size_bytes and row.size_bytes:
            torrent_ref.total_size_bytes = row.size_bytes

        info_hash = (torrent_ref.info_hash or "").strip().lower()
        if not info_hash:
            raise RuntimeError("topic has no usable info_hash")

        was_known = info_hash in next_metadata_hashes
        if (
            not was_known
            and torrserver_max_new
            and unconfirmed_attempted >= torrserver_max_new
        ):
            metadata_stats["deferred"] += 1
            raise RuntimeError("torrent metadata deferred by torrserver_max_new")

        metadata_stats["attempted"] += 1
        if not was_known:
            unconfirmed_attempted += 1

        task = active_metadata_tasks.get(info_hash)
        if task is None:
            assert torrserver is not None
            task = asyncio.create_task(
                torrserver.ensure_metadata(info_hash, torrent_ref.magnet_uri)
            )
            active_metadata_tasks[info_hash] = task
        return _PendingMetadata(
            row=row,
            forum_key=forum_key,
            book=book,
            torrent_ref=torrent_ref,
            info_hash=info_hash,
            was_known=was_known,
            from_retry=from_retry,
            task=task,
        )

    async def finish_metadata(item: _PendingMetadata) -> None:
        try:
            try:
                torrent = await item.task
            finally:
                if active_metadata_tasks.get(item.info_hash) is item.task:
                    active_metadata_tasks.pop(item.info_hash, None)
            torrent.torrent_url = (
                item.row.torrent_url
                or parser.torrent_url(item.row.topic_id)
            )
            torrent.seeders = item.row.seeders
            torrent.leechers = item.row.leechers
            if item.torrent_ref.info_hash and torrent.info_hash != item.info_hash:
                raise RuntimeError(
                    "magnet/TorrServer info_hash mismatch: "
                    f"{item.torrent_ref.info_hash} != {torrent.info_hash}"
                )
            if not any(entry.media_type == "audio" for entry in torrent.files):
                raise _PermanentTopicReject(
                    "RuTracker torrent metadata has no supported audio files: "
                    f"{item.book.external_url}",
                    reason="rutracker_unsupported_audio",
                )
            book = hydrate_book_from_torrent(item.book, torrent)
            _assert_torrserver_ready(book)
            metadata_stats["enriched"] += 1

            retry_still_needed = False
            if item.was_known:
                torrent_status = "torrent_metadata_known"
                metadata_stats["known"] += 1
            else:
                successes = next_metadata_pending.get(item.info_hash, 0) + 1
                if successes >= torrserver_replay_successes:
                    next_metadata_pending.pop(item.info_hash, None)
                    next_metadata_hashes.add(item.info_hash)
                    torrent_status = "torrent_metainfo_confirmed"
                    metadata_stats["confirmed"] += 1
                else:
                    next_metadata_pending[item.info_hash] = successes
                    torrent_status = "torrent_metainfo_replay_pending"
                    metadata_stats["replay_pending"] += 1
                    retry_still_needed = True
                    is_new = _queue_retry(
                        next_retry_queue,
                        item.row,
                        error="",
                        increment_attempt=False,
                    )
                    if is_new:
                        retry_stats["newly_queued"] += 1

            _append_record(
                records,
                book=book,
                row=item.row,
                forum_key=item.forum_key,
                torrent_status=torrent_status,
                metadata_attempted=True,
            )

            if not retry_still_needed:
                removed = next_retry_queue.pop(item.row.topic_id, None)
                if item.from_retry and removed is not None:
                    retry_stats["resolved"] += 1
        except _PermanentTopicReject as exc:
            metadata_stats["failed"] += 1
            handle_topic_failure(item.row, exc, from_retry=item.from_retry)
        except Exception as exc:
            metadata_stats["failed"] += 1
            handle_topic_failure(
                item.row,
                RuntimeError(
                    f"TorrServer metadata is required for RuTracker feed: {exc}"
                ),
                from_retry=item.from_retry,
            )

    # Retry queued topics directly before normal page traversal. This decouples
    # transient topic/TorrServer failures from the deep-page cursor: the cursor
    # can continue while the failed topic remains durable in state.
    if torrserver is not None:
        retry_pending: list[_PendingMetadata] = []
        for retry in list(state.topic_retry_queue.values()):
            row = retry.to_row()
            if not row.topic_url:
                row.topic_url = parser.topic_url(row.topic_id)
            if not row.torrent_url:
                row.torrent_url = parser.torrent_url(row.topic_id)
            seen_topics.add(row.topic_id)
            retry_stats["attempted"] += 1
            try:
                item = await prepare_metadata(
                    row,
                    forum_key=row.forum_id,
                    from_retry=True,
                )
            except Exception as exc:
                handle_topic_failure(row, exc, from_retry=True)
            else:
                retry_pending.append(item)
                if len(retry_pending) >= metadata_parallelism:
                    await finish_metadata(retry_pending.pop(0))
        while retry_pending:
            await finish_metadata(retry_pending.pop(0))

    for forum_id in forum_ids:
        forum_key = str(forum_id)
        before = state.forums.get(forum_key, ForumCursor())

        first_url = parser.forum_url(forum_id, 1)
        first_html = await parser.get_html(first_url)
        last_page = detect_last_forum_page(
            first_html,
            forum_id=forum_id,
            page_size=parser.page_size,
        )
        if last_page == 1 and before.last_page and before.last_page > 1:
            last_page = before.last_page

        pages, next_deep, backfill_complete = plan_pages(
            last_page=last_page,
            deep_page=before.deep_page,
            backfill_pages=backfill_pages,
            backfill_complete=before.backfill_complete,
        )
        page_trace.append({
            "forum_id": forum_id,
            "pages": pages,
            "last_page": last_page,
            "backfill_complete": backfill_complete,
        })

        for page in pages:
            html = (
                first_html
                if page == 1
                else await parser.get_html(parser.forum_url(forum_id, page))
            )
            rows = parse_forum_html(html, parser.base_url, forum_id)
            pending_metadata: list[_PendingMetadata] = []

            for row in rows:
                if row.topic_id in seen_topics:
                    continue
                if max_topics and len(seen_topics) >= max_topics:
                    truncated = True
                    break
                seen_topics.add(row.topic_id)

                if torrserver is not None:
                    try:
                        item = await prepare_metadata(
                            row,
                            forum_key=forum_key,
                            from_retry=False,
                        )
                    except Exception as exc:
                        handle_topic_failure(row, exc, from_retry=False)
                    else:
                        pending_metadata.append(item)
                        if len(pending_metadata) >= metadata_parallelism:
                            await finish_metadata(pending_metadata.pop(0))
                    continue

                try:
                    # RuTracker/Worker HTTP remains sequential. Only metadata
                    # resolution above is overlapped between up to two workers.
                    topic_html = await parser.get_html(parser.topic_url(row.topic_id))
                    book = parse_topic_html(
                        topic_html,
                        parser.topic_url(row.topic_id),
                        parser.base_url,
                    )
                    torrent_ref = book.torrent or ParsedTorrent(
                        info_hash="",
                        torrent_url=row.torrent_url,
                    )
                    torrent_ref.seeders = row.seeders
                    torrent_ref.leechers = row.leechers
                    if not torrent_ref.total_size_bytes and row.size_bytes:
                        torrent_ref.total_size_bytes = row.size_bytes

                    info_hash = (torrent_ref.info_hash or "").strip().lower()
                    if not info_hash:
                        raise RuntimeError("topic has no usable info_hash")

                    torrent_status = "magnet"
                    torrent_error = ""
                    metadata_attempted = False

                    if download_torrents:
                        metadata_attempted = True
                        try:
                            raw_torrent = await parser.get_torrent(
                                row.torrent_url or parser.torrent_url(row.topic_id),
                                referer=parser.topic_url(row.topic_id),
                            )
                            torrent = parse_torrent_bytes(
                                raw_torrent,
                                magnet_uri=torrent_ref.magnet_uri,
                                torrent_url=(
                                    row.torrent_url
                                    or parser.torrent_url(row.topic_id)
                                ),
                            )
                            torrent.seeders = row.seeders
                            torrent.leechers = row.leechers
                            if (
                                torrent_ref.info_hash
                                and torrent.info_hash != torrent_ref.info_hash
                            ):
                                raise RuntimeError(
                                    "magnet/torrent info_hash mismatch: "
                                    f"{torrent_ref.info_hash} != {torrent.info_hash}"
                                )
                            book = hydrate_book_from_torrent(book, torrent)
                            torrent_status = "torrent_metainfo"
                        except Exception as exc:
                            if not torrent_ref.info_hash:
                                raise
                            book.torrent = torrent_ref
                            torrent_status = "magnet_fallback"
                            torrent_error = str(exc)[:500]
                    else:
                        book.torrent = torrent_ref

                    _append_record(
                        records,
                        book=book,
                        row=row,
                        forum_key=forum_key,
                        torrent_status=torrent_status,
                        metadata_attempted=metadata_attempted,
                        torrent_error=torrent_error,
                    )
                except Exception as exc:
                    _append_rejection(rejected, row, exc)

            while pending_metadata:
                await finish_metadata(pending_metadata.pop(0))

            if truncated:
                break
        if truncated:
            break

        if advance_cursor:
            next_forums[forum_key] = ForumCursor(
                deep_page=next_deep,
                last_page=last_page,
                backfill_complete=backfill_complete,
            )

    if torrserver is not None:
        statistics = getattr(torrserver, "statistics", None)
        if callable(statistics):
            pool_stats = statistics()
            metadata_stats["servers"] = list(pool_stats.get("servers") or [])
            metadata_stats["failovers"] = int(pool_stats.get("failovers") or 0)

    if truncated or not advance_cursor:
        next_forums = dict(state.forums)
        next_metadata_hashes = set(state.torrent_metadata_hashes)
        next_metadata_pending = dict(state.torrent_metadata_pending)
        next_retry_queue = dict(state.topic_retry_queue)

    retry_stats["pending"] = len(next_retry_queue)
    metadata_stats["retry_queue"] = retry_stats
    next_state = RuTrackerState(
        forums=next_forums,
        torrent_metadata_hashes=next_metadata_hashes,
        torrent_metadata_pending=next_metadata_pending,
        topic_retry_queue=next_retry_queue,
    )
    return {
        "pages": page_trace,
        "records": records,
        "tombstones": tombstones,
        "rejected": rejected,
        "cursor_before": state.as_dict(),
        "cursor_after": next_state.as_dict(),
        "truncated": truncated,
        "cursor_held_for_metadata": False,
        "cursor_advanced": bool(advance_cursor and not truncated),
        "topics_seen": len(seen_topics),
        "torrent_metadata": metadata_stats,
    }, next_state
