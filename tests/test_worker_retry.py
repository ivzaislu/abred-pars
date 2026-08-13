import httpx
import pytest

from abred_catalog_pipeline.rutracker.parser import RuTrackerWorkerClient
from abred_catalog_pipeline.rutracker.retry_client import RetryingRuTrackerWorkerClient


def make_client(handler):
    client = RetryingRuTrackerWorkerClient(
        worker_url="https://worker.example",
        worker_token="test-token",
        delay_seconds=0,
        retry_delays=(0, 0),
    )
    original = client.client
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client, original


def test_default_worker_client_uses_retry_class():
    assert RuTrackerWorkerClient is RetryingRuTrackerWorkerClient


@pytest.mark.asyncio
async def test_retries_502_then_succeeds():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(502, request=request)
        return httpx.Response(200, request=request, content=b"ok")

    client, original = make_client(handler)
    await original.aclose()
    try:
        assert await client.get_html("https://rutracker.org/forum/viewforum.php?f=403") == "ok"
        assert calls == 3
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_retries_429_then_succeeds():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        status = 429 if calls == 1 else 200
        return httpx.Response(status, request=request, content=b"ok" if status == 200 else b"")

    client, original = make_client(handler)
    await original.aclose()
    try:
        assert await client.get_html("https://rutracker.org/forum/viewtopic.php?t=1") == "ok"
        assert calls == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_retries_connection_error_then_succeeds():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("temporary", request=request)
        return httpx.Response(200, request=request, content=b"ok")

    client, original = make_client(handler)
    await original.aclose()
    try:
        assert await client.get_html("https://rutracker.org/forum/viewtopic.php?t=2") == "ok"
        assert calls == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_does_not_retry_404():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(404, request=request)

    client, original = make_client(handler)
    await original.aclose()
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.get_html("https://rutracker.org/forum/viewtopic.php?t=3")
        assert calls == 1
    finally:
        await client.aclose()
