import json

import httpx
import pytest

from abred_catalog_pipeline.models import ParsedBook, ParsedTorrent, ParsedTorrentFile
from abred_catalog_pipeline.rutracker.parser import TrackerRow
from abred_catalog_pipeline.rutracker.torrserver import TorrServerClient


HASH = "0123456789abcdef0123456789abcdef01234567"
HASH2 = "89abcdef0123456789abcdef0123456789abcdef"
MAGNET = f"magnet:?xt=urn:btih:{HASH}"


@pytest.mark.asyncio
async def test_existing_torrent_metadata_uses_zero_based_abred_indexes_without_mutation():
    actions = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        actions.append(payload)
        assert payload == {"action": "get", "hash": HASH}
        return httpx.Response(200, json={
            "hash": HASH,
            "stat_string": "Torrent working",
            "file_stats": [
                # TorrServer web IDs are one-based. Abred feed indexes stay
                # zero-based and are resolved by file path at playback time.
                {"id": 1, "path": "Book/01.mp3", "length": 1000},
                {"id": 2, "path": "Book/cover.jpg", "length": 200},
            ],
        })

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = TorrServerClient(
        base_url="https://torr.example",
        timeout_seconds=1,
        poll_interval_seconds=0,
        client=http,
    )
    try:
        torrent = await client.ensure_metadata(HASH, MAGNET)
    finally:
        await http.aclose()

    assert [item.index for item in torrent.files] == [0, 1]
    assert [item.media_type for item in torrent.files] == ["audio", "other"]
    assert torrent.total_size_bytes == 1200
    assert [item["action"] for item in actions] == ["get"]


@pytest.mark.asyncio
async def test_missing_torrent_is_added_ephemerally_without_destructive_cleanup():
    actions = []
    get_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_calls
        payload = json.loads(request.content)
        actions.append(payload)
        if payload["action"] == "get":
            get_calls += 1
            if get_calls == 1:
                return httpx.Response(404)
            return httpx.Response(200, json={
                "hash": HASH,
                "file_stats": [
                    {"id": 1, "path": "01.mp3", "length": 1000},
                    {"id": 2, "path": "02.mp3", "length": 2000},
                ],
            })
        if payload["action"] == "add":
            assert payload["link"] == MAGNET
            assert payload["save_to_db"] is False
            return httpx.Response(200, json={"hash": HASH, "stat_string": "Torrent getting info"})
        raise AssertionError(payload)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = TorrServerClient(
        base_url="https://torr.example",
        timeout_seconds=1,
        poll_interval_seconds=0,
        client=http,
    )
    try:
        torrent = await client.ensure_metadata(HASH, MAGNET)
    finally:
        await http.aclose()

    assert [item.index for item in torrent.files] == [0, 1]
    assert [item["action"] for item in actions] == ["get", "add", "get"]
    assert not any(item["action"] in {"rem", "drop", "wipe"} for item in actions)


def test_rutracker_state_persists_confirmed_and_pending_metadata_without_feed_cursor_bloat(tmp_path):
    from abred_catalog_pipeline.rutracker.crawler import RuTrackerState

    path = tmp_path / "rutracker.json"
    state = RuTrackerState(
        torrent_metadata_hashes={HASH},
        torrent_metadata_pending={HASH2: 1},
    )
    state.save(path)

    raw = json.loads(path.read_text())
    assert raw["torrent_metadata_hashes"] == [HASH]
    assert raw["torrent_metadata_pending"] == {HASH2: 1}

    loaded = RuTrackerState.load(path)
    assert loaded.torrent_metadata_hashes == {HASH}
    assert loaded.torrent_metadata_pending == {HASH2: 1}
    assert "torrent_metadata_hashes" not in loaded.as_dict()
    assert "torrent_metadata_pending" not in loaded.as_dict()


class _CrawlerProbeParser:
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


class _CrawlerProbeTorrServer:
    def __init__(self):
        self.calls = []

    async def ensure_metadata(self, info_hash: str, magnet_uri: str) -> ParsedTorrent:
        self.calls.append(info_hash)
        return ParsedTorrent(
            info_hash=info_hash,
            magnet_uri=magnet_uri,
            total_size_bytes=1000,
            files=[
                ParsedTorrentFile(
                    index=0,
                    path=f"{info_hash[:8]}.mp3",
                    size_bytes=1000,
                    media_type="audio",
                )
            ],
        )


def _patch_deep_page_crawl(monkeypatch, rows):
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
    return crawler


@pytest.mark.asyncio
async def test_deferred_deep_metadata_holds_cursor_but_keeps_successful_cache(monkeypatch):
    from abred_catalog_pipeline.rutracker.crawler import ForumCursor, RuTrackerState

    rows = [
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
    crawler = _patch_deep_page_crawl(monkeypatch, rows)
    state = RuTrackerState(
        forums={"2387": ForumCursor(deep_page=5, last_page=10, backfill_complete=False)}
    )
    torrserver = _CrawlerProbeTorrServer()

    result, next_state = await crawler.crawl_once(
        _CrawlerProbeParser(),
        state,
        forum_ids=(2387,),
        torrserver=torrserver,
        torrserver_max_new=1,
        torrserver_replay_successes=1,
        advance_cursor=True,
    )

    assert torrserver.calls == [HASH]
    assert result["torrent_metadata"]["confirmed"] == 1
    assert result["torrent_metadata"]["deferred"] == 1
    assert result["cursor_held_for_metadata"] is True
    assert result["cursor_advanced"] is False
    assert next_state.forums == state.forums
    assert next_state.torrent_metadata_hashes == {HASH}


@pytest.mark.asyncio
async def test_deep_metadata_replays_before_cursor_advances(monkeypatch):
    from abred_catalog_pipeline.rutracker.crawler import ForumCursor, RuTrackerState

    rows = [
        TrackerRow(
            topic_id="1",
            topic_url="https://rutracker.org/forum/viewtopic.php?t=1",
            torrent_url="https://rutracker.org/forum/dl.php?t=1",
            title=HASH,
            forum_id="2387",
        ),
    ]
    crawler = _patch_deep_page_crawl(monkeypatch, rows)
    initial = RuTrackerState(
        forums={"2387": ForumCursor(deep_page=5, last_page=10, backfill_complete=False)}
    )

    first_torrserver = _CrawlerProbeTorrServer()
    first_result, first_state = await crawler.crawl_once(
        _CrawlerProbeParser(),
        initial,
        forum_ids=(2387,),
        torrserver=first_torrserver,
        torrserver_max_new=1,
        torrserver_replay_successes=2,
        advance_cursor=True,
    )

    assert first_result["torrent_metadata"]["replay_pending"] == 1
    assert first_result["cursor_held_for_metadata"] is True
    assert first_state.forums == initial.forums
    assert first_state.torrent_metadata_pending == {HASH: 1}

    second_torrserver = _CrawlerProbeTorrServer()
    second_result, second_state = await crawler.crawl_once(
        _CrawlerProbeParser(),
        first_state,
        forum_ids=(2387,),
        torrserver=second_torrserver,
        torrserver_max_new=1,
        torrserver_replay_successes=2,
        advance_cursor=True,
    )

    assert second_result["torrent_metadata"]["confirmed"] == 1
    assert second_result["cursor_held_for_metadata"] is False
    assert second_result["cursor_advanced"] is True
    assert second_state.forums["2387"].deep_page == 4
    assert second_state.torrent_metadata_hashes == {HASH}
    assert second_state.torrent_metadata_pending == {}
