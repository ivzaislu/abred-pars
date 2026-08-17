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
    def __init__(self, *, fail_hash: str = "", unsupported_hash: str = ""):
        self.calls: list[str] = []
        self.fail_hash = fail_hash
        self.unsupported_hash = unsupported_hash

    async def ensure_metadata(self, info_hash: str, magnet_uri: str) -> ParsedTorrent:
        self.calls.append(info_hash)
        if info_hash == self.fail_hash:
            raise RuntimeError("metadata unavailable")
        media_type = "other" if info_hash == self.unsupported_hash else "audio"
        extension = "jpg" if media_type == "other" else "mp3"
        return ParsedTorrent(
            info_hash=info_hash,
            magnet_uri=magnet_uri,
            total_size_bytes=1000,
            files=[
                ParsedTorrentFile(
                    index=0,
                    path=f"Book/{info_hash[:8]}.{extension}",
                    size_bytes=1000,
                    media_type=media_type,
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
    assert result["torrent_metadata"]["retry_queue"]["pending"] == 0
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
async def test_missing_required_metadata_is_queued_without_holding_cursor(monkeypatch):
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
    rejected = result["rejected"][0]
    assert "TorrServer metadata is required" in rejected["detail"]
    assert rejected["reason"] == "rutracker_topic_retry_queued"
    assert rejected["retry_queued"] is True
    assert rejected["non_blocking"] is True
    assert result["cursor_held_for_metadata"] is False
    assert result["cursor_advanced"] is True
    assert state.forums["2387"].deep_page == 4
    assert state.topic_retry_queue["1"].attempts == 1
    assert result["torrent_metadata"]["retry_queue"]["pending"] == 1


@pytest.mark.asyncio
async def test_retry_queue_replays_topic_directly_and_removes_it_on_success(monkeypatch):
    row = _rows()[0]
    _patch(monkeypatch, [row])
    first_result, first_state = await crawl_once(
        _Parser(),
        RuTrackerState(
            forums={"2387": ForumCursor(deep_page=5, last_page=10)},
        ),
        forum_ids=(2387,),
        torrserver=_TorrServer(fail_hash=HASH),
        torrserver_replay_successes=1,
        advance_cursor=True,
    )
    assert first_result["torrent_metadata"]["retry_queue"]["pending"] == 1

    torrserver = _TorrServer()
    second_result, second_state = await crawl_once(
        _Parser(),
        first_state,
        forum_ids=(2387,),
        torrserver=torrserver,
        torrserver_replay_successes=1,
        advance_cursor=True,
    )

    # The queued topic is retried before page traversal and then skipped when
    # the same row is encountered on the page, so only one TorrServer call is made.
    assert torrserver.calls == [HASH]
    assert len(second_result["records"]) == 1
    assert second_state.topic_retry_queue == {}
    retry = second_result["torrent_metadata"]["retry_queue"]
    assert retry["attempted"] == 1
    assert retry["resolved"] == 1
    assert retry["pending"] == 0


@pytest.mark.asyncio
async def test_unsupported_audio_is_non_blocking_and_allows_cursor_to_advance(monkeypatch):
    row = _rows()[0]
    _patch(monkeypatch, [row])
    torrserver = _TorrServer(unsupported_hash=HASH)
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
    rejected = result["rejected"][0]
    assert rejected["reason"] == "rutracker_unsupported_audio"
    assert rejected["non_blocking"] is True
    assert rejected["retry_queued"] is False
    assert result["cursor_held_for_metadata"] is False
    assert result["cursor_advanced"] is True
    assert state.forums["2387"].deep_page == 4
    assert state.topic_retry_queue == {}


def test_retry_queue_is_persisted_in_state(tmp_path):
    from abred_catalog_pipeline.rutracker.crawler import RetryTopic

    path = tmp_path / "rutracker.json"
    state = RuTrackerState(
        topic_retry_queue={
            "1": RetryTopic(
                topic_id="1",
                topic_url="https://rutracker.org/forum/viewtopic.php?t=1",
                torrent_url="https://rutracker.org/forum/dl.php?t=1",
                forum_id="2387",
                attempts=3,
                last_error="metadata unavailable",
            )
        }
    )
    state.save(path)
    loaded = RuTrackerState.load(path)

    assert loaded.topic_retry_queue["1"].attempts == 3
    assert loaded.topic_retry_queue["1"].last_error == "metadata unavailable"
