from abred_catalog_pipeline.cursor import plan_pages


def test_first_run_uses_page_one_plus_last_five():
    pages, next_deep, complete = plan_pages(last_page=503, deep_page=None, backfill_pages=5)
    assert pages == [1, 503, 502, 501, 500, 499]
    assert next_deep == 498
    assert complete is False


def test_next_run_continues_backfill():
    pages, next_deep, complete = plan_pages(last_page=503, deep_page=498, backfill_pages=5)
    assert pages == [1, 498, 497, 496, 495, 494]
    assert next_deep == 493
    assert complete is False


def test_reaching_page_two_completes_without_wrap():
    pages, next_deep, complete = plan_pages(last_page=503, deep_page=6, backfill_pages=5)
    assert pages == [1, 6, 5, 4, 3, 2]
    assert next_deep is None
    assert complete is True


def test_completed_backfill_scans_page_one_only():
    pages, next_deep, complete = plan_pages(
        last_page=600, deep_page=None, backfill_pages=5, backfill_complete=True
    )
    assert pages == [1]
    assert next_deep is None
    assert complete is True


def test_rutracker_style_one_deep_page():
    pages, next_deep, complete = plan_pages(last_page=394, deep_page=389, backfill_pages=1)
    assert pages == [1, 389]
    assert next_deep == 388
    assert complete is False


def test_page_two_with_one_backfill_completes():
    pages, next_deep, complete = plan_pages(last_page=394, deep_page=2, backfill_pages=1)
    assert pages == [1, 2]
    assert next_deep is None
    assert complete is True
