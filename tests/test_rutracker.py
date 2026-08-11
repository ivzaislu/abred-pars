from pathlib import Path

from abred_catalog_pipeline.models import book_to_feed_record
from abred_catalog_pipeline.rutracker.crawler import RuTrackerState, ForumCursor, crawl_once, parse_forum_ids
from abred_catalog_pipeline.rutracker.parser import (
    DEFAULT_AUDIOBOOK_FORUM_IDS,
    RuTrackerWorkerClient,
    detect_last_forum_page,
    hydrate_book_from_torrent,
    parse_forum_html,
    parse_topic_html,
    parse_torrent_bytes,
)

FIX = Path(__file__).parent / "fixtures"


def _b(value):
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, list):
        return b"l" + b"".join(_b(x) for x in value) + b"e"
    if isinstance(value, dict):
        return b"d" + b"".join(_b(k) + _b(value[k]) for k in sorted(value)) + b"e"
    raise TypeError(value)


def _torrent_fixture():
    return _b({
        b"announce": b"https://tracker.example/announce",
        b"info": {
            b"name": b"Test release",
            b"piece length": 262144,
            b"pieces": b"x" * 20,
            b"files": [
                {b"length": 1000, b"path": [b"01.mp3"]},
                {b"length": 2000, b"path": [b"02.mp3"]},
                {b"length": 300, b"path": [b"cover.jpg"]},
            ],
        },
    })


def test_forum_parser_and_scope():
    html = (FIX / "rutracker_viewforum.html").read_text()
    rows = parse_forum_html(html, "https://rutracker.org", 2387)
    assert len(rows) == 1
    assert rows[0].topic_id == "6862086"
    assert rows[0].torrent_url.endswith("dl.php?t=6862086")
    assert rows[0].seeders == 54
    assert rows[0].leechers == 6
    assert 2387 in DEFAULT_AUDIOBOOK_FORUM_IDS
    assert parse_forum_ids("2387,574,2387") == (2387, 574)


def test_real_topic_metadata_is_preserved():
    html = (FIX / "rutracker_real_topic.html").read_text()
    book = parse_topic_html(
        html,
        "https://rutracker.org/forum/viewtopic.php?t=6862086",
        "https://rutracker.org",
    )
    assert book.external_id == "6862086"
    assert book.title == "Строитель 1, Путь строителя 1"
    assert book.authors == ["Ковтунов Алексей"]
    assert book.narrators == ["Андрей Федоренко"]
    assert book.series_name == "Строитель"
    assert book.series_position == 1
    assert book.series_external_id == "topic-series:6862086"
    assert book.torrent is not None
    assert book.torrent.info_hash == "7fd5e9a38677655779a77cd224abd734c56aebdf"


def test_torrent_metainfo_builds_file_list_and_chapters():
    html = (FIX / "rutracker_real_topic.html").read_text()
    book = parse_topic_html(
        html,
        "https://rutracker.org/forum/viewtopic.php?t=6862086",
        "https://rutracker.org",
    )
    torrent = parse_torrent_bytes(
        _torrent_fixture(),
        magnet_uri="",
        torrent_url="https://rutracker.org/forum/dl.php?t=1",
    )
    hydrate_book_from_torrent(book, torrent)
    assert torrent.total_size_bytes == 3300
    assert [(x.index, x.path, x.media_type) for x in torrent.files] == [
        (0, "01.mp3", "audio"),
        (1, "02.mp3", "audio"),
        (2, "cover.jpg", "other"),
    ]
    assert [c.external_id for c in book.chapters] == ["0", "1"]
    record = book_to_feed_record(book, source="rutracker")
    assert record["torrent"]["info_hash"] == torrent.info_hash
    assert len(record["torrent"]["files"]) == 3
    assert len(record["chapters"]) == 2


def test_worker_mirror_keeps_path_query_and_token_header():
    parser = RuTrackerWorkerClient(
        worker_url="https://worker.example", worker_token="secret", delay_seconds=0
    )
    try:
        target = "https://rutracker.org/forum/viewforum.php?f=2387&start=50"
        assert parser._request_url(target) == "https://worker.example/forum/viewforum.php?f=2387&start=50"
        assert parser._headers() == {"X-Proxy-Token": "secret"}
    finally:
        import asyncio
        asyncio.run(parser.aclose())


