from abred_catalog_pipeline.audiopolka.crawler import detect_last_page


def test_detect_last_page_from_path_pagination():
    html = """
    <a href='/p2/'>2</a>
    <a href='/p17/'>17</a>
    <a href='https://audiopolka.club/p503/'>503</a>
    <a href='https://other.invalid/p999/'>bad</a>
    """
    assert detect_last_page(html, "https://audiopolka.club") == 503


def test_detect_last_page_defaults_to_one():
    assert detect_last_page("<html></html>", "https://audiopolka.club") == 1
