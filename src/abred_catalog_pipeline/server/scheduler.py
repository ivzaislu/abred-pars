from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import ServerSettings
from .runner import ParserRunner
from .storage import ServerStorage

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SourceSchedule:
    source: str
    minute: int
    every_hours: int = 1

    def due(self, now: datetime) -> bool:
        return now.minute == self.minute and now.hour % self.every_hours == 0


class ParserScheduler:
    def __init__(self, settings: ServerSettings, storage: ServerStorage, runner: ParserRunner):
        self.settings = settings
        self.storage = storage
        self.runner = runner
        self.schedules = (
            SourceSchedule("uknig", settings.uknig_schedule_minute),
            SourceSchedule("audiopolka", settings.audiopolka_schedule_minute),
            SourceSchedule(
                "rutracker",
                settings.rutracker_schedule_minute,
                settings.rutracker_schedule_every_hours,
            ),
        )
        self._tasks: set[asyncio.Task] = set()

    async def run_forever(self) -> None:
        while True:
            now = datetime.now(timezone.utc)
            slot = now.strftime("%Y-%m-%dT%H:%MZ")
            for schedule in self.schedules:
                if not schedule.due(now):
                    continue
                if not self.storage.claim_schedule_slot(source=schedule.source, slot=slot):
                    continue
                task = asyncio.create_task(self._run(schedule.source), name=f"parser-{schedule.source}-{slot}")
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            await asyncio.sleep(self.settings.scheduler_poll_seconds)

    async def _run(self, source: str) -> None:
        try:
            result = await self.runner.run_source(source)
            logger.info("scheduled parser run completed: %s", result)
        except Exception:
            logger.exception("scheduled parser run failed for %s", source)
