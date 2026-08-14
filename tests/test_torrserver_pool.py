import asyncio

import httpx
import pytest

from abred_catalog_pipeline.cli import build_parser
from abred_catalog_pipeline.models import ParsedBook, ParsedTorrent, ParsedTorrentFile
from abred_catalog_pipeline.rutracker.parser import TrackerRow
from abred_catalog_pipeline.rutracker.torrserver import (
    TorrServerMetadataError,
    TorrServerPool,
    TorrServerTransientError,
)


HASH0 = "0000000000000000000000000000000000000000"
HASH1 = "0000000000000000000000000000000000000001"


def _torrent(info_hash: str) -> ParsedTorrent:
    return ParsedTorrent(
        info_hash=info_hash,
        magnet_uri=f"magnet:?xt=urn:btih:{info_hash}",
        total_size_bytes=1000,
        files=[
            ParsedTorrentFile(
                index=0,
                path=f"{info_hash[-8:]}.mp3",
                size_bytes=1000,
                media_type="audio",
            )
        ],
    )


class _PoolProbeClient:
    def __init__(self, *, error: Exception | None = None, release: asyncio.Event | None = None):
        self.error = error
        self.release = release
        self.calls: list[str] = []
        self.started = asyncio.Event()

    async def ensure_metadata(self, info_hash: str, magnet_uri: str) -> ParsedTorrent:
        self.calls.append(info_hash)
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        if self.error is not None:
            raise self.error
        return _torrent(info_hash)

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_pool_runs_two_different_hashes_on_two_servers_in_parallel():
    release = asyncio.Event()
    first = _PoolProbeClient(release=release)
    second = _PoolProbeClient(release=release)
    pool = TorrServerPool([first, second])

    task0 = asyncio.create_task(pool.ensure_metadata(HASH0, f"magnet:?xt=urn:btih:{HASH0}"))
    task1 = asyncio.create_task(pool.ensure_metadata(HASH1, f"magnet:?xt=urn:btih:{HASH1}"))
    await asyncio.wait_for(
        asyncio.gather(first.started.wait(), second.started.wait()),
        timeout=1,
    )
    release.set()
    await asyncio.gather(task0, task1)

    assert len(first.calls) == 1
    assert len(second.calls) == 1
    stats = pool.statistics()
    assert [item["enriched"] for item in stats["servers"]] == [1, 1]
    assert stats["failovers"] == 0


@pytest.mark.asyncio
async def test_transient_failure_fails_over_once_to_second_server():
    first = _PoolProbeClient(error=TorrServerTransientError("temporary timeout"))
    second = _PoolProbeClient()
    pool = TorrServerPool([first, second])

    result = await pool.ensure_metadata(HASH0, f"magnet:?xt=urn:btih:{HASH0}")

    assert result.info_hash == HASH0
    assert first.calls == [HASH0]
    assert second.calls == [HASH0]
    stats = pool.statistics()
    assert stats["failovers"] == 1
    assert stats["servers"][0]["attempted"] == 1
    assert stats["servers"][0]["failed"] == 1
    assert stats["servers"][1]["attempted"] == 1
    assert stats["servers"][1]["enriched"] == 1


@pytest.mark.asyncio
async def test_structural_metadata_failure_does_not_fail_over():
    first = _PoolProbeClient(error=TorrServerMetadataError("hash mismatch"))
    second = _PoolProbeClient()
    pool = TorrServerPool([first, second])

    with pytest.raises(TorrServerMetadataError, match="hash mismatch"):
        await pool.ensure_metadata(HASH0, f"magnet:?xt=urn:btih:{HASH0}")

    assert first.calls == [HASH0]
    assert second.calls == []
    assert pool.statistics()["failovers"] == 0


@pytest.mark.asyncio
async def test_http_503_is_transient_and_uses_failover():
    request = httpx.Request("POST", "https://torr-1.example/torrents")
    response = httpx.Response(503, request=request)
    error = httpx.HTTPStatusError("service unavailable", request=request, response=response)
    first = _PoolProbeClient(error=error)
    second = _PoolProbeClient()
    pool = TorrServerPool([first, second])

    result = await pool.ensure_metadata(HASH0, f"magnet:?xt=urn:btih:{HASH0}")

    assert result.info_hash == HASH0
    assert pool.statistics()["failovers"] == 1


def test_cli_reads_second_torrserver_url_from_environment(monkeypatch):
    monkeypatch.setenv("TORRSERVER_URL", "https://torr-1.example")
    monkeypatch.setenv("TORRSERVER_URL_2", "https://torr-2.example")
    monkeypatch.setenv("TORRSERVER_USERNAME", "shared-user")
    monkeypatch.setenv("TORRSERVER_PASSWORD", "shared-pass")

    args = build_parser().parse_args(["run-rutracker", "--torrserver-enrich"])

    assert args.torrserver_url == "https://torr-1.example"
    assert args.torrserver_url_2 == "https://torr-2.example"
    assert args.torrserver_username == "shared-user"
    assert args.torrserver_password == "shared-pass"


class _CrawlerParser:
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


class _ConcurrentCrawlerTorrServer:
    size = 2

    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.calls: list[str] = []

    async def ensure_metadata(self, info_hash: str, magnet_uri: str) -> ParsedTorrent:
        self.calls.append(info_hash)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.02)
            return _torrent(info_hash)
        finally:
            self.active -= 1

    def statistics(self) -> dict:
        return {
            "servers": [
                {"server": 1, "attempted": 1, "enriched": 1, "failed": 0, "in_flight": 0},
                {"server": 2, "attempted": 1, "enriched": 1, "failed": 0, "in_flight": 0},
            ],
            "failovers": 0,
        }


@pytest.mark.asyncio
async def test_crawler_overlaps_two_torrserver_metadata_jobs(monkeypatch):
    from abred_catalog_pipeline.rutracker import crawler

    rows = [
        TrackerRow(
            topic_id="1",
            topic_url="https://rutracker.org/forum/viewtopic.php?t=1",
            torrent_url="https://rutracker.org/forum/dl.php?t=1",
            title=HASH0,
            forum_id="2387",
        ),
        TrackerRow(
            topic_id="2",
            topic_url="https://rutracker.org/forum/viewtopic.php?t=2",
            torrent_url="https://rutracker.org/forum/dl.php?t=2",
            title=HASH1,
            forum_id="2387",
        ),
    ]
    monkeypatch.setattr(
        crawler,
        "detect_last_forum_page",
        lambda html, *, forum_id, page_size: 1,
    )
    monkeypatch.setattr(crawler, "plan_pages", lambda **kwargs: ([1], None, True))
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
    torrserver = _ConcurrentCrawlerTorrServer()

    result, _ = await crawler.crawl_once(
        _CrawlerParser(),
        crawler.RuTrackerState(),
        forum_ids=(2387,),
        torrserver=torrserver,
        torrserver_replay_successes=1,
        advance_cursor=True,
    )

    assert torrserver.max_active == 2
    assert len(result["records"]) == 2
    assert result["torrent_metadata"]["servers"][0]["enriched"] == 1
    assert result["torrent_metadata"]["servers"][1]["enriched"] == 1
    assert result["torrent_metadata"]["failovers"] == 0
