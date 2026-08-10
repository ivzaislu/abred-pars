from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "abred.catalog.feed/v1"


def _canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def write_feed_bundle(
    *,
    output_dir: str | Path,
    run_id: str,
    source: str,
    pages: list[int],
    records: list[dict[str, Any]],
    tombstones: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    cursor_before: dict[str, Any],
    cursor_after: dict[str, Any],
) -> dict[str, Any]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    feed_id = f"{source}:{run_id}"
    feed = {
        "schema_version": SCHEMA_VERSION,
        "feed_id": feed_id,
        "run_id": run_id,
        "source": source,
        "generated_at": generated_at,
        "pages": pages,
        "cursor_before": cursor_before,
        "cursor_after": cursor_after,
        "counts": {
            "records": len(records),
            "tombstones": len(tombstones),
            "rejected": len(rejected),
        },
        "records": records,
        "tombstones": tombstones,
        "rejected": rejected,
    }
    feed_bytes = _canonical_json_bytes(feed)
    sha256 = hashlib.sha256(feed_bytes).hexdigest()
    feed_path = out / "feed.json"
    feed_path.write_bytes(feed_bytes)
    manifest = {
        "schema_version": "abred.catalog.feed-manifest/v1",
        "feed_id": feed_id,
        "run_id": run_id,
        "source": source,
        "feed_file": feed_path.name,
        "sha256": sha256,
        "bytes": len(feed_bytes),
        "generated_at": generated_at,
    }
    manifest_path = out / "manifest.json"
    manifest_path.write_bytes(_canonical_json_bytes(manifest))
    return {"feed": feed, "manifest": manifest, "feed_path": str(feed_path), "manifest_path": str(manifest_path)}