def test_worker_authorization_and_fetch_mode():
    parser = RuTrackerWorkerClient(
        worker_url="https://worker.example/fetch",
        worker_token="secret",
        worker_token_header="Authorization",
        worker_mode="fetch",
        delay_seconds=0,
    )
    try:
        assert parser._headers() == {"Authorization": "Bearer secret"}
        assert parser._request_url("https://rutracker.org/forum/viewtopic.php?t=1").startswith(
            "https://worker.example/fetch?url="
        )
    finally:
        import asyncio
        asyncio.run(parser.aclose())


def test_last_page_detection_uses_start_offsets_and_text():
    html = """
    <html><body>
      <a href="viewforum.php?f=2387&start=50">2</a>
      <a href="viewforum.php?f=2387&start=4950">100</a>
      <div>Страница 1 из 100</div>
    </body></html>
    """
    assert detect_last_forum_page(html, forum_id=2387, page_size=50) == 100


def test_rutracker_state_roundtrip(tmp_path):
    path = tmp_path / "rutracker.json"
    state = RuTrackerState(forums={"2387": ForumCursor(deep_page=9, last_page=10)})
    state.save(path)
    loaded = RuTrackerState.load(path)
    assert loaded.as_dict() == state.as_dict()


def test_worker_torrent_request_sends_topic_referer():
    import asyncio
    import httpx

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["referer"] = request.headers.get("Referer")
        seen["token"] = request.headers.get("X-Proxy-Token")
        seen["target"] = request.headers.get("X-RuTracker-Target")
        return httpx.Response(200, content=b"d4:infode")

    parser = RuTrackerWorkerClient(
        worker_url="https://worker.example",
        worker_token="secret",
        delay_seconds=0,
    )
    old_client = parser.client
    parser.client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    )
    asyncio.run(old_client.aclose())
    try:
        data = asyncio.run(
            parser.get_torrent(
                "https://rutracker.org/forum/dl.php?t=123",
                referer="https://rutracker.org/forum/viewtopic.php?t=123",
            )
        )
        assert data.startswith(b"d")
        assert seen["url"] == "https://worker.example/forum/dl.php?t=123"
        assert seen["referer"] == "https://rutracker.org/forum/viewtopic.php?t=123"
        assert seen["token"] == "secret"
        assert seen["target"] == "https://rutracker.org/forum/dl.php?t=123"
    finally:
        asyncio.run(parser.aclose())


class _ProbeParser:
    base_url = "https://rutracker.org"
    page_size = 50

    def __init__(self, *, torrent_error: Exception | None = None):
        self.torrent_error = torrent_error
        self.torrent_calls = 0

    def forum_url(self, forum_id: int, page: int) -> str:
        start = "" if page == 1 else f"&start={(page - 1) * self.page_size}"
        return f"https://rutracker.org/forum/viewforum.php?f={forum_id}{start}"

    def topic_url(self, topic_id: str) -> str:
        return f"https://rutracker.org/forum/viewtopic.php?t={topic_id}"

    def torrent_url(self, topic_id: str) -> str:
        return f"https://rutracker.org/forum/dl.php?t={topic_id}"

    async def get_html(self, url: str) -> str:
        if "viewforum.php" in url:
            return (FIX / "rutracker_viewforum.html").read_text()
        return (FIX / "rutracker_real_topic.html").read_text()

    async def get_torrent(self, url: str, *, referer: str = "") -> bytes:
        self.torrent_calls += 1
        if self.torrent_error is not None:
            raise self.torrent_error
        return _torrent_fixture()


def test_crawl_is_magnet_first_and_does_not_call_dl_by_default():
    import asyncio

    parser = _ProbeParser()
    result, next_state = asyncio.run(
        crawl_once(
            parser,
            RuTrackerState(),
            forum_ids=(2387,),
            max_topics=1,
            download_torrents=False,
            advance_cursor=False,
        )
    )

    assert parser.torrent_calls == 0
    assert result["rejected"] == []
    assert len(result["records"]) == 1
    record = result["records"][0]
    assert record["torrent"]["info_hash"] == "7fd5e9a38677655779a77cd224abd734c56aebdf"
    assert record["torrent"]["files"] == []
    assert record["torrent"]["total_size_bytes"] > 0
    assert record["rutracker"]["torrent_metadata_status"] == "magnet"
    assert record["rutracker"]["torrent_metadata_attempted"] is False
    assert record["rutracker"]["torrent_metadata_error"] is None
    assert next_state.as_dict() == RuTrackerState().as_dict()


