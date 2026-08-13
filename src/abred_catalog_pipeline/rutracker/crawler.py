from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..cursor import plan_pages
from ..models import ParsedTorrent, book_to_feed_record
from .parser import (
    DEFAULT_AUDIOBOOK_FORUM_IDS,
    RuTrackerWorkerClient,
    detect_last_forum_page,
    hydrate_book_from_torrent,
    parse_forum_html,
    parse_topic_html,
    parse_torrent_bytes,
)
from .torrserver import TorrServerClient


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
        return cls(
            source="rutracker",
            forums=forums,
            torrent_metadata_hashes=hashes,
            torrent_metadata_pending=pending,
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


def _held_page(current: int | None, page: int) -> int:
    return page if current is None else max(current, page)


class _PermanentTopicReject(RuntimeError):
    """Content is permanently non-playable and must not freeze a forum cursor."""


def _assert_torrserver_ready(book) -> None:
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
            f"RuTracker torrent metadata has no supported audio files: {book.external_url}"
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


async def crawl_once(
    parser: RuTrackerWorkerClient,
    state: RuTrackerState,
    *,
    forum_ids: tuple[int, ...],
    backfill_pages: int = 1,
    max_topics: int = 0,
    download_torrents: bool = False,
    torrserver: TorrServerClient | None = None,
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
    truncated = False
    metadata_blocked_forums: set[str] = set()
    torrserver_max_new = max(0, int(torrserver_max_new or 0))
    torrserver_replay_successes = max(1, int(torrserver_replay_successes or 1))
    unconfirmed_attempted = 0
    metadata_stats = {
        "attempted": 0,
        "enriched": 0,
        "confirmed": 0,
        "replay_pending": 0,
        "known": 0,
        "deferred": 0,
        "failed": 0,
    }

    for forum_id in forum_ids:
        forum_key = str(forum_id)
        before = state.forums.get(forum_key, ForumCursor())
        forum_metadata_hold_page: int | None = None
        forum_metadata_blocked = False

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
            for row in rows:
                if row.topic_id in seen_topics:
                    continue
                if max_topics and len(seen_topics) >= max_topics:
                    truncated = True
                    break
                seen_topics.add(row.topic_id)
                try:
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

                    torrent_status = "magnet"
                    torrent_error = ""
                    metadata_attempted = False
                    info_hash = (torrent_ref.info_hash or "").strip().lower()

                    if not info_hash:
                        forum_metadata_blocked = True
                        if page != 1:
                            forum_metadata_hold_page = _held_page(
                                forum_metadata_hold_page,
                                page,
                            )
                        raise RuntimeError("topic has no usable info_hash")

                    if torrserver is not None:
                        was_known = info_hash in next_metadata_hashes

                        if (
                            not was_known
                            and torrserver_max_new
                            and unconfirmed_attempted >= torrserver_max_new
                        ):
                            metadata_stats["deferred"] += 1
                            forum_metadata_blocked = True
                            if page != 1:
                                forum_metadata_hold_page = _held_page(
                                    forum_metadata_hold_page,
                                    page,
                                )
                            raise RuntimeError(
                                "torrent metadata deferred by torrserver_max_new"
                            )

                        metadata_attempted = True
                        metadata_stats["attempted"] += 1
                        if not was_known:
                            unconfirmed_attempted += 1

                        try:
                            torrent = await torrserver.ensure_metadata(
                                info_hash,
                                torrent_ref.magnet_uri,
                            )
                            torrent.torrent_url = (
                                row.torrent_url
                                or parser.torrent_url(row.topic_id)
                            )
                            torrent.seeders = row.seeders
                            torrent.leechers = row.leechers
                            if torrent_ref.info_hash and torrent.info_hash != info_hash:
                                raise RuntimeError(
                                    f"magnet/TorrServer info_hash mismatch: "
                                    f"{torrent_ref.info_hash} != {torrent.info_hash}"
                                )
                            if not any(item.media_type == "audio" for item in torrent.files):
                                raise _PermanentTopicReject(
                                    f"RuTracker torrent metadata has no supported audio files: {book.external_url}"
                                )
                            book = hydrate_book_from_torrent(book, torrent)
                            _assert_torrserver_ready(book)
                            metadata_stats["enriched"] += 1

                            if was_known:
                                torrent_status = "torrent_metadata_known"
                                metadata_stats["known"] += 1
                            else:
                                successes = next_metadata_pending.get(info_hash, 0) + 1
                                if successes >= torrserver_replay_successes:
                                    next_metadata_pending.pop(info_hash, None)
                                    next_metadata_hashes.add(info_hash)
                                    torrent_status = "torrent_metainfo_confirmed"
                                    metadata_stats["confirmed"] += 1
                                else:
                                    next_metadata_pending[info_hash] = successes
                                    torrent_status = "torrent_metainfo_replay_pending"
                                    metadata_stats["replay_pending"] += 1
                                    if page != 1:
                                        forum_metadata_hold_page = _held_page(
                                            forum_metadata_hold_page,
                                            page,
                                        )
                        except _PermanentTopicReject:
                            metadata_stats["failed"] += 1
                            raise
                        except Exception as exc:
                            metadata_stats["failed"] += 1
                            forum_metadata_blocked = True
                            if page != 1:
                                forum_metadata_hold_page = _held_page(
                                    forum_metadata_hold_page,
                                    page,
                                )
                            raise RuntimeError(
                                f"TorrServer metadata is required for RuTracker feed: {exc}"
                            ) from exc

                    elif download_torrents:
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
                                    f"magnet/torrent info_hash mismatch: "
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
                except Exception as exc:
                    permanent_reject = isinstance(exc, _PermanentTopicReject)
                    rejected.append({
                        "source": "rutracker",
                        "external_id": row.topic_id,
                        "external_url": row.topic_url,
                        "reason": (
                            "rutracker_unsupported_audio"
                            if permanent_reject
                            else "rutracker_topic_rejected"
                        ),
                        "detail": str(exc)[:500],
                        "non_blocking": permanent_reject,
                    })
            if truncated:
                break
        if truncated:
            break

        if advance_cursor:
            if forum_metadata_hold_page is not None:
                metadata_blocked_forums.add(forum_key)
                next_forums[forum_key] = ForumCursor(
                    deep_page=forum_metadata_hold_page,
                    last_page=last_page,
                    backfill_complete=False,
                )
            elif forum_metadata_blocked:
                metadata_blocked_forums.add(forum_key)
                next_forums[forum_key] = ForumCursor(
                    deep_page=before.deep_page,
                    last_page=last_page,
                    backfill_complete=before.backfill_complete,
                )
            else:
                next_forums[forum_key] = ForumCursor(
                    deep_page=next_deep,
                    last_page=last_page,
                    backfill_complete=backfill_complete,
                )

    cursor_held_for_metadata = bool(
        advance_cursor and torrserver is not None and metadata_blocked_forums
    )

    if truncated or not advance_cursor:
        next_forums = dict(state.forums)
        next_metadata_hashes = set(state.torrent_metadata_hashes)
        next_metadata_pending = dict(state.torrent_metadata_pending)

    next_state = RuTrackerState(
        forums=next_forums,
        torrent_metadata_hashes=next_metadata_hashes,
        torrent_metadata_pending=next_metadata_pending,
    )
    return {
        "pages": page_trace,
        "records": records,
        "tombstones": tombstones,
        "rejected": rejected,
        "cursor_before": state.as_dict(),
        "cursor_after": next_state.as_dict(),
        "truncated": truncated,
        "cursor_held_for_metadata": cursor_held_for_metadata,
        "cursor_advanced": bool(
            advance_cursor and not truncated and not cursor_held_for_metadata
        ),
        "topics_seen": len(seen_topics),
        "torrent_metadata": metadata_stats,
    }, next_state