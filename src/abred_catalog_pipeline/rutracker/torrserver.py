from __future__ import annotations

import asyncio
import re
import time
from pathlib import PurePosixPath

import httpx

from ..models import ParsedTorrent, ParsedTorrentFile


_AUDIO_EXTS = {".mp3", ".m4a", ".m4b", ".aac", ".ogg", ".opus", ".flac", ".wav", ".wma"}
_HEX40_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class TorrServerMetadataError(RuntimeError):
    pass


class TorrServerClient:
    """Minimal metadata-only TorrServer client for a shared instance.

    The client never calls ``rem``, ``drop`` or ``wipe`` and never saves a
    magnet to the TorrServer database. Missing magnets are added with
    ``save_to_db=false`` and are left to TorrServer's own inactive-torrent
    timeout. That avoids racing with another app that may start using the same
    torrent while the GitHub job is resolving metadata.

    TorrServer's public ``file_stats[].id`` is a one-based, path-sorted stream
    id. Abred stores zero-based catalog file indexes, so feed indexes are the
    zero-based position in ``file_stats``; playback later resolves the real
    TorrServer stream id by the persisted file path.
    """

    def __init__(
        self,
        *,
        base_url: str,
        username: str = "",
        password: str = "",
        timeout_seconds: float = 30.0,
        poll_interval_seconds: float = 1.0,
        client: httpx.AsyncClient | None = None,
    ):
        base_url = (base_url or "").strip().rstrip("/")
        if not base_url:
            raise ValueError("TorrServer base_url is required")
        self.base_url = base_url
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.poll_interval_seconds = max(0.0, float(poll_interval_seconds))
        self._owns_client = client is None
        if client is None:
            auth = httpx.BasicAuth(username, password) if username else None
            client = httpx.AsyncClient(
                auth=auth,
                follow_redirects=True,
                timeout=httpx.Timeout(min(30.0, self.timeout_seconds)),
            )
        self.client = client

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def _post(self, payload: dict) -> httpx.Response:
        return await self.client.post(f"{self.base_url}/torrents", json=payload)

    async def get(self, info_hash: str) -> dict | None:
        response = await self._post({"action": "get", "hash": info_hash})
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise TorrServerMetadataError("TorrServer get returned a non-object response")
        return data

    async def add(self, magnet_uri: str) -> dict:
        response = await self._post({
            "action": "add",
            "link": magnet_uri,
            "save_to_db": False,
        })
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise TorrServerMetadataError("TorrServer add returned a non-object response")
        return data

    @staticmethod
    def _files_from_status(status: dict) -> list[ParsedTorrentFile]:
        raw_files = status.get("file_stats") or []
        if not isinstance(raw_files, list):
            return []

        files: list[ParsedTorrentFile] = []
        for position, item in enumerate(raw_files):
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip().replace("\\", "/")
            if not path:
                continue
            try:
                size = max(0, int(item.get("length") or 0))
            except (TypeError, ValueError):
                size = 0
            ext = PurePosixPath(path).suffix.casefold()
            files.append(ParsedTorrentFile(
                # TorrServer web IDs are one-based stream IDs. Keep Abred's
                # catalog index zero-based and path-addressable.
                index=position,
                path=path,
                size_bytes=size,
                media_type="audio" if ext in _AUDIO_EXTS else "other",
            ))
        return files

    async def ensure_metadata(self, info_hash: str, magnet_uri: str) -> ParsedTorrent:
        expected_hash = (info_hash or "").strip().lower()
        if not _HEX40_RE.fullmatch(expected_hash):
            raise TorrServerMetadataError(f"invalid info_hash: {info_hash!r}")
        magnet_uri = (magnet_uri or "").strip()
        if not magnet_uri:
            raise TorrServerMetadataError("magnet_uri is empty")

        status = await self.get(expected_hash)
        if status is None:
            status = await self.add(magnet_uri)

        deadline = time.monotonic() + self.timeout_seconds
        last_state = ""
        while True:
            if status is not None:
                actual_hash = str(status.get("hash") or "").strip().lower()
                if actual_hash and actual_hash != expected_hash:
                    raise TorrServerMetadataError(
                        f"TorrServer info_hash mismatch: {expected_hash} != {actual_hash}"
                    )
                last_state = str(status.get("stat_string") or status.get("stat") or "")
                files = self._files_from_status(status)
                if files:
                    return ParsedTorrent(
                        info_hash=expected_hash,
                        magnet_uri=magnet_uri,
                        total_size_bytes=sum(item.size_bytes for item in files),
                        files=files,
                    )

            if time.monotonic() >= deadline:
                suffix = f"; last_state={last_state}" if last_state else ""
                raise TorrServerMetadataError(
                    f"timeout waiting for torrent metadata: {expected_hash}{suffix}"
                )

            if self.poll_interval_seconds:
                await asyncio.sleep(self.poll_interval_seconds)
            else:
                await asyncio.sleep(0)
            status = await self.get(expected_hash)
