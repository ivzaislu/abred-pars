from pathlib import Path

from abred_catalog_pipeline.models import book_to_feed_record
from abred_catalog_pipeline.rutracker.crawler import RuTrackerState, ForumCursor, parse_forum_ids
from abred_catalog_pipeline.rutracker.parser import (
    DEFAULT_AUDIOBOOK_FORUM_IDS,
    RuTrackerWorkerClient,
    detect_last_forum_page,
    hydrate_book_from_torrent,
    parse_forum_html,
    parse_topic_html,
    parse_torrent_bytes,
)

FIX = Path(__file__).parent / "fixtures"


def _b(value):
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, list):
        return b"l" + b"".join(_b(x) for x in value) + b"e"
    if isinstance(value, dict):
        return b"d" + b"".join(_b(k) + _b(value[k]) for k in sorted(value)) + b"e"
    raise TypeError(value)


def _torrent_fixture():
    return _b({
        b"announce": b"https://tracker.example/announce",
        b"info": {
            b"name": b"Test release",
            b"piece length": 262144,
            b"pieces": b"x" * 20,
            b"files": [
                {b"length": 1000, b"path": [b"01.mp3"]},
                {b"length": 2000, b"path": [b"02.mp3"]},
                {b"length": 300, b"path": [b"cover.jpg"]},
            ],
        },
    })


def test_forum_parser_and_scope():
    html = (FIX / "rutracker_viewforum.html").read_text()
    rows = parse_forum_html(html, "https://rutracker.org", 2387)
    assert len(rows) == 1
    assert rows[0].topic_id == "6862086"
    assert rows[0].torrent_url.endswith("dl.php?t=6862086")
    assert rows[0].seeders == 54
    assert rows[0].leechers == 6
    assert 2387 in DEFAULT_AUDIOBOOK_FORUM_IDS
    assert parse_forum_ids("2387,574,2387") == (2387, 574)


def test_real_topic_metadata_is_preserved():
    html = (FIX / "rutracker_real_topic.html").read_text()
    book = parse_topic_html(
        html,
        "https://rutracker.org/forum/viewtopic.php?t=6862086",
        "https://rutracker.org",
    )
    assert book.external_id == "6862086"
    assert book.title == "Строитель 1, Путь строителя 1"
    assert book.authors == ["Ковтунов Алексей"]
    assert book.narrators == ["Андрей Федоренко"]
    assert book.series_name == "Строитель"
    assert book.series_position == 1
    assert book.series_external_id == "topic-series:6862086"
    assert book.torrent is not None
    assert book.torrent.info_hash == "7fd5e9a38677655779a77cd224abd734c56aebdf"


def test_torrent_metainfo_builds_file_list_and_chapters():
    html = (FIX / "rutracker_real_topic.html").read_text()
    book = parse_topic_html(
        html,
        "https://rutracker.org/forum/viewtopic.php?t=6862086",
        "https://rutracker.org",
    )
    torrent = parse_torrent_bytes(_torrent_fixture(), magnet_uri="", torrent_url="https://rutracker.org/forum/dl.php?t=1")
    hydrate_book_from_torrent(book, torrent)
    assert torrent.total_size_bytes == 3300
    assert [(x.index, x.path, x.media_type) for x in torrent.files] == [
        (0, "01.mp3", "audio"),
        (1, "02.mp3", "audio"),
        (2, "cover.jpg", "other"),
    ]
    assert [c.external_id for c in book.chapters] == ["0", "1"]
    record = book_to_feed_record(book, source="rutracker")
    assert record["torrent"]["info_hash"] == torrent.info_hash
    assert len(record["torrent"]["files"]) == 3
    assert len(record["chapters"]) == 2


def test_worker_mirror_keeps_path_query_and_token_header():
    parser = RuTrackerWorkerClient(worker_url="https://worker.example", worker_token="secret", delay_seconds=0)
    try:
        target = "https://rutracker.org/forum/viewforum.php?f=2387&start=50"
        assert parser._request_url(target) == "https://worker.example/forum/viewforum.php?f=2387&start=50"
        assert parser._headers() == {"X-Proxy-Token": "secret"}
    finally:
        import asyncio
        asyncio.run(parser.aclose())


def test_worker_authorization_and_fetch_mode():
    parser = RuTrackerWorkerClient(
        worker_url="https://worker.example/fetch",
        worker_token="secret",
        worker_token_header="Authorization",
        worker_mode="fetch",
        delay_seconds=0,
    )
    try:
        assert parser._headers() == {"Authorization": "Bearer secret"}
        assert parser._request_url("https://rutracker.org/forum/viewtopic.php?t=1").startswith(
            "https://worker.example/fetch?url="
        )
    finally:
        import asyncio
        asyncio.run(parser.aclose())


def test_last_page_detection_uses_start_offsets_and_text():
    html = '''
    <html><body>
      <a href="viewforum.php?f=2387&start=50">2</a>
      <a href="viewforum.php?f=2387&start=4950">100</a>
      <div>Страница 1 из 100</div>
    </body></html>
    '''
    assert detect_last_forum_page(html, forum_id=2387, page_size=50) == 100


def test_rutracker_state_roundtrip(tmp_path):
    path = tmp_path / "rutracker.json"
    state = RuTrackerState(forums={"2387": ForumCursor(deep_page=9, last_page=10)})
    state.save(path)
    loaded = RuTrackerState.load(path)
    assert loaded.as_dict() == state.as_dict()
