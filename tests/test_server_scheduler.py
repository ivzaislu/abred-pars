from datetime import datetime, timezone

from abred_catalog_pipeline.server.scheduler import SourceSchedule


def test_hourly_sources_are_due_every_hour_at_their_minute() -> None:
    schedule = SourceSchedule("uknig", minute=7, every_hours=1)
    assert schedule.due(datetime(2026, 8, 17, 12, 7, tzinfo=timezone.utc))
    assert schedule.due(datetime(2026, 8, 17, 13, 7, tzinfo=timezone.utc))
    assert not schedule.due(datetime(2026, 8, 17, 13, 8, tzinfo=timezone.utc))


def test_rutracker_is_due_every_two_hours() -> None:
    schedule = SourceSchedule("rutracker", minute=47, every_hours=2)
    assert schedule.due(datetime(2026, 8, 17, 12, 47, tzinfo=timezone.utc))
    assert not schedule.due(datetime(2026, 8, 17, 13, 47, tzinfo=timezone.utc))
    assert schedule.due(datetime(2026, 8, 17, 14, 47, tzinfo=timezone.utc))
