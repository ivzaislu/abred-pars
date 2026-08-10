import hashlib
import json
from pathlib import Path

from abred_catalog_pipeline.feed import write_feed_bundle


def test_feed_bundle_has_manifest_sha256(tmp_path: Path):
    bundle = write_feed_bundle(
        output_dir=tmp_path,
        run_id="test-run",
        source="audiopolka",
        pages=[1, 5, 4, 3, 2],
        records=[{"source": "audiopolka", "external_id": "123", "title": "Book"}],
        tombstones=[],
        rejected=[],
        cursor_before={"source": "audiopolka", "deep_page": 5, "last_page": 5},
        cursor_after={"source": "audiopolka", "deep_page": 5, "last_page": 5},
    )
    feed_bytes = (tmp_path / "feed.json").read_bytes()
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["sha256"] == hashlib.sha256(feed_bytes).hexdigest()
    assert manifest["feed_id"] == "audiopolka:test-run"
    assert bundle["feed"]["counts"]["records"] == 1
