from __future__ import annotations

import hashlib
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
