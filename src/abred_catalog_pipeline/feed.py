from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "abred.catalog.feed/v1"
_PERSON_NAME_SAFETY_LIMIT = 500


def _canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _validate_backend_person_fields(records: list[dict[str, Any]]) -> None:
    """Fail before feed publication when person metadata cannot fit Backend.

    Backend stores author/narrator display names and identity keys in bounded
    VARCHAR columns.  Keep a small amount of headroom below that boundary so a
    parser bug cannot publish an immutable poison feed that Backend can never
    dry-run successfully.  Do not truncate: a failed parser run keeps its
    crawler cursor unchanged and can be retried after the parser is fixed.
    """
    for record_index, record in enumerate(records):
        external_id = str(record.get("external_id") or "")
        for field in ("authors", "narrators"):
            values = record.get(field) or []
            if not isinstance(values, list):
                continue
            for value_index, value in enumerate(values):
                text = str(value or "").strip()
                if len(text) <= _PERSON_NAME_SAFETY_LIMIT:
                    continue
                raise ValueError(
                    f"feed record[{record_index}] external_id={external_id!r} "
                    f"{field}[{value_index}] exceeds person-name safety limit: "
                    f"{len(text)} > {_PERSON_NAME_SAFETY_LIMIT}"
                )


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
    _validate_backend_person_fields(records)

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
