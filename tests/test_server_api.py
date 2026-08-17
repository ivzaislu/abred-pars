from pathlib import Path

from fastapi.testclient import TestClient

from abred_catalog_pipeline.server.api import create_app
from abred_catalog_pipeline.server.config import ServerSettings


def _settings(tmp_path: Path) -> ServerSettings:
    import os

    previous = dict(os.environ)
    try:
        os.environ["PARSER_DATA_DIR"] = str(tmp_path / "data")
        os.environ["PARSER_API_TOKEN"] = "x" * 48
        os.environ["PARSER_SCHEDULER_ENABLED"] = "false"
        os.environ["PARSER_FEED_RETENTION_HOURS"] = "96"
        return ServerSettings.from_env()
    finally:
        os.environ.clear()
        os.environ.update(previous)


def test_stats_is_protected_and_exposes_control_data(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        assert client.get("/v1/stats").status_code == 401
        response = client.get(
            "/v1/stats",
            headers={"Authorization": "Bearer " + "x" * 48},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["retention"]["hours"] == 96
        assert body["scheduler"]["enabled"] is False
        schedules = {item["source"]: item for item in body["scheduler"]["schedules"]}
        assert schedules["uknig"]["every_hours"] == 1
        assert schedules["audiopolka"]["every_hours"] == 1
        assert schedules["rutracker"]["every_hours"] == 2
        assert body["feeds"]["count"] == 0
        assert {item["source"] for item in body["sources"]} == {
            "uknig",
            "audiopolka",
            "rutracker",
        }
