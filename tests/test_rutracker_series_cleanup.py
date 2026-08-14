from pathlib import Path

from abred_catalog_pipeline.rutracker import parser
from abred_catalog_pipeline.rutracker.series import normalize_series_name


FIXTURES = Path(__file__).parent / "fixtures"


def test_series_cleanup_stops_at_next_flattened_metadata_label():
    raw = (
        "Перья Номер книги : 2 Жанр : Героическое фэнтези "
        "Издательство : Аудиокнига Категория : аудиокнига "
        "Аудиокодек : MP3 Битрейт : 64 kbps Описание : очень длинный текст"
    )
    assert normalize_series_name(raw) == "Перья"


def test_series_cleanup_rejects_unbounded_garbage_instead_of_truncating():
    assert normalize_series_name("x" * 600) == ""


def test_parse_topic_html_recovers_series_from_nested_broken_markup():
    html = (FIXTURES / "rutracker_series_nested_metadata.html").read_text(encoding="utf-8")
    book = parser.parse_topic_html(
        html,
        "https://rutracker.org/forum/viewtopic.php?t=9999001",
        "https://rutracker.org",
    )

    assert book.title == "Эпоха пепла"
    assert book.series_name == "Перья"
    assert book.series_position == 2
    assert len(book.series_name) < 512