def test_optional_torrent_failure_falls_back_to_magnet_without_rejecting_topic():
    import asyncio

    parser = _ProbeParser(torrent_error=RuntimeError("403 Forbidden"))
    result, _ = asyncio.run(
        crawl_once(
            parser,
            RuTrackerState(),
            forum_ids=(2387,),
            max_topics=1,
            download_torrents=True,
            advance_cursor=False,
        )
    )

    assert parser.torrent_calls == 1
    assert result["rejected"] == []
    assert len(result["records"]) == 1
    record = result["records"][0]
    assert record["torrent"]["info_hash"] == "7fd5e9a38677655779a77cd224abd734c56aebdf"
    assert record["torrent"]["files"] == []
    assert record["rutracker"]["torrent_metadata_status"] == "magnet_fallback"
    assert record["rutracker"]["torrent_metadata_attempted"] is True
    assert "403 Forbidden" in record["rutracker"]["torrent_metadata_error"]


def test_people_normalization_filters_service_marker_and_keeps_initials():
    from abred_catalog_pipeline.rutracker.parser import _split_people

    assert _split_people("Кочергина Елена, (ЛИ)") == ["Кочергина Елена"]
    assert _split_people("Роман Злотников.") == ["Роман Злотников"]
    assert _split_people("Райро А.") == ["Райро А."]


def test_inferred_subject_author_list_is_split():
    html = """
    <html><body>
      <h1 class="maintitle">
        <a id="topic-title">Подгурский Игорь, Романтовский Дмитрий - На суше и на море [2020, MP3]</a>
      </h1>
      <div class="post_body">
        <span class="post-align">На суше и на море</span>
        <span class="post-b">Исполнитель</span>: Иван Иванов<br>
        <a class="magnet-link"
           href="magnet:?xt=urn:btih:0123456789012345678901234567890123456789">magnet</a>
      </div>
    </body></html>
    """
    book = parse_topic_html(
        html,
        "https://rutracker.org/forum/viewtopic.php?t=5007473",
        "https://rutracker.org",
    )
    assert book.authors == ["Подгурский Игорь", "Романтовский Дмитрий"]


def test_plural_performers_field_keeps_all_narrators():
    expected = [
        "Дарья Рублёва",
        "Вася Аккерман",
        "Евгений Харитонов",
        "Галина Козинец",
        "Сергей Зябко",
        "Александр Новиков",
        "Ирина Лачина",
        "Шевченко Сергей",
        "Кубракова Виталина",
        "Тугова Анастасия",
        "Мария Будрина",
        "Евгений Петиш",
        "Майя Грибоедова",
        "Андрей Борисов",
        "Светлана Тома",
        "Марат Волошин",
    ]
    html = """
    <html><body>
      <h1 class="maintitle"><a id="topic-title">Потенциальные жертвы</a></h1>
      <div class="post_body">
        <span class="post-b">Фамилия автора</span>: Рублёва<br>
        <span class="post-b">Имя автора</span>: Дарья<br>
        <span class="post-b">Исполнители</span>:
        Дарья Рублёва, Вася Аккерман, Евгений Харитонов, Галина Козинец,
        Сергей Зябко, Александр Новиков, Ирина Лачина, Шевченко Сергей,
        Кубракова Виталина, Тугова Анастасия, Мария Будрина, Евгений Петиш,
        Майя Грибоедова, Андрей Борисов, Светлана Тома, Марат Волошин<br>
        <span class="post-b">Жанр</span>: Фантастика, мистика, триллер<br>
        <a class="magnet-link"
           href="magnet:?xt=urn:btih:0123456789012345678901234567890123456789">magnet</a>
      </div>
    </body></html>
    """
    book = parse_topic_html(
        html,
        "https://rutracker.org/forum/viewtopic.php?t=6097671",
        "https://rutracker.org",
    )
    assert book.narrators == expected
    assert "narrators" in book.metadata_fields_present
    assert book.genres == ["Фантастика", "мистика", "триллер"]


