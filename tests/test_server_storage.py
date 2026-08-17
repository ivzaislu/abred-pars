from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from abred_catalog_pipeline.feed import write_feed_bundle
from abred_catalog_pipeline.server.storage import ServerStorage


def test_publish_bundle_is_registered_and_listed(tmp_path: Path) -> None:
    data = tmp_path / "data"
    staging = data / "staging" / "run"
    feeds = data / "feeds"
    storage = ServerStorage(db_path=data / "server.sqlite3", data_dir=data)
    storage.initialize()

    bundle = write_feed_bundle(
        output_dir=staging,
        run_id="20260817T120000Z-test",
        source="uknig",
        pages=[1],
        records=[],
        tombstones=[],
        rejected=[],
        cursor_before={},
        cursor_after={},
    )
    record = storage.publish_bundle(
        staging_dir=staging,
        source="uknig",
        run_id="20260817T120000Z-test",
        feeds_dir=feeds,
    )

    assert record.feed_id == bundle["manifest"]["feed_id"]
    rows = storage.list_feeds(source="uknig", after=0, limit=10)
    assert [row.feed_id for row in rows] == [record.feed_id]
    path = storage.bundle_path(record)
    assert path.is_file()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == record.bundle_sha256


def test_schedule_claim_is_durable(tmp_path: Path) -> None:
    data = tmp_path / "data"
    storage = ServerStorage(db_path=data / "server.sqlite3", data_dir=data)
    storage.initialize()
    assert storage.claim_schedule_slot(source="uknig", slot="2026-08-17T12:07Z") is True
    assert storage.claim_schedule_slot(source="uknig", slot="2026-08-17T12:07Z") is False


def test_feed_retention_deletes_only_older_than_four_days(tmp_path: Path) -> None:
    data = tmp_path / "data"
    feeds = data / "feeds" / "uknig"
    feeds.mkdir(parents=True)
    storage = ServerStorage(db_path=data / "server.sqlite3", data_dir=data)
    storage.initialize()
    now = datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc)

    old_path = feeds / "old.zip"
    old_path.write_bytes(b"old")
    storage.register_feed(
        feed_id="uknig:old",
        run_id="old",
        source="uknig",
        created_at=(now - timedelta(days=4, seconds=1)).isoformat(),
        feed_sha256="a" * 64,
        bundle_sha256="b" * 64,
        bundle_bytes=old_path.stat().st_size,
        bundle_path=old_path,
    )

    fresh_path = feeds / "fresh.zip"
    fresh_path.write_bytes(b"fresh")
    storage.register_feed(
        feed_id="uknig:fresh",
        run_id="fresh",
        source="uknig",
        created_at=(now - timedelta(days=4)).isoformat(),
        feed_sha256="c" * 64,
        bundle_sha256="d" * 64,
        bundle_bytes=fresh_path.stat().st_size,
        bundle_path=fresh_path,
    )

    result = storage.purge_expired_feeds(retention_hours=96, now=now)

    assert result["deleted"] == 1
    assert not old_path.exists()
    assert fresh_path.exists()
    assert [row.feed_id for row in storage.list_feeds(source="uknig", after=0, limit=10)] == ["uknig:fresh"]


def test_statistics_reports_retention_feeds_runs_and_disk(tmp_path: Path) -> None:
    data = tmp_path / "data"
    bundle = data / "feeds" / "rutracker" / "one.zip"
    bundle.parent.mkdir(parents=True)
    bundle.write_bytes(b"12345")
    storage = ServerStorage(db_path=data / "server.sqlite3", data_dir=data)
    storage.initialize()
    storage.register_feed(
        feed_id="rutracker:one",
        run_id="one",
        source="rutracker",
        created_at=datetime.now(timezone.utc).isoformat(),
        feed_sha256="a" * 64,
        bundle_sha256="b" * 64,
        bundle_bytes=5,
        bundle_path=bundle,
    )
    storage.start_run(run_id="run-1", source="rutracker")
    storage.finish_run(run_id="run-1", status="COMPLETED")

    stats = storage.statistics(retention_hours=96)

    assert stats["retention"]["hours"] == 96
    assert stats["retention"]["days"] == 4
    assert stats["feeds"]["count"] == 1
    assert stats["feeds"]["bundle_bytes"] == 5
    assert stats["feeds"]["missing_bundles"] == 0
    assert stats["runs"]["by_status"]["COMPLETED"] == 1
    assert stats["disk"]["free_bytes"] > 0
