from __future__ import annotations

import asyncio
import re
import time
from pathlib import PurePosixPath
from typing import Iterable

import httpx

from ..models import ParsedTorrent, ParsedTorrentFile


_AUDIO_EXTS = {".mp3", ".m4a", ".m4b", ".aac", ".ogg", ".opus", ".flac", ".wav", ".wma"}
_HEX40_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class TorrServerMetadataError(RuntimeError):
    pass


class TorrServerTransientError(TorrServerMetadataError):
    """Временная ошибка, для которой допустим один failover на другой сервер."""


class TorrServerClient:
    """Минимальный metadata-only клиент одного общего TorrServer.

    Клиент никогда не вызывает ``rem``, ``drop`` или ``wipe`` и не сохраняет
    magnet в базе TorrServer. Отсутствующий magnet добавляется с
    ``save_to_db=false`` и остаётся под обычным inactive-torrent timeout самого
    TorrServer. Это не создаёт гонку с другим приложением, которое может начать
    использовать тот же torrent, пока GitHub job получает metadata.

    Публичный ``file_stats[].id`` TorrServer — one-based stream id в сортировке
    по path. В feed Abred хранится zero-based индекс позиции в ``file_stats``;
    playback затем находит реальный stream id по сохранённому path.
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
                raise TorrServerTransientError(
                    f"timeout waiting for torrent metadata: {expected_hash}{suffix}"
                )

            if self.poll_interval_seconds:
                await asyncio.sleep(self.poll_interval_seconds)
            else:
                await asyncio.sleep(0)
            status = await self.get(expected_hash)


def _is_transient_torrserver_error(exc: Exception) -> bool:
    if isinstance(exc, TorrServerTransientError):
        return True
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return False


class TorrServerPool:
    """Пул TorrServer с least-in-flight выбором и одним transient failover.

    Одновременные разные ``info_hash`` распределяются по наименее занятым
    серверам; при равной загрузке порядок стабильно определяется самим hash.
    Один hash никогда не отправляется на два сервера одновременно. Только
    timeout/network/HTTP 429/5xx разрешают один последовательный failover на
    другой сервер. Structural metadata errors остаются blocking без failover.

    Логин и пароль задаются один раз в ``from_urls`` и применяются ко всем
    экземплярам. Пустые и повторяющиеся URL игнорируются; один URL сохраняет
    полную обратную совместимость с прежней конфигурацией.
    """

    def __init__(self, clients: Iterable[TorrServerClient]):
        self.clients = tuple(clients)
        if not self.clients:
            raise ValueError("at least one TorrServer client is required")
        self._in_flight = [0 for _ in self.clients]
        self._attempted = [0 for _ in self.clients]
        self._enriched = [0 for _ in self.clients]
        self._failed = [0 for _ in self.clients]
        self._failovers = 0

    @classmethod
    def from_urls(
        cls,
        base_urls: Iterable[str],
        *,
        username: str = "",
        password: str = "",
        timeout_seconds: float = 30.0,
        poll_interval_seconds: float = 1.0,
    ) -> "TorrServerPool":
        normalized: list[str] = []
        for value in base_urls:
            url = (value or "").strip().rstrip("/")
            if url and url not in normalized:
                normalized.append(url)
        if not normalized:
            raise ValueError("at least one TorrServer URL is required")
        return cls(
            TorrServerClient(
                base_url=url,
                username=username,
                password=password,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )
            for url in normalized
        )

    @property
    def size(self) -> int:
        return len(self.clients)

    def _primary_index(self, info_hash: str, *, exclude: set[int] | None = None) -> int:
        expected_hash = (info_hash or "").strip().lower()
        if not _HEX40_RE.fullmatch(expected_hash):
            raise TorrServerMetadataError(f"invalid info_hash: {info_hash!r}")
        excluded = exclude or set()
        available = [index for index in range(len(self.clients)) if index not in excluded]
        if not available:
            raise TorrServerMetadataError("no TorrServer instances available")
        preferred = int(expected_hash[-8:], 16) % len(self.clients)
        return min(
            available,
            key=lambda index: (
                self._in_flight[index],
                (index - preferred) % len(self.clients),
            ),
        )

    async def _attempt(self, index: int, info_hash: str, magnet_uri: str) -> ParsedTorrent:
        self._attempted[index] += 1
        self._in_flight[index] += 1
        try:
            torrent = await self.clients[index].ensure_metadata(info_hash, magnet_uri)
        except Exception:
            self._failed[index] += 1
            raise
        else:
            self._enriched[index] += 1
            return torrent
        finally:
            self._in_flight[index] -= 1

    async def ensure_metadata(self, info_hash: str, magnet_uri: str) -> ParsedTorrent:
        first = self._primary_index(info_hash)
        try:
            return await self._attempt(first, info_hash, magnet_uri)
        except Exception as first_exc:
            if self.size < 2 or not _is_transient_torrserver_error(first_exc):
                raise

            second = self._primary_index(info_hash, exclude={first})
            self._failovers += 1
            try:
                return await self._attempt(second, info_hash, magnet_uri)
            except Exception as second_exc:
                raise TorrServerMetadataError(
                    "TorrServer failover exhausted for "
                    f"{(info_hash or '').strip().lower()}: "
                    f"primary={type(first_exc).__name__}: {first_exc}; "
                    f"fallback={type(second_exc).__name__}: {second_exc}"
                ) from second_exc

    def statistics(self) -> dict:
        return {
            "servers": [
                {
                    "server": index + 1,
                    "attempted": self._attempted[index],
                    "enriched": self._enriched[index],
                    "failed": self._failed[index],
                    "in_flight": self._in_flight[index],
                }
                for index in range(self.size)
            ],
            "failovers": self._failovers,
        }

    async def aclose(self) -> None:
        for client in self.clients:
            await client.aclose()
