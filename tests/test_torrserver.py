import json

import httpx
import pytest

from abred_catalog_pipeline.rutracker.torrserver import TorrServerClient


HASH = "0123456789abcdef0123456789abcdef01234567"
MAGNET = f"magnet:?xt=urn:btih:{HASH}"


@pytest.mark.asyncio
async def test_existing_torrent_metadata_is_read_without_mutation():
    actions = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        actions.append(payload)
        assert payload == {"action": "get", "hash": HASH}
        return httpx.Response(200, json={
            "hash": HASH,
            "stat_string": "Torrent working",
            "file_stats": [
                {"path": "Book/01.mp3", "length": 1000},  # id=0 is omitted by TorrServer
                {"id": 1, "path": "Book/cover.jpg", "length": 200},
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
async def test_missing_torrent_is_added_ephemerally_and_never_removed():
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
                    {"path": "01.mp3", "length": 1000},
                    {"id": 1, "path": "02.mp3", "length": 2000},
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

    assert len(torrent.files) == 2
    assert [item["action"] for item in actions] == ["get", "add", "get"]
    assert not any(item["action"] in {"rem", "drop", "wipe"} for item in actions)


def test_rutracker_state_persists_metadata_hash_cache_without_feed_cursor_bloat(tmp_path):
    from abred_catalog_pipeline.rutracker.crawler import RuTrackerState

    path = tmp_path / "rutracker.json"
    state = RuTrackerState(torrent_metadata_hashes={HASH})
    state.save(path)

    raw = json.loads(path.read_text())
    assert raw["torrent_metadata_hashes"] == [HASH]

    loaded = RuTrackerState.load(path)
    assert loaded.torrent_metadata_hashes == {HASH}
    assert "torrent_metadata_hashes" not in loaded.as_dict()
