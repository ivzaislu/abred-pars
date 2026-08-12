from abred_catalog_pipeline.rutracker.parser import _select_topic_title, parse_topic_html


def test_legacy_release_metadata_prefers_clean_body_title():
    cases = [
        (
            "Рен Персиваль - Похороны викинга [Козий Николай, 96kb,44kHz,время 14ч.11м.22с]",
            "Похороны викинга",
            ["Рен Персиваль"],
            "Похороны викинга",
        ),
        (
            "Баур Ганс - Личный пилот Гитлера. Воспоминания обергруппенфюрера СС. 1939-1945. [Харитонов Александр, 2026, 128 kbps, MP3] Раздача будет пополняться по мере озвучивания.",
            "Личный пилот Гитлера. Воспоминания обергруппенфюрера СС. 1939-1945.",
            ["Баур Ганс"],
            "Личный пилот Гитлера. Воспоминания обергруппенфюрера СС. 1939-1945.",
        ),
        (
            "Андрей Родионов - Пельмени устрицы - CD к книге [автор, 2004], MP3, 124 kbps",
            "Пельмени устрицы - CD к книге",
            ["Андрей Родионов"],
            "Пельмени устрицы",
        ),
    ]
    for subject, body, authors, expected in cases:
        assert _select_topic_title(subject, body, authors) == expected


def test_body_title_itself_gets_conservative_media_cleanup():
    cases = [
        (
            'Даниил Андреев - "Роза мира" Д.Андреев аудиокнига MP3 (авторская)',
            '"Роза мира" Д.Андреев аудиокнига мр3 (авторская)',
            ["Даниил Андреев"],
            "Роза мира",
        ),
        (
            "Вадим Зеланд - Практический курс Трансерфинга за 78 дней (аудиокнига) Rip 64 kbps",
            "Практический курс Трансерфинга за 78 дней (аудиокнига) Rip 64 kbps",
            ["Вадим Зеланд"],
            "Практический курс Трансерфинга за 78 дней",
        ),
        (
            "Мухаммед - Благословенный коран MP3, PDF на русском",
            "Благословенный коран MP3, PDF на русском",
            ["Мухаммед"],
            "Благословенный коран",
        ),
    ]
    for subject, body, authors, expected in cases:
        assert _select_topic_title(subject, body, authors) == expected


def test_clean_richer_subject_is_not_replaced_by_short_body_title():
    assert _select_topic_title(
        "Строитель 1, Путь строителя 1",
        "Путь строителя",
        [],
    ) == "Строитель 1, Путь строителя 1"


def test_year_range_inside_real_title_is_preserved():
    assert _select_topic_title(
        "Баур Ганс - Личный пилот Гитлера. Воспоминания обергруппенфюрера СС. 1939-1945.",
        "Личный пилот Гитлера. Воспоминания обергруппенфюрера СС. 1939-1945.",
        ["Баур Ганс"],
    ) == "Личный пилот Гитлера. Воспоминания обергруппенфюрера СС. 1939-1945."


def test_release_type_audiobook_prefix_is_removed_only_at_title_start():
    cases = [
        (
            "Исмаил ибн Умар ибн Касир - Аудиокнига. Рассказы о пророках. Ибн Касир [2015, 256 kbps, MP3]",
            "Аудиокнига. Рассказы о пророках. Ибн Касир",
            ["Исмаил ибн Умар ибн Касир"],
            "Рассказы о пророках. Ибн Касир",
        ),
        (
            "Аудиокнига: Коран. Перевод смыслов. [Халяф Джафаров. Эльмир Кулиев, 2016, 96 kbps, MP3]",
            "Аудиокнига: Коран. Перевод смыслов.",
            [],
            "Коран. Перевод смыслов.",
        ),
        (
            "Аудиокнига: «Единобожие. Учебное пособие» [2014, 160 kbps, MP3]",
            "Аудиокнига: «Единобожие. Учебное пособие»",
            [],
            "Единобожие. Учебное пособие",
        ),
        (
            "Имам Ибн Каййим аль-Джаузийя - Аудиокнига: «Вабиль. Благодатный дождь» [2015, 128 kbps, MP3]",
            "Аудиокнига: «Вабиль. Благодатный дождь»",
            ["Имам Ибн Каййим аль-Джаузийя"],
            "Вабиль. Благодатный дождь",
        ),
        (
            "- Аудиокнига «История жизни Пророка» [2014, 56 kbps, MP3]",
            "Аудиокнига «История жизни Пророка»",
            [],
            "История жизни Пророка",
        ),
        (
            "Аудиокнига «Усуль аль-Иман» [2014, 192 kbps, MP3]",
            "Аудиокнига «Усуль аль-Иман»",
            [],
            "Усуль аль-Иман",
        ),
        (
            "Аудиокнига. Личность мусульманки (Мухаммад Али аль-Хашими / Умм Иклиль Карима / Динара Садретдинова) [2011, MP3, 128kbit]",
            "Аудиокнига. Личность мусульманки",
            ["Мухаммад Али аль-Хашими"],
            "Личность мусульманки",
        ),
    ]
    for subject, body, authors, expected in cases:
        assert _select_topic_title(subject, body, authors) == expected


def test_audiobook_word_inside_real_title_is_preserved():
    assert _select_topic_title(
        "Как создавалась аудиокнига: дневник студии",
        "Как создавалась аудиокнига: дневник студии",
        [],
    ) == "Как создавалась аудиокнига: дневник студии"


def _topic_html(title: str, author: str, narrator: str, body_title: str) -> str:
    return f'''<html><body>
    <h1 class="maintitle"><a id="topic-title">{title}</a></h1>
    <div class="post_body">
      <span style="font-size: 24px">{body_title}</span><br>
      <span class="post-b">Автор</span>: {author}<br>
      <span class="post-b">Исполнитель</span>: {narrator}<br>
      <span class="post-b">Жанр</span>: аудиозапись семинара<br>
    </div>
    </body></html>'''


def test_confirmed_narrator_suffix_is_removed_from_real_legacy_shape():
    cases = [
        (
            "Гинзбург М.Р. - Эриксоновский гипноз ступень № 4 [Гинзбург М.Р.]",
            "Гинзбург М.Р.",
            "Гинзбург М.Р.",
            "Эрикссоновский гипноз ступень № 4",
            "Эриксоновский гипноз ступень № 4",
        ),
        (
            "М.Р.Гинзбург - Эриксоновский гипноз : ступень № 6 [М.Р.Гинзбург]",
            "М.Р.Гинзбург",
            "М.Р.Гинзбург",
            "Эриксоновский гипноз : ступень № 6",
            "Эриксоновский гипноз : ступень № 6",
        ),
    ]
    for subject, author, narrator, body_title, expected in cases:
        book = parse_topic_html(
            _topic_html(subject, author, narrator, body_title),
            "https://rutracker.org/forum/viewtopic.php?t=1",
            "https://rutracker.org",
        )
        assert book.title == expected
        assert book.authors == [author]
        assert book.narrators == [narrator]


def test_unrelated_bracketed_qualifier_is_preserved():
    subject = "Абдерауф Даккак - Коранические рассказы для детей [Коран]"
    book = parse_topic_html(
        _topic_html(subject, "Абдерауф Даккак", "Другой Исполнитель", "Коранические рассказы для детей [Коран]"),
        "https://rutracker.org/forum/viewtopic.php?t=870349",
        "https://rutracker.org",
    )
    assert book.title == "Коранические рассказы для детей [Коран]"
