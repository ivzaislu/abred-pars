from abred_catalog_pipeline.rutracker.parser import parse_topic_html


def topic(subject: str, fields: str = "", body_title: str = ""):
    heading = f'<span style="font-size:24px">{body_title}</span><br>' if body_title else ""
    return f'''<html><body><h1 class="maintitle"><a id="topic-title">{subject}</a></h1>
    <div class="post_body">{heading}{fields}</div></body></html>'''


def parse(subject: str, fields: str = "", body_title: str = ""):
    return parse_topic_html(topic(subject, fields, body_title),
                            "https://rutracker.org/forum/viewtopic.php?t=1",
                            "https://rutracker.org")


def field(label: str, value: str):
    return f'<span class="post-b">{label}</span>: {value}<br>'


def test_author_nickname_label_and_li_narrator():
    book = parse(
        "brothergabriel - Путь 1, Ветер Времен [Freshman, (ЛИ), 2017, 192 kbps, MP3]",
        field("Автор (никнейм)", "brothergabriel") + field("Исполнитель", "Freshman"),
        "Ветер Времен",
    )
    assert book.authors == ["brothergabriel"]
    assert book.narrators == ["Freshman"]
    assert book.title == "Путь 1, Ветер Времен"


def test_multi_author_full_name_label():
    people = "Кови Стивен, Кови Джейн, Кови Джон, Кови Сандра"
    book = parse(
        f"{people} - Счастливый союз. 7 навыков высокоэффективных пар [Сергей Двинянинов, 2023, 128 kbps, MP3]",
        field("Фамилия Имя автора", people) + field("Исполнитель", "Сергей Двинянинов"),
    )
    assert book.authors == ["Кови Стивен", "Кови Джейн", "Кови Джон", "Кови Сандра"]
    assert book.narrators == ["Сергей Двинянинов"]
    assert book.title == "Счастливый союз. 7 навыков высокоэффективных пар"


def test_separate_plural_surnames_and_given_names_in_bold_values():
    fields = '''
    <span class="post-b">Фамилии автора</span>: <span class="post-b">Роулинг, Гэлбрейт</span><br>
    <span class="post-b">Имена автора</span>: <span class="post-b">Джоан, Роберт</span><br>
    <span class="post-b">Исполнитель</span>: Князев Игорь<br>'''
    book = parse(
        "Роулинг Джоан aka Гэлбрейт Роберт – Корморан Страйк 1, Зов Кукушки [Князев Игорь, 2014, MP3]",
        fields,
    )
    assert book.authors == ["Джоан Роулинг", "Роберт Гэлбрейт"]
    assert book.narrators == ["Князев Игорь"]


def test_fio_author_label():
    book = parse(
        "Тит Нат Хан Тит Нат Хан - Жизнь Будды [А.Абагян, (ЛИ), 2015, 320 kbps, MP3]",
        field("ФИО автора", "Тит Нат Хан") + field("Исполнитель", "А.Абагян"),
    )
    assert book.authors == ["Тит Нат Хан"]
    assert book.narrators == ["А.Абагян"]
    assert book.title == "Жизнь Будды"


def test_explicit_title_reciter_is_extracted_and_removed():
    book = parse("Священный Коран (полностью).Чтец Mohd Altablawi [2000, 56 kbps, MP3]")
    assert book.authors == []
    assert book.narrators == ["Mohd Altablawi"]
    assert book.title == "Священный Коран (полностью)"


def test_people_group_can_precede_edition_group():
    book = parse(
        "Ершов Пётр - Конёк-Горбунок [Сергей Лукьянов, Виктор Хохряков, Олег Анофриев, 1964, 128 kbps, MP3] [Мелодия, WEB]",
        field("Автор", "Пётр Ершов"),
        "Конёк-Горбунок",
    )
    assert book.narrators == ["Сергей Лукьянов", "Виктор Хохряков", "Олег Анофриев"]


def test_legacy_subject_author_without_body_title():
    book = parse(
        "Кристи Агата - Сборник детективных произведений [А. Адоскин, Е. Весник, 128-192 кбит/с]"
    )
    assert book.authors == ["Кристи Агата"]
    assert book.title == "Сборник детективных произведений"


def test_fallback_people_are_reflected_in_metadata_fields():
    book = parse("Священный Коран. Чтец Mohd Altablawi [2000, 56 kbps, MP3]")
    assert "narrators" in book.metadata_fields_present


def test_author_narrator_markers_resolve_to_author_name():
    for marker in ("автор", "читает автор", "автор (ЛИ)"):
        book = parse(
            f"Логинов А.А. - Горсть бисера [{marker}, 2008, 128 kbps]",
            field("Автор", "Логинов А.А.") + field("Исполнитель", marker),
        )
        assert book.narrators == ["Логинов А.А."]


def test_descriptive_narrator_fields_are_reduced_to_people():
    cases = {
        "Коран читает Хусам Абдурахман": ["Хусам Абдурахман"],
        "читает и поёт Иван Рассомахин (ЛИ)": ["Иван Рассомахин"],
        "Читает Св.Харлап. Музыка Л.Казаковой.": ["Св.Харлап"],
        "Юрий Гальцев - читает стихи, передразнивает звуки": ["Юрий Гальцев"],
    }
    for raw, expected in cases.items():
        book = parse("Автор - Название [исполнитель, 2000, MP3]",
                     field("Автор", "Автор") + field("Исполнитель", raw))
        assert book.narrators == expected


def test_empty_given_name_does_not_consume_next_label():
    fields = (
        field("Фамилия автора", "Тур Екатерина")
        + field("Имя автора", "")
        + field("Исполнитель", "Екатерина Тур")
    )
    book = parse("Тур Екатерина - Психосоматика [Екатерина Тур, 2023, MP3]", fields)
    assert book.authors == ["Тур Екатерина"]


def test_series_audiobook_label_is_not_part_of_book_title():
    cases = (
        ("Али Мухаммед - История Халифата Аудиокнига. Умар ибн аль-Хаттаб [2013, MP3]",
         "История Халифата", "Умар ибн аль-Хаттаб"),
        ("Умар аль-Ашкар - «Исламские науки» Аудиокнига: «Исламская акида» [2016, MP3]",
         "Исламские науки", "Исламская акида"),
    )
    for subject, series, expected in cases:
        book = parse(subject, field("Автор", "Автор") + field("Цикл/серия", series))
        assert book.title == expected


def test_loose_release_tail_after_brackets_is_removed():
    book = parse("Андрей Родионов - Пельмени устрицы - CD к книге [автор, 2004], MP3, 124 kbps",
                 field("Автор", "Андрей Родионов") + field("Исполнитель", "автор"),
                 "Пельмени устрицы")
    assert book.title == "Пельмени устрицы"
    assert book.narrators == ["Андрей Родионов"]
