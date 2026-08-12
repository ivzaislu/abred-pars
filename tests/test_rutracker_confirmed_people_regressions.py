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
