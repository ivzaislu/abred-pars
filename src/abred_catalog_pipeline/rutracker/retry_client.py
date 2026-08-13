from __future__ import annotations

import asyncio

import httpx

from .parser import RuTrackerWorkerClient


class RetryingRuTrackerWorkerClient(RuTrackerWorkerClient):
    """RuTracker Worker client with bounded retries for transient transport failures."""

    def __init__(
        self,
        *,
        retry_delays: tuple[float, ...] = (1.0, 3.0),
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.retry_delays = tuple(max(0.0, float(value)) for value in retry_delays)

    async def _request(
        self,
        target_url: str,
        *,
        accept: str,
        referer: str = "",
    ) -> httpx.Response:
        if not self.worker_url:
            raise RuntimeError("RUTRACKER_WORKER_URL is required")
        if not self.worker_token:
            raise RuntimeError("RUTRACKER_WORKER_TOKEN is required")

        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)

        headers = self._headers()
        headers["Accept"] = accept
        headers["X-RuTracker-Target"] = target_url
        if referer:
            headers["Referer"] = referer

        request_url = self._request_url(target_url)
        attempts = len(self.retry_delays) + 1
        for attempt in range(attempts):
            try:
                response = await self.client.get(request_url, headers=headers)
            except httpx.RequestError:
                if attempt >= len(self.retry_delays):
                    raise
                delay = self.retry_delays[attempt]
                if delay:
                    await asyncio.sleep(delay)
                continue

            retryable_status = response.status_code == 429 or response.status_code >= 500
            if retryable_status and attempt < len(self.retry_delays):
                await response.aclose()
                delay = self.retry_delays[attempt]
                if delay:
                    await asyncio.sleep(delay)
                continue

            response.raise_for_status()
            return response

        raise RuntimeError("RuTracker Worker retry loop exhausted unexpectedly")