def test_legacy_combined_author_field_and_li_suffix():
    html = """
    <html><body>
      <h1 class="maintitle">
        <a id="topic-title">
          Всеволод Слукин, Евгений Карташев Карташев - Смех по дороге в Атлантис
          [Комиссар (ЛИ), 2013 г., 256 kbps, MP3]
        </a>
      </h1>
      <div class="post_body">
        <span style="font-size: 24px;">Смех по дороге в Атлантис</span><br>
        <span class="post-b">Год выпуска</span>: 2013 г.<br>
        <span class="post-b">Фамилия и имя автора</span>:
        Всеволод Слукин, Евгений Карташев<br>
        <span class="post-b">Исполнитель</span>: Комиссар (ЛИ)<br>
        <span class="post-b">Жанр</span>: научно-фантастический рассказ<br>
        <a class="magnet-link"
           href="magnet:?xt=urn:btih:0123456789012345678901234567890123456789">magnet</a>
      </div>
    </body></html>
    """
    book = parse_topic_html(
        html,
        "https://rutracker.org/forum/viewtopic.php?t=4536901",
        "https://rutracker.org",
    )
    assert book.authors == ["Всеволод Слукин", "Евгений Карташев"]
    assert book.narrators == ["Комиссар"]
    assert book.genres == ["научно-фантастический рассказ"]
    assert "authors" in book.metadata_fields_present
    assert "narrators" in book.metadata_fields_present


def test_legacy_mixed_latin_author_label_is_parsed():
    html = """
    <html><body>
      <h1 class="maintitle"><a id="topic-title">Вайн Барбара - Пятьдесят оттенков темноты[Кирсанов Сергей, 2015 г., 128 kbps, MP3]</a></h1>
      <div class="post_body">
        <span class="post-b">Aвтор</span>: Вайн Барбара<br>
        <span class="post-b">Исполнитель</span>: Кирсанов Сергей<br>
        <a class="magnet-link" href="magnet:?xt=urn:btih:0123456789012345678901234567890123456789">magnet</a>
      </div>
    </body></html>
    """
    book = parse_topic_html(html, "https://rutracker.org/forum/viewtopic.php?t=5158225", "https://rutracker.org")
    assert book.authors == ["Вайн Барбара"]
    assert book.narrators == ["Кирсанов Сергей"]


def test_legacy_plural_surname_author_label_and_li_narrator():
    html = """
    <html><body>
      <h1 class="maintitle"><a id="topic-title">Хэйнс Дороти К., Матесон Ричард, Кинг Стивен - Ведьма [Владимир Князев(ЛИ), 2012 г., 256, MP3]</a></h1>
      <div class="post_body">
        <span class="post-b">Фамилии авторов</span>: Хэйнс Дороти К., Матесон Ричард, Кинг Стивен<br>
        <span class="post-b">Исполнитель</span>: Владимир Князев(ЛИ)<br>
        <a class="magnet-link" href="magnet:?xt=urn:btih:0123456789012345678901234567890123456789">magnet</a>
      </div>
    </body></html>
    """
    book = parse_topic_html(html, "https://rutracker.org/forum/viewtopic.php?t=4153936", "https://rutracker.org")
    assert book.authors == ["Хэйнс Дороти К.", "Матесон Ричард", "Кинг Стивен"]
    assert book.narrators == ["Владимир Князев"]


def test_bold_label_with_embedded_colon_is_recognized():
    html = """
    <html><body>
      <h1 class="maintitle"><a id="topic-title">Фаулз Джон &quot;Коллекционер&quot; [А.Хорлин, Е.Морозова, 2005, 192 кбит/с]</a></h1>
      <div class="post_body">
        <span style="font-size:24px">Джон Фаулз &quot;Коллекционер&quot;</span><br>
        <span class="post-b">Исполнители:</span> Александр Хорлин, Елена Морозова<br>
        <span class="post-b">Описание:</span> Тестовое описание книги.<br>
        <a class="magnet-link" href="magnet:?xt=urn:btih:0123456789012345678901234567890123456789">magnet</a>
      </div>
    </body></html>
    """
    book = parse_topic_html(html, "https://rutracker.org/forum/viewtopic.php?t=545082", "https://rutracker.org")
    assert book.authors == ["Фаулз Джон"]
    assert book.narrators == ["Александр Хорлин", "Елена Морозова"]
    assert book.title == "Коллекционер"


