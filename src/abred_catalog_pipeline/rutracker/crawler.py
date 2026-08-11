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


@dataclass(frozen=True, slots=True)
class ForumCursor:
    deep_page: int | None = None
    last_page: int | None = None
    backfill_complete: bool = False


@dataclass(slots=True)
class RuTrackerState:
    source: str = "rutracker"
    forums: dict[str, ForumCursor] = field(default_factory=dict)

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
        return cls(source="rutracker", forums=forums)

    def as_dict(self) -> dict:
        return {
            "source": "rutracker",
            "forums": {
                key: asdict(value)
                for key, value in sorted(self.forums.items(), key=lambda item: int(item[0]))
            },
        }

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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


async def crawl_once(
    parser: RuTrackerWorkerClient,
    state: RuTrackerState,
    *,
    forum_ids: tuple[int, ...],
    backfill_pages: int = 1,
    max_topics: int = 0,
    download_torrents: bool = False,
    advance_cursor: bool = True,
) -> tuple[dict, RuTrackerState]:
    records: list[dict] = []
    rejected: list[dict] = []
    tombstones: list[dict] = []
    seen_topics: set[str] = set()
    page_trace: list[dict] = []
    next_forums = dict(state.forums)
    truncated = False

    for forum_id in forum_ids:
        forum_key = str(forum_id)
        before = state.forums.get(forum_key, ForumCursor())

        first_url = parser.forum_url(forum_id, 1)
        first_html = await parser.get_html(first_url)
        last_page = detect_last_forum_page(first_html, forum_id=forum_id, page_size=parser.page_size)
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
            html = first_html if page == 1 else await parser.get_html(parser.forum_url(forum_id, page))
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
                    book = parse_topic_html(topic_html, parser.topic_url(row.topic_id), parser.base_url)
                    torrent_ref = book.torrent or ParsedTorrent(info_hash="", torrent_url=row.torrent_url)
                    torrent_ref.seeders = row.seeders
                    torrent_ref.leechers = row.leechers
                    if not torrent_ref.total_size_bytes and row.size_bytes:
                        torrent_ref.total_size_bytes = row.size_bytes

                    torrent_status = "magnet"
                    torrent_error = ""
                    if download_torrents:
                        try:
                            raw_torrent = await parser.get_torrent(
                                row.torrent_url or parser.torrent_url(row.topic_id),
                                referer=parser.topic_url(row.topic_id),
                            )
                            torrent = parse_torrent_bytes(
                                raw_torrent,
                                magnet_uri=torrent_ref.magnet_uri,
                                torrent_url=row.torrent_url or parser.torrent_url(row.topic_id),
                            )
                            torrent.seeders = row.seeders
                            torrent.leechers = row.leechers
                            if torrent_ref.info_hash and torrent.info_hash != torrent_ref.info_hash:
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
                        "torrent_metadata_attempted": bool(download_torrents),
                        "torrent_metadata_error": torrent_error or None,
                    }
                    if book.series_entries and record.get("series"):
                        record["series"][0]["entries"] = [asdict(entry) for entry in book.series_entries]
                    records.append(record)
                except Exception as exc:
                    rejected.append({
                        "source": "rutracker",
                        "external_id": row.topic_id,
                        "external_url": row.topic_url,
                        "reason": "rutracker_topic_rejected",
                        "detail": str(exc)[:500],
                        "non_blocking": False,
                    })
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

    if truncated or not advance_cursor:
        next_forums = dict(state.forums)

    next_state = RuTrackerState(forums=next_forums)
    return {
        "pages": page_trace,
        "records": records,
        "tombstones": tombstones,
        "rejected": rejected,
        "cursor_before": state.as_dict(),
        "cursor_after": next_state.as_dict(),
        "truncated": truncated,
        "topics_seen": len(seen_topics),
    }, next_state
