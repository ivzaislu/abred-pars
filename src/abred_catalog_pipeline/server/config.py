from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = os.environ.get(name)
    value = default if raw is None or not raw.strip() else int(raw)
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return value


def _float_env(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.environ.get(name)
    value = default if raw is None or not raw.strip() else float(raw)
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


@dataclass(frozen=True, slots=True)
class ServerSettings:
    data_dir: Path
    api_token: str
    host: str
    port: int
    scheduler_enabled: bool
    scheduler_poll_seconds: float

    audiopolka_base_url: str
    audiopolka_backfill_pages: int
    audiopolka_delay_seconds: float
    audiopolka_schedule_minute: int

    uknig_base_url: str
    uknig_backfill_pages: int
    uknig_delay_seconds: float
    uknig_schedule_minute: int

    rutracker_base_url: str
    rutracker_worker_url: str
    rutracker_worker_token: str
    rutracker_worker_token_header: str
    rutracker_worker_mode: str
    rutracker_forums: str
    rutracker_backfill_pages: int
    rutracker_delay_seconds: float
    rutracker_schedule_minute: int
    rutracker_schedule_every_hours: int
    rutracker_torrserver_enrich: bool
    torrserver_urls: tuple[str, ...]
    torrserver_username: str
    torrserver_password: str
    torrserver_timeout_seconds: float
    torrserver_poll_interval_seconds: float
    torrserver_replay_successes: int

    @classmethod
    def from_env(cls) -> "ServerSettings":
        urls = tuple(
            value.strip()
            for value in (
                os.environ.get("TORRSERVER_URL", ""),
                os.environ.get("TORRSERVER_URL_2", ""),
            )
            if value.strip()
        )
        mode = os.environ.get("RUTRACKER_WORKER_MODE", "mirror").strip().casefold() or "mirror"
        if mode not in {"mirror", "fetch"}:
            raise ValueError("RUTRACKER_WORKER_MODE must be mirror or fetch")
        return cls(
            data_dir=Path(os.environ.get("PARSER_DATA_DIR", "/data")).expanduser(),
            api_token=os.environ.get("PARSER_API_TOKEN", "").strip(),
            host=os.environ.get("PARSER_HOST", "0.0.0.0").strip() or "0.0.0.0",
            port=_int_env("PARSER_PORT", 8081, minimum=1, maximum=65535),
            scheduler_enabled=_bool_env("PARSER_SCHEDULER_ENABLED", True),
            scheduler_poll_seconds=_float_env("PARSER_SCHEDULER_POLL_SECONDS", 15.0, minimum=1.0),
            audiopolka_base_url=os.environ.get("AUDIOPOLKA_BASE_URL", "https://audiopolka.club").strip(),
            audiopolka_backfill_pages=_int_env("AUDIOPOLKA_BACKFILL_PAGES", 5, minimum=0, maximum=200),
            audiopolka_delay_seconds=_float_env("AUDIOPOLKA_DELAY_SECONDS", 0.35),
            audiopolka_schedule_minute=_int_env("AUDIOPOLKA_SCHEDULE_MINUTE", 17, minimum=0, maximum=59),
            uknig_base_url=os.environ.get("UKNIG_BASE_URL", "https://uknig.com").strip(),
            uknig_backfill_pages=_int_env("UKNIG_BACKFILL_PAGES", 20, minimum=0, maximum=200),
            uknig_delay_seconds=_float_env("UKNIG_DELAY_SECONDS", 0.35),
            uknig_schedule_minute=_int_env("UKNIG_SCHEDULE_MINUTE", 7, minimum=0, maximum=59),
            rutracker_base_url=os.environ.get("RUTRACKER_BASE_URL", "https://rutracker.org").strip(),
            rutracker_worker_url=os.environ.get("RUTRACKER_WORKER_URL", "").strip(),
            rutracker_worker_token=os.environ.get("RUTRACKER_WORKER_TOKEN", "").strip(),
            rutracker_worker_token_header=os.environ.get("RUTRACKER_WORKER_TOKEN_HEADER", "X-Proxy-Token").strip() or "X-Proxy-Token",
            rutracker_worker_mode=mode,
            rutracker_forums=os.environ.get("RUTRACKER_FORUMS", "").strip(),
            rutracker_backfill_pages=_int_env("RUTRACKER_BACKFILL_PAGES", 1, minimum=0, maximum=50),
            rutracker_delay_seconds=_float_env("RUTRACKER_DELAY_SECONDS", 0.15),
            rutracker_schedule_minute=_int_env("RUTRACKER_SCHEDULE_MINUTE", 47, minimum=0, maximum=59),
            rutracker_schedule_every_hours=_int_env("RUTRACKER_SCHEDULE_EVERY_HOURS", 2, minimum=1, maximum=24),
            rutracker_torrserver_enrich=_bool_env("RUTRACKER_TORRSERVER_ENRICH", True),
            torrserver_urls=urls,
            torrserver_username=os.environ.get("TORRSERVER_USERNAME", "").strip(),
            torrserver_password=os.environ.get("TORRSERVER_PASSWORD", ""),
            torrserver_timeout_seconds=_float_env("TORRSERVER_TIMEOUT_SECONDS", 30.0, minimum=1.0),
            torrserver_poll_interval_seconds=_float_env("TORRSERVER_POLL_INTERVAL_SECONDS", 1.0, minimum=0.1),
            torrserver_replay_successes=_int_env("TORRSERVER_REPLAY_SUCCESSES", 1, minimum=1, maximum=20),
        )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "server.sqlite3"

    @property
    def feeds_dir(self) -> Path:
        return self.data_dir / "feeds"

    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state"

    @property
    def staging_dir(self) -> Path:
        return self.data_dir / "staging"

    @property
    def locks_dir(self) -> Path:
        return self.data_dir / "locks"

    def ensure_directories(self) -> None:
        for path in (self.data_dir, self.feeds_dir, self.state_dir, self.staging_dir, self.locks_dir):
            path.mkdir(parents=True, exist_ok=True)

    def validate_api(self) -> None:
        if len(self.api_token) < 32:
            raise RuntimeError("PARSER_API_TOKEN must be set to at least 32 characters")

    def validate_source(self, source: str) -> None:
        if source == "rutracker":
            if not self.rutracker_worker_url:
                raise RuntimeError("RUTRACKER_WORKER_URL is required for RuTracker")
            if self.rutracker_torrserver_enrich and not self.torrserver_urls:
                raise RuntimeError("RuTracker TorrServer enrichment is enabled but TORRSERVER_URL is empty")