def test_strict_legacy_author_fallback_formats():
    cases = [
        ('Евгений Весник &quot;Хмельные странички&quot; [Евгений Весник, 256 кбит/с]', 'Евгений Весник &quot;Хмельные странички&quot;', ["Евгений Весник"], "Хмельные странички"),
        ('Кинки Фридман. &quot;Убийство по Гринвичу&quot;', 'Кинки Фридман. &quot;Убийство по Гринвичу&quot;', ["Кинки Фридман"], "Убийство по Гринвичу"),
        ("Мигель Де Сервантес Сааведра. Дон Кихот", "Мигель Де Сервантес Сааведра. Дон Кихот", ["Мигель Де Сервантес Сааведра"], "Дон Кихот"),
        ("Пушкин А.С. Евгений Онегин [Иннокентий Смоктуновский, 2005, 192 kbps]", "Пушкин А.С. Евгений Онегин", ["Пушкин А.С."], "Евгений Онегин"),
        ("Киплинг Редьярд - Рикша-призрак. В кратере [А. Смоляков, М. Шашлова, М. Станкевич, 2010 г., 160 kbps, MP3]", "Рикша-призрак. В кратере", ["Киплинг Редьярд"], "Рикша-призрак. В кратере"),
    ]
    for index, (subject, body_title, authors, title) in enumerate(cases):
        html = f"""
        <html><body>
          <h1 class="maintitle"><a id="topic-title">{subject}</a></h1>
          <div class="post_body">
            <span style="font-size:24px">{body_title}</span><br>
            <a class="magnet-link" href="magnet:?xt=urn:btih:0123456789012345678901234567890123456789">magnet</a>
          </div>
        </body></html>
        """
        book = parse_topic_html(html, f"https://rutracker.org/forum/viewtopic.php?t={9000000 + index}", "https://rutracker.org")
        assert book.authors == authors
        assert book.title == title


def test_subject_narrator_fallback_is_conservative():
    cases = [
        ('Джеффри Дж.Фокс &quot;Как стать Волшебником Продаж&quot; [Виктор Петров, 2007]', ["Виктор Петров"]),
        ('Евгений Весник &quot;Хмельные странички&quot; [Евгений Весник, 256 кбит/с]', ["Евгений Весник"]),
        ("А.А.Ахматова - Стихотворения и поэма [И.Чурикова, А.Покровская, О.Остроумова и др., 2007, 192 кБ/с]", ["И.Чурикова", "А.Покровская", "О.Остроумова"]),
        ("Тестовая книга [Комиссар (ЛИ), 2013 г., 256 kbps, MP3]", ["Комиссар"]),
    ]
    for index, (subject, expected) in enumerate(cases):
        html = f"""
        <html><body>
          <h1 class="maintitle"><a id="topic-title">{subject}</a></h1>
          <div class="post_body">
            <span style="font-size:24px">Тестовая книга</span><br>
            <a class="magnet-link" href="magnet:?xt=urn:btih:0123456789012345678901234567890123456789">magnet</a>
          </div>
        </body></html>
        """
        book = parse_topic_html(html, f"https://rutracker.org/forum/viewtopic.php?t={9100000 + index}", "https://rutracker.org")
        assert book.narrators == expected


def test_explicit_narrators_win_over_subject_fallback():
    html = """
    <html><body>
      <h1 class="maintitle"><a id="topic-title">Фаулз Джон &quot;Коллекционер&quot; [А.Хорлин, Е.Морозова, 2005, 192 кбит/с]</a></h1>
      <div class="post_body">
        <span class="post-b">Исполнители:</span> Александр Хорлин, Елена Морозова<br>
        <a class="magnet-link" href="magnet:?xt=urn:btih:0123456789012345678901234567890123456789">magnet</a>
      </div>
    </body></html>
    """
    book = parse_topic_html(html, "https://rutracker.org/forum/viewtopic.php?t=545082", "https://rutracker.org")
    assert book.narrators == ["Александр Хорлин", "Елена Морозова"]


def test_subject_fallback_refuses_non_person_va_release():
    html = """
    <html><body>
      <h1 class="maintitle"><a id="topic-title">(musical performance, audio fantacy) VA - Мокрые Уши. Гоголь. - 2014, MP3, 320 kbps</a></h1>
      <div class="post_body">
        <span style="font-size:24px">Мокрые Уши. Гоголь.</span><br>
        <span class="post-b">Жанр</span>: musical performance, audio fantacy<br>
        <a class="magnet-link" href="magnet:?xt=urn:btih:0123456789012345678901234567890123456789">magnet</a>
      </div>
    </body></html>
    """
    book = parse_topic_html(html, "https://rutracker.org/forum/viewtopic.php?t=4809802", "https://rutracker.org")
    assert book.authors == []
    assert book.narrators == []
