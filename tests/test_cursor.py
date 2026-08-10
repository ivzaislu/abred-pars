from abred_catalog_pipeline.cursor import plan_pages


def test_first_run_uses_page_one_plus_last_five():
    pages, next_deep = plan_pages(last_page=503, deep_page=None, backfill_pages=5)
    assert pages == [1, 503, 502, 501, 500, 499]
    assert next_deep == 498


def test_next_run_continues_backfill():
    pages, next_deep = plan_pages(last_page=503, deep_page=498, backfill_pages=5)
    assert pages == [1, 498, 497, 496, 495, 494]
    assert next_deep == 493


def test_reaching_page_two_wraps():
    pages, next_deep = plan_pages(last_page=503, deep_page=6, backfill_pages=5)
    assert pages == [1, 6, 5, 4, 3, 2]
    assert next_deep == 503


def test_page_one_never_duplicated_in_backfill():
    pages, next_deep = plan_pages(last_page=3, deep_page=3, backfill_pages=5)
    assert pages == [1, 3, 2]
    assert next_deep == 3
