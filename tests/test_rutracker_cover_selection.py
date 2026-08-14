from pathlib import Path

from bs4 import BeautifulSoup

from abred_catalog_pipeline.rutracker import parser


FIXTURES = Path(__file__).parent / "fixtures"


def _post(name: str):
    html = (FIXTURES / name).read_text(encoding="utf-8")
    return BeautifulSoup(html, "html.parser").select_one(".post_body")


def test_normal_portrait_cover_is_kept():
    assert parser._cover_from_post(
        _post("rutracker_cover_normal.html"),
        "https://rutracker.org/forum/",
    ) == "https://img.example/book-cover.jpg"


def test_multiple_images_prefer_book_like_portrait():
    assert parser._cover_from_post(
        _post("rutracker_cover_multiple.html"),
        "https://rutracker.org/forum/",
    ) == "https://img.example/actual-cover.jpg"


def test_wide_decorative_post_image_before_cover_is_rejected():
    assert parser._cover_from_post(
        _post("rutracker_cover_wide_before_book.html"),
        "https://rutracker.org/forum/",
    ) == "https://img.example/portrait-book.jpg"


def test_post_without_safe_cover_returns_blank():
    assert parser._cover_from_post(
        _post("rutracker_cover_none.html"),
        "https://rutracker.org/forum/",
    ) == ""


def test_existing_real_fixture_without_dimensions_remains_compatible():
    html = (FIXTURES / "rutracker_real_topic.html").read_text(encoding="utf-8")
    book = parser.parse_topic_html(
        html,
        "https://rutracker.org/forum/viewtopic.php?t=6862086",
        "https://rutracker.org",
    )
    assert book.cover_url == "https://img.example/path-builder.jpg"
