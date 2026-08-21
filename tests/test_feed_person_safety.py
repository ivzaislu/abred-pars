from pathlib import Path

import pytest

from abred_catalog_pipeline.feed import write_feed_bundle


def _write(tmp_path: Path, *, authors=None, narrators=None):
    return write_feed_bundle(
        output_dir=tmp_path / "bundle",
        run_id="test-run",
        source="rutracker",
        pages=[1],
        records=[
            {
                "source": "rutracker",
                "external_id": "123",
                "authors": list(authors or []),
                "narrators": list(narrators or []),
            }
        ],
        tombstones=[],
        rejected=[],
        cursor_before={"source": "rutracker"},
        cursor_after={"source": "rutracker"},
    )


def test_overlong_author_is_rejected_before_bundle_is_written(tmp_path: Path):
    with pytest.raises(ValueError, match=r"authors\[0\].*501 > 500"):
        _write(tmp_path, authors=["А" * 501])

    assert not (tmp_path / "bundle").exists()


def test_overlong_narrator_is_rejected_before_bundle_is_written(tmp_path: Path):
    with pytest.raises(ValueError, match=r"narrators\[0\].*501 > 500"):
        _write(tmp_path, narrators=["Б" * 501])

    assert not (tmp_path / "bundle").exists()


def test_person_name_at_safety_limit_is_allowed(tmp_path: Path):
    result = _write(tmp_path, authors=["А" * 500], narrators=["Б" * 500])

    assert result["feed"]["counts"]["records"] == 1
    assert (tmp_path / "bundle" / "feed.json").is_file()
    assert (tmp_path / "bundle" / "manifest.json").is_file()
