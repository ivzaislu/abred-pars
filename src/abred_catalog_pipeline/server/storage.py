from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import SERVER_VERSION


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class FeedRecord:
    cursor: int
    feed_id: str
    run_id: str
    source: str
    created_at: str
    feed_sha256: str
    bundle_sha256: str
    bundle_bytes: int
    bundle_path: str
    producer_version: str

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("bundle_path", None)
        return data


class ServerStorage:
    def __init__(self, *, db_path: Path, data_dir: Path):
        self.db_path = db_path
        self.data_dir = data_dir

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS feeds (
                    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
                    feed_id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    feed_sha256 TEXT NOT NULL,
                    bundle_sha256 TEXT NOT NULL,
                    bundle_bytes INTEGER NOT NULL,
                    bundle_path TEXT NOT NULL UNIQUE,
                    producer_version TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS feeds_source_cursor_idx ON feeds(source, cursor);

                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    feed_id TEXT,
                    stats_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS runs_source_started_idx ON runs(source, started_at DESC);

                CREATE TABLE IF NOT EXISTS schedule_claims (
                    source TEXT NOT NULL,
                    slot TEXT NOT NULL,
                    claimed_at TEXT NOT NULL,
                    PRIMARY KEY(source, slot)
                );
                """
            )

    @staticmethod
    def _feed_from_row(row: sqlite3.Row) -> FeedRecord:
        return FeedRecord(
            cursor=int(row["cursor"]),
            feed_id=str(row["feed_id"]),
            run_id=str(row["run_id"]),
            source=str(row["source"]),
            created_at=str(row["created_at"]),
            feed_sha256=str(row["feed_sha256"]),
            bundle_sha256=str(row["bundle_sha256"]),
            bundle_bytes=int(row["bundle_bytes"]),
            bundle_path=str(row["bundle_path"]),
            producer_version=str(row["producer_version"]),
        )

    def start_run(self, *, run_id: str, source: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO runs(run_id, source, started_at, status) VALUES (?, ?, ?, 'running')",
                (run_id, source, utcnow_iso()),
            )

    def finish_run(
        self,
        *,
        run_id: str,
        status: str,
        feed_id: str | None = None,
        stats: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE runs
                SET finished_at=?, status=?, feed_id=?, stats_json=?, error=?
                WHERE run_id=?
                """,
                (
                    utcnow_iso(),
                    status,
                    feed_id,
                    json.dumps(stats or {}, ensure_ascii=False, sort_keys=True),
                    error[:8000],
                    run_id,
                ),
            )

    def claim_schedule_slot(self, *, source: str, slot: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO schedule_claims(source, slot, claimed_at) VALUES (?, ?, ?)",
                (source, slot, utcnow_iso()),
            )
            return cursor.rowcount == 1

    def register_feed(
        self,
        *,
        feed_id: str,
        run_id: str,
        source: str,
        created_at: str,
        feed_sha256: str,
        bundle_sha256: str,
        bundle_bytes: int,
        bundle_path: Path,
    ) -> FeedRecord:
        relative = str(bundle_path.resolve().relative_to(self.data_dir.resolve()))
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO feeds(
                    feed_id, run_id, source, created_at, feed_sha256,
                    bundle_sha256, bundle_bytes, bundle_path, producer_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feed_id,
                    run_id,
                    source,
                    created_at,
                    feed_sha256,
                    bundle_sha256,
                    int(bundle_bytes),
                    relative,
                    SERVER_VERSION,
                ),
            )
            row = conn.execute("SELECT * FROM feeds WHERE feed_id=?", (feed_id,)).fetchone()
        assert row is not None
        return self._feed_from_row(row)

    def get_feed(self, feed_id: str) -> FeedRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM feeds WHERE feed_id=?", (feed_id,)).fetchone()
        return self._feed_from_row(row) if row is not None else None

    def list_feeds(self, *, source: str | None, after: int, limit: int) -> list[FeedRecord]:
        with self._connect() as conn:
            if source:
                rows = conn.execute(
                    "SELECT * FROM feeds WHERE source=? AND cursor>? ORDER BY cursor ASC LIMIT ?",
                    (source, int(after), int(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM feeds WHERE cursor>? ORDER BY cursor ASC LIMIT ?",
                    (int(after), int(limit)),
                ).fetchall()
        return [self._feed_from_row(row) for row in rows]

    def bundle_path(self, feed: FeedRecord) -> Path:
        path = (self.data_dir / feed.bundle_path).resolve()
        root = self.data_dir.resolve()
        if root != path and root not in path.parents:
            raise RuntimeError("stored feed path escaped parser data directory")
        return path

    def recent_runs(self, *, source: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if source:
                rows = conn.execute(
                    "SELECT * FROM runs WHERE source=? ORDER BY started_at DESC LIMIT ?",
                    (source, int(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["stats"] = json.loads(item.pop("stats_json") or "{}")
            except json.JSONDecodeError:
                item["stats"] = {}
                item.pop("stats_json", None)
            result.append(item)
        return result

    def source_status(self, source: str) -> dict[str, Any]:
        runs = self.recent_runs(source=source, limit=1)
        with self._connect() as conn:
            feed_count = int(conn.execute(
                "SELECT COUNT(*) FROM feeds WHERE source=?", (source,)
            ).fetchone()[0])
            row = conn.execute(
                "SELECT * FROM feeds WHERE source=? ORDER BY cursor DESC LIMIT 1", (source,)
            ).fetchone()
        return {
            "source": source,
            "feed_count": feed_count,
            "last_feed": self._feed_from_row(row).public_dict() if row is not None else None,
            "last_run": runs[0] if runs else None,
        }

    def publish_bundle(self, *, staging_dir: Path, source: str, run_id: str, feeds_dir: Path) -> FeedRecord:
        feed_path = staging_dir / "feed.json"
        manifest_path = staging_dir / "manifest.json"
        if not feed_path.is_file() or not manifest_path.is_file():
            raise RuntimeError("feed bundle is incomplete")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        feed_id = str(manifest.get("feed_id") or "")
        if manifest.get("source") != source or manifest.get("run_id") != run_id:
            raise RuntimeError("feed manifest source/run_id mismatch")
        expected_feed_sha = str(manifest.get("sha256") or "").casefold()
        actual_feed_sha = _sha256_file(feed_path)
        if not expected_feed_sha or expected_feed_sha != actual_feed_sha:
            raise RuntimeError("feed manifest sha256 mismatch")

        destination_dir = feeds_dir / source
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / f"{run_id}.zip"
        if destination.exists():
            raise RuntimeError(f"immutable feed bundle already exists: {destination.name}")

        temporary = destination_dir / f".{run_id}.{uuid4().hex}.tmp"
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(feed_path, "feed.json")
                archive.write(manifest_path, "manifest.json")
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

        bundle_sha = _sha256_file(destination)
        return self.register_feed(
            feed_id=feed_id,
            run_id=run_id,
            source=source,
            created_at=str(manifest.get("generated_at") or utcnow_iso()),
            feed_sha256=actual_feed_sha,
            bundle_sha256=bundle_sha,
            bundle_bytes=destination.stat().st_size,
            bundle_path=destination,
        )
