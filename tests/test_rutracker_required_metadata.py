import pytest

from abred_catalog_pipeline.models import ParsedBook, ParsedTorrent, ParsedTorrentFile
from abred_catalog_pipeline.rutracker.crawler import ForumCursor, RuTrackerState, crawl_once
from abred_catalog_pipeline.rutracker.parser import TrackerRow


HASH = "0123456789abcdef0123456789abcdef01234567"
HASH2 = "89abcdef0123456789abcdef0123456789abcdef"


class _Parser:
    base_url = "https://rutracker.org"
    page_size = 50

    def forum_url(self, forum_id: int, page: int) -> str:
        return f"forum:{forum_id}:{page}"

    def topic_url(self, topic_id: str) -> str:
        return f"https://rutracker.org/forum/viewtopic.php?t={topic_id}"

    def torrent_url(self, topic_id: str) -> str:
        return f"https://rutracker.org/forum/dl.php?t={topic_id}"

    async def get_html(self, url: str) -> str:
        return url


class _TorrServer:
    def __init__(self, *, fail_hash: str = ""):
        self.calls: list[str] = []
        self.fail_hash = fail_hash

    async def ensure_metadata(self, info_hash: str, magnet_uri: str) -> ParsedTorrent:
        self.calls.append(info_hash)
        if info_hash == self.fail_hash:
            raise RuntimeError("metadata unavailable")
        return ParsedTorrent(
            info_hash=info_hash,
            magnet_uri=magnet_uri,
            total_size_bytes=1000,
            files=[
                ParsedTorrentFile(
                    index=0,
                    path=f"Book/{info_hash[:8]}.mp3",
                    size_bytes=1000,
                    media_type="audio",
                )
            ],
        )


def _patch(monkeypatch, rows):
    from abred_catalog_pipeline.rutracker import crawler

    monkeypatch.setattr(
        crawler,
        "detect_last_forum_page",
        lambda html, *, forum_id, page_size: 10,
    )
    monkeypatch.setattr(
        crawler,
        "plan_pages",
        lambda **kwargs: ([5], 4, False),
    )
    monkeypatch.setattr(
        crawler,
        "parse_forum_html",
        lambda html, base_url, forum_id: list(rows),
    )
    hashes = {row.topic_id: row.title for row in rows}

    def parse_topic_html(html, topic_url, base_url):
        topic_id = topic_url.rsplit("=", 1)[-1]
        info_hash = hashes[topic_id]
        return ParsedBook(
            external_id=topic_id,
            external_url=topic_url,
            title=f"Book {topic_id}",
            torrent=ParsedTorrent(
                info_hash=info_hash,
                magnet_uri=f"magnet:?xt=urn:btih:{info_hash}",
                torrent_url=f"https://rutracker.org/forum/dl.php?t={topic_id}",
            ),
        )

    monkeypatch.setattr(crawler, "parse_topic_html", parse_topic_html)


def _rows():
    return [
        TrackerRow(
            topic_id="1",
            topic_url="https://rutracker.org/forum/viewtopic.php?t=1",
            torrent_url="https://rutracker.org/forum/dl.php?t=1",
            title=HASH,
            forum_id="2387",
        ),
        TrackerRow(
            topic_id="2",
            topic_url="https://rutracker.org/forum/viewtopic.php?t=2",
            torrent_url="https://rutracker.org/forum/dl.php?t=2",
            title=HASH2,
            forum_id="2387",
        ),
    ]


@pytest.mark.asyncio
async def test_unlimited_torrserver_enrichment_sends_every_hash_and_builds_chapters(monkeypatch):
    rows = _rows()
    _patch(monkeypatch, rows)
    torrserver = _TorrServer()

    result, state = await crawl_once(
        _Parser(),
        RuTrackerState(
            forums={"2387": ForumCursor(deep_page=5, last_page=10)},
        ),
        forum_ids=(2387,),
        torrserver=torrserver,
        torrserver_max_new=0,
        torrserver_replay_successes=1,
        advance_cursor=True,
    )

    assert torrserver.calls == [HASH, HASH2]
    assert result["torrent_metadata"]["deferred"] == 0
    assert result["torrent_metadata"]["confirmed"] == 2
    assert result["rejected"] == []
    assert len(result["records"]) == 2
    for record in result["records"]:
        assert record["torrent"]["info_hash"]
        assert len(record["torrent"]["files"]) == 1
        assert len(record["chapters"]) == 1
        assert record["chapters"][0]["media_url"].startswith(
            f"torrent://{record['torrent']['info_hash']}/"
        )
    assert state.forums["2387"].deep_page == 4


@pytest.mark.asyncio
async def test_known_hash_is_still_resolved_so_chapters_never_disappear(monkeypatch):
    row = _rows()[0]
    _patch(monkeypatch, [row])
    torrserver = _TorrServer()

    result, _ = await crawl_once(
        _Parser(),
        RuTrackerState(
            forums={"2387": ForumCursor(deep_page=5, last_page=10)},
            torrent_metadata_hashes={HASH},
        ),
        forum_ids=(2387,),
        torrserver=torrserver,
        torrserver_max_new=0,
        torrserver_replay_successes=1,
        advance_cursor=True,
    )

    assert torrserver.calls == [HASH]
    assert result["torrent_metadata"]["known"] == 1
    assert len(result["records"]) == 1
    assert len(result["records"][0]["torrent"]["files"]) == 1
    assert len(result["records"][0]["chapters"]) == 1


@pytest.mark.asyncio
async def test_missing_required_metadata_never_emits_incomplete_record_and_holds_page(monkeypatch):
    row = _rows()[0]
    _patch(monkeypatch, [row])
    torrserver = _TorrServer(fail_hash=HASH)
    initial = RuTrackerState(
        forums={"2387": ForumCursor(deep_page=5, last_page=10)},
    )

    result, state = await crawl_once(
        _Parser(),
        initial,
        forum_ids=(2387,),
        torrserver=torrserver,
        torrserver_max_new=0,
        torrserver_replay_successes=1,
        advance_cursor=True,
    )

    assert result["records"] == []
    assert len(result["rejected"]) == 1
    assert "TorrServer metadata is required" in result["rejected"][0]["detail"]
    assert result["cursor_held_for_metadata"] is True
    assert state.forums["2387"].deep_page == 5
