from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import FileResponse

from . import SERVER_VERSION, SUPPORTED_SOURCES
from .config import ServerSettings
from .runner import ParserRunner
from .scheduler import ParserScheduler
from .storage import ServerStorage


def create_app(settings: ServerSettings | None = None) -> FastAPI:
    settings = settings or ServerSettings.from_env()
    settings.validate_api()
    settings.ensure_directories()
    storage = ServerStorage(db_path=settings.db_path, data_dir=settings.data_dir)
    storage.initialize()
    runner = ParserRunner(settings, storage)
    scheduler = ParserScheduler(settings, storage, runner)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task: asyncio.Task | None = None
        if settings.scheduler_enabled:
            task = asyncio.create_task(scheduler.run_forever(), name="parser-scheduler")
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(
        title="Abred Parser Server",
        version=SERVER_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.storage = storage
    app.state.runner = runner

    def require_token(
        authorization: str | None = Header(default=None),
        x_parser_token: str | None = Header(default=None, alias="X-Parser-Token"),
    ) -> None:
        candidate = (x_parser_token or "").strip()
        if not candidate and authorization:
            scheme, _, value = authorization.partition(" ")
            if scheme.casefold() == "bearer":
                candidate = value.strip()
        if not candidate or not secrets.compare_digest(candidate, settings.api_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid parser API token")

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "service": "abred-parser-server",
            "version": SERVER_VERSION,
            "scheduler_enabled": settings.scheduler_enabled,
        }

    @app.get("/v1/sources", dependencies=[Depends(require_token)])
    def sources() -> dict:
        return {
            "sources": [storage.source_status(source) for source in SUPPORTED_SOURCES],
        }

    @app.get("/v1/feeds", dependencies=[Depends(require_token)])
    def feeds(
        source: str | None = Query(default=None),
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict:
        if source is not None and source not in SUPPORTED_SOURCES:
            raise HTTPException(status_code=400, detail="unsupported source")
        rows = storage.list_feeds(source=source, after=after, limit=limit)
        return {
            "feeds": [row.public_dict() for row in rows],
            "next_cursor": rows[-1].cursor if rows else after,
        }

    @app.get("/v1/feeds/{feed_id}", dependencies=[Depends(require_token)])
    def feed_metadata(feed_id: str) -> dict:
        feed = storage.get_feed(feed_id)
        if feed is None:
            raise HTTPException(status_code=404, detail="feed not found")
        return feed.public_dict()

    @app.get("/v1/feeds/{feed_id}/bundle", dependencies=[Depends(require_token)])
    def feed_bundle(feed_id: str) -> FileResponse:
        feed = storage.get_feed(feed_id)
        if feed is None:
            raise HTTPException(status_code=404, detail="feed not found")
        path = storage.bundle_path(feed)
        if not path.is_file():
            raise HTTPException(status_code=410, detail="feed bundle is missing from storage")
        return FileResponse(
            path=path,
            media_type="application/zip",
            filename=f"{feed.run_id}.zip",
            headers={
                "ETag": f'"sha256:{feed.bundle_sha256}"',
                "X-Feed-SHA256": feed.feed_sha256,
                "X-Bundle-SHA256": feed.bundle_sha256,
            },
        )

    @app.get("/v1/runs", dependencies=[Depends(require_token)])
    def runs(
        source: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict:
        if source is not None and source not in SUPPORTED_SOURCES:
            raise HTTPException(status_code=400, detail="unsupported source")
        return {"runs": storage.recent_runs(source=source, limit=limit)}

    return app
