from abred_catalog_pipeline.rutracker.parser import parse_topic_html


def parse(subject: str, body: str, topic_id: str = "1"):
    html = f'''<html><body>
    <h1 class="maintitle"><a id="topic-title">{subject}</a></h1>
    <div class="post_body">{body}</div>
    </body></html>'''
    return parse_topic_html(
        html,
        f"https://rutracker.org/forum/viewtopic.php?t={topic_id}",
        "https://rutracker.org",
    )


def field(label: str, value: str):
    return f'<span class="post-b">{label}</span>: {value}<br>'


def test_author_contribution_label_and_compact_character_cast_are_cleaned():
    book = parse(
        "Наталья Манушкина - Секреты русского языка. Второй класс, 1 часть "
        "[Арина Кирсанова, Евгений Кондратьев, 128 кбит/с]",
        field("Автор", "Автор стихов и текста- Наталья Манушкина")
        + field(
            "Исполнитель",
            "Ваня Шишечкин-Арина Кирсанова, "
            "Мел Чернилыч Тряпкин-Евгений Кондратьев",
        ),
        "519217",
    )
    assert book.authors == ["Наталья Манушкина"]
    assert book.narrators == ["Арина Кирсанова", "Евгений Кондратьев"]


def test_profession_only_author_fragment_is_discarded():
    book = parse(
        'Аудиосборник "Каббала обо всем" - 2 [Михаэль Лайтман, 2009]',
        field("Автор", "ученый каббалист, профессор Михаэль Лайтман")
        + field("Исполнитель", "Михаэль Лайтман"),
        "1728168",
    )
    assert book.title == '"Каббала обо всем" - 2'
    assert book.authors == ["Михаэль Лайтман"]
    assert book.narrators == ["Михаэль Лайтман"]


def test_source_unnamed_female_narrator_is_preserved():
    book = parse(
        "Прайор Карен - Не рычите на собаку! [чтец-женщина (ЛИ), MP3]",
        field("Фамилия автора", "Прайор")
        + field("Имя автора", "Карен")
        + field("Исполнитель", "чтец-женщина (ЛИ)"),
        "4375239",
    )
    assert book.narrators == ["чтец-женщина"]


def test_decorative_equals_row_is_not_part_of_title():
    book = parse(
        "Пушкин Александр, Лермонтов Михаил - Великие исполнители - 19. "
        "Михаил Царев [Михаил Царев, 2012, MP3]",
        '<span class="post-align">Великие исполнители - 19 '
        '= = = = = = = = = Михаил Царев</span><br>'
        + field("Авторы", "Пушкин Александр, Лермонтов Михаил")
        + field("Исполнитель", "Михаил Царев"),
        "4296322",
    )
    assert book.title == "Великие исполнители - 19. Михаил Царев"


def test_anonymous_author_release_prefix_is_removed_without_inventing_author():
    book = parse(
        'Норбеков - аудиокнига "ОНИЗАК"+Тренажёр интеллекта [2007]',
        field(
            "Описание",
            "Книга одного из членов клуба; по определённым причинам "
            "имя автора не раскрывается.",
        ),
        "506436",
    )
    assert book.title == '"ОНИЗАК"+Тренажёр интеллекта'
    assert book.authors == []


def test_literal_topic_urls_are_removed_from_series_entry_titles():
    body = (
        field("Автор", "Пратчетт Терри")
        + field("Исполнитель", "Капитан Абр")
        + field("Цикл/серия", "Плоский мир. Ринсвинд.")
        + field("Номер книги", "3")
        + "Весь подцикл и существующие раздачи аудиокниг из него:<br>"
        + '1. Цвет волшебства <a href="viewtopic.php?t=2812211">'
        "https://rutracker.org/forum/viewtopic.php?t=2812211</a> (Д-р Лутц) "
        + '<a href="viewtopic.php?t=1054733">'
        "https://rutracker.org/forum/viewtopic.php?t=1054733</a><br>"
        + "2. Безумная звезда<br>"
        + "3. Посох и шляпа - эта раздача<br>"
    )
    book = parse(
        "Пратчетт Терри - Посох и шляпа. Плоский мир. Ринсвинд- 3",
        body,
        "4522727",
    )
    assert [(x.position, x.title) for x in book.series_entries] == [
        (1, "Цвет волшебства"),
        (2, "Безумная звезда"),
        (3, "Посох и шляпа"),
    ]
    assert book.series_entries[0].external_id == "2812211"


def test_chapter_contents_before_explicit_cycle_list_are_ignored():
    body = (
        field("Фамилия автора", "Вилар")
        + field("Имя автора", "Симона")
        + field("Исполнитель", "Татьяна Мещерякова")
        + field("Цикл/серия", "Анна Невиль")
        + field("Номер книги", "01")
        + '<div class="sp-wrap"><div class="sp-head">Оглавление</div>'
        + '<div class="sp-body">1. Рыцарский турнир<br>'
        + "2. Королева вспоминает<br>3. Братья<br></div></div>"
        + '<span class="post-b">Книги цикла «Анна Невиль»</span><br>'
        + '01. <a href="viewtopic.php?t=6612015">Обручённая с розой</a><br>'
        + '02. <a href="viewtopic.php?t=2349720">Делатель королей</a><br>'
        + '03. <a href="viewtopic.php?t=6734384">Замок на скале</a><br>'
    )
    book = parse(
        "Вилар Симона - «Анна Невиль» 01, Обрученная с Розой",
        body,
        "6612015",
    )
    assert [(x.position, x.external_id, x.title) for x in book.series_entries] == [
        (1, "6612015", "Обручённая с розой"),
        (2, "2349720", "Делатель королей"),
        (3, "6734384", "Замок на скале"),
    ]


def test_bare_bold_cycle_heading_starts_real_list_after_contents():
    body = (
        field("Фамилия автора", "Бакман")
        + field("Имя автора", "Фредрик")
        + field("Исполнитель", "Радциг Кирилл")
        + field("Цикл/серия", "Медвежий угол")
        + field("Номер книги", "02")
        + "Содержание<br>1. Это случится по чьей-то вине<br>"
        + "2. Люди бывают трех сортов<br>3. Будь мужчиной<br>"
        + '<hr><span class="post-b">«Медвежий угол»</span>:<br>'
        + '1. <a href="viewtopic.php?t=5691148">Медвежий угол</a><br>'
        + '2. <span class="post-b">Мы против вас</span><br>'
    )
    book = parse(
        "Бакман Фредрик - Медвежий угол 02, Мы против вас",
        body,
        "5815669",
    )
    assert [(x.position, x.external_id, x.title) for x in book.series_entries] == [
        (1, "5691148", "Медвежий угол"),
        (2, "5815669", "Мы против вас"),
    ]


def test_nested_subcycle_is_not_flattened_into_outer_reading_order():
    middle = "".join(
        f"{position}. Книга {position}<br>" for position in range(4, 12)
    )
    middle += (
        "12. «Черновик беса» (1913 г.) т.е. как раз перед событиями "
        "романа «Лик над пропастью».<br>"
    )
    middle += "".join(
        f"{position}. Книга {position}<br>" for position in range(13, 19)
    )
    body = (
        field("Автор", "Любенко Иван")
        + field("Исполнитель", "Актерский коллектив")
        + field("Цикл/серия", "Клим Ардашев")
        + field("Номер книги", "19")
        + '<div class="sp-wrap"><div class="sp-head">Цикл «Клим Ардашев»</div>'
        + '<div class="sp-body">'
        + '1. <a href="viewtopic.php?t=5660693">«Маскарад со смертью»</a><br>'
        + '2. <a href="viewtopic.php?t=6027455">«Серый монах»</a><br>'
        + '3. <a href="viewtopic.php?t=6064368">«Поцелуй анаконды» '
        + '(1908 г.); книга вышла в 2015 г.</a><br>'
        + middle
        + '19. <a href="viewtopic.php?t=6095647">«Мёртвое пианино»</a><br>'
        + '20. <a href="tracker.php?nm=Путешествие">'
        + 'Путешествие за смертью (1920 г.)</a>'
        + '<ol><li><a href="viewtopic.php?t=6188165">1. Могильщик из Таллина</a></li>'
        + '<li><a href="viewtopic.php?t=6232505">2. Визитёр из Сан-Франциско</a></li>'
        + '<li><a href="viewtopic.php?t=6302534">3. Душегуб из Нью-Йорка</a></li>'
        + '</ol></div></div>'
    )
    book = parse(
        "Любенко Иван - Клим Ардашев 19, Мёртвое пианино. Аудиоспектакль",
        body,
        "6095647",
    )

    assert [entry.position for entry in book.series_entries] == list(range(1, 21))
    assert book.series_entries[2].title == "«Поцелуй анаконды» (1908 г.)"
    assert book.series_entries[11].title == "«Черновик беса» (1913 г.)"
    assert book.series_entries[19].title == "Путешествие за смертью (1920 г.)"
    assert not {
        "6188165", "6232505", "6302534",
    } & {entry.external_id for entry in book.series_entries}


def test_br_separated_outer_series_inside_ol_is_preserved():
    body = (
        field("Автор", "Юрт Микаэль, Русенфельдт Ханс")
        + field("Исполнитель", "Станислав Иванов")
        + field("Цикл/серия", "Себастиан Бергман")
        + field("Номер книги", "6")
        + '<div class="sp-wrap"><div class="sp-head">'
        + 'Произведения цикла «Себастиан Бергман»</div><div class="sp-body">'
        + '<ol class="post-ul">📘 1. Тёмные тайны (2010)<br>'
        + '📘 2. <a href="viewtopic.php?t=6185495">Ученик</a> (2011)<br>'
        + '📘 3. <a href="viewtopic.php?t=6185497">Могила в горах</a> (2012)<br>'
        + '📘 4. <a href="viewtopic.php?t=6185499">Немая девочка</a> (2014)<br>'
        + '📘 5. <a href="viewtopic.php?t=6185502">Провал</a> (2015)<br>'
        + '📘 6. <a href="viewtopic.php?t=6185508">Высшая справедливость</a> '
        + '(2018)</ol></div></div>'
    )
    book = parse(
        "Юрт Микаэль, Русенфельдт Ханс - Себастиан Бергман 6, "
        "Высшая справедливость",
        body,
        "6185508",
    )

    assert [entry.position for entry in book.series_entries] == list(range(1, 7))
    assert [entry.external_id for entry in book.series_entries[1:]] == [
        "6185495", "6185497", "6185499", "6185502", "6185508",
    ]


def test_generic_cycle_contents_heading_starts_series_list():
    body = (
        field("Автор", "Гончарова Галина")
        + field("Исполнитель", "Татьяна Черничкина")
        + field("Цикл/серия", "Средневековая история")
        + field("Номер книги", "11")
        + '<span class="post-b">Содержание цикла:</span><br>'
        + "1. Домашняя работа<br>"
        + "2. Интриги королевского двора<br>"
        + "11. Чужие миры Книги цикла на трекере<br>"
    )
    book = parse(
        "Гончарова Галина - Средневековая история 11, Чужие миры",
        body,
        "6317627",
    )
    assert [(x.position, x.title) for x in book.series_entries] == [
        (1, "Домашняя работа"),
        (2, "Интриги королевского двора"),
        (11, "Чужие миры"),
    ]


def test_cycle_spoiler_skips_notes_between_numbered_books():
    body = (
        field("Автор", "Зинина Татьяна")
        + field("Цикл/серия", "Союз Человеческих Рас")
        + field("Номер книги", "2")
        + '<div class="sp-wrap"><div class="sp-head">Книги цикла</div>'
        + '<div class="sp-body">1. Эргай. Новая эра Земли<br>'
        + "+ Не подглядывай! *рассказ-приквел<br>"
        + "2. Аделия. Позор рода *самостоятельный однотомник<br>"
        + "3. Пари с наследником Земли</div></div>"
    )
    book = parse(
        "Зинина Татьяна - Союз Человеческих Рас 2, Аделия. Позор рода",
        body,
        "6534665",
    )
    assert [(x.position, x.title) for x in book.series_entries] == [
        (1, "Эргай. Новая эра Земли"),
        (2, "Аделия. Позор рода"),
        (3, "Пари с наследником Земли"),
    ]


def test_placeholder_author_and_leading_title_dash_are_removed():
    book = parse(
        "- Любимые котлеты [Соболева Елена, 2015, MP3]",
        field("Фамилия автора", "---")
        + field("Имя автора", "---")
        + field("Исполнитель", "Соболева Елена"),
        "5041634",
    )
    assert book.title == "Любимые котлеты"
    assert book.authors == []


def test_shared_family_name_and_split_complete_authors_are_repaired():
    kovi = parse(
        "Кови Шон, Кови Стивен Р. - Семь навыков на каждый день",
        field("Фамилия автора", "Кови")
        + field("Имя автора", "Шон, Стивен Р."),
        "6567565",
    )
    split = parse(
        "Величкина М., Нароков Н. - Дело №1937",
        field("Фамилия автора", "Величкина М., Нароков Н.")
        + field("Имя автора", "—"),
        "4840388",
    )
    assert kovi.authors == ["Кови Шон", "Кови Стивен Р."]
    assert split.authors == ["Величкина М.", "Нароков Н."]


def test_screenplay_authors_and_role_cast_are_recovered_from_description():
    book = parse(
        "Клуб знаменитых капитанов (9 выпусков)",
        '<span class="post-b">Описание</span>: '
        "Авторы сценария: Владимир Крепс, Климентий Минц.<br>"
        + "Действующие лица и исполнители:<br>"
        + "Гулливер - Б. Лавров<br>Катюша - Н. Львова<br>"
        + field("Доп. информация", "Запись 1945-47 годов"),
        "165756",
    )
    assert book.authors == ["Владимир Крепс", "Климентий Минц"]
    assert book.narrators == ["Б. Лавров", "Н. Львова"]


def test_malformed_release_tail_ebook_suffix_and_quoted_list_are_cleaned():
    quran = parse(
        "Коран Шейх Сауд Шурейм, 2016 г., 128 kbps, MP3]",
        field("Исполнитель", "Шейх Сауд Шурейм"),
        "5254713",
    )
    eret = parse(
        "Арнольд Эрет - Целебная Система Бесслизистой Диеты + DOC, FB2, EPUB "
        "[mixmaslab, 80kbps, MP3]",
        field("Фамилия автора", "Эрет")
        + field("Имя автора", "Арнольд"),
        "4642405",
    )
    dante = parse(
        'Алигьери Данте - "Божественная комедия", "Ад" [Кирилл Пирогов, 256 кбит/с]',
        field("Автор", "Алигьери Данте")
        + field("Исполнитель", "читает - Кирилл Пирогов"),
        "112144",
    )
    assert quran.title == "Коран Шейх Сауд Шурейм"
    assert eret.title == "Целебная Система Бесслизистой Диеты"
    assert dante.title == '"Божественная комедия", "Ад"'
    assert dante.narrators == ["Кирилл Пирогов"]


def test_donation_banner_is_not_selected_as_body_title():
    book = parse(
        'Дойль Артур Конан - Профессор Челленджер. Театр у микрофона 52 - "Затерянный мир" '
        "[артисты театров]",
        'Театр у микрофона 52 - Артур Конан Дойль "Затерянный мир"<br>'
        '<span class="post-align">Помощь | Донаты | Donations</span><br>'
        + field("Исполнитель", "артисты театров"),
        "712767",
    )
    assert book.authors == ["Дойль Артур Конан"]
    assert book.title == 'Профессор Челленджер. Театр у микрофона 52 - "Затерянный мир"'


def test_release_type_suffix_is_removed_without_touching_qualifiers():
    spectacle = parse(
        "Любенко Иван - Клим Ардашев 19, Мёртвое пианино. "
        "Аудиоспектакль [Актерский коллектив, 2021, MP3]",
        field("Автор", "Любенко Иван")
        + field("Исполнитель", "Актерский коллектив")
        + field("Цикл/серия", "Клим Ардашев")
        + field("Номер книги", "19"),
        "6095647",
    )
    quran = parse(
        "Абдерауф Даккак - Коранические рассказы для детей [Коран]",
        field("Автор", "Абдерауф Даккак"),
        "870349",
    )
    assert spectacle.title == "Клим Ардашев 19, Мёртвое пианино"
    assert quran.title == "Коранические рассказы для детей [Коран]"


def test_meaningful_audiobook_words_are_preserved():
    deep = parse(
        "В.Н. Пятибрат - Глубинная аудиокнига "
        "[Михаил Миронов, 2013-2015, MP3]",
        field("Автор", "Пятибрат В.Н.")
        + field("Исполнитель", "Михаил Миронов"),
        "5763223",
    )
    craft = parse(
        "Уделов Сергей Владимирович - Технология создания аудиокниг, "
        "или Как реально зарабатывать, записывая аудиокниги",
        field("Автор", "Уделов Сергей Владимирович"),
        "6332202",
    )
    contest = parse(
        "Первый Открытый Конкурс Чтецов Клуба Любителей Аудиокниг "
        "Открывашка №1 [Участники конкурса, 2010, 128 kbps]",
        field("Исполнитель", "Участники конкурса"),
        "2829095",
    )
    assert deep.title == "Глубинная аудиокнига"
    assert craft.title == (
        "Технология создания аудиокниг, или Как реально зарабатывать, "
        "записывая аудиокниги"
    )
    assert contest.title.endswith("Клуба Любителей Аудиокниг Открывашка №1")


def test_text_reader_and_joint_author_performers_are_recovered():
    prophet = parse(
        "- Аудиокнига «История жизни Пророка» [2014, 56 kbps, MP3]",
        field("Текст читает", "Кадыров Усман"),
        "4819114",
    )
    lectures = parse(
        'Ислам - Сборник лекций "К Исламу" [2008, MP3]',
        '<span class="post-b">Автор и исполнитель</span>:<br>'
        + "1 и 8 читает Салим Абу Умар аль-Газзи<br>"
        + "2 и 8 читает Наиль Абу Салих Казахстани<br>"
        + "Остальные лекции читает Ринат Абу Мухаммад Казахстани<br>"
        + field("Жанр", "Религиозные аудиолекции"),
        "2060400",
    )
    people = [
        "Салим Абу Умар аль-Газзи",
        "Наиль Абу Салих Казахстани",
        "Ринат Абу Мухаммад Казахстани",
    ]
    assert prophet.narrators == ["Кадыров Усман"]
    assert lectures.title == 'Сборник лекций "К Исламу"'
    assert lectures.authors == people
    assert lectures.narrators == people


def test_explicit_text_credit_and_collective_readers_are_preserved():
    gogol = parse(
        "(musical performance, audio fantacy) VA - Мокрые Уши. Гоголь. "
        "- 2014, MP3, 320 kbps",
        "Мокрые Уши. Гоголь.<br>"
        + "Текст:<br>Гоголь. Мертвые души.<br>"
        + '<span class="post-align">Набор в группу «Хранители» - '
        + "Помогите сохранить редкие раздачи</span>",
        "4809802",
    )
    poets = parse(
        "Поэты Серебряного века (голоса, видео) [читают авторы, WAW]",
        field("Жанр", "документальная запись"),
        "588559",
    )
    assert gogol.title == "Мокрые Уши. Гоголь."
    assert gogol.authors == ["Гоголь"]
    assert gogol.narrators == []
    assert poets.authors == []
    assert poets.narrators == ["Авторы"]


def test_unnamed_people_are_not_invented_from_translators_or_publishers():
    cases = [
        (
            "Аудиокнига «Усуль аль-Иман» [2014, MP3]",
            field("Перевод", "Эльмир Кулиев"),
        ),
        (
            "Аудиокнига: «Единобожие. Учебное пособие» [2014, MP3]",
            field("Издательство", "Умма"),
        ),
        (
            "Книги в кратком изложении Smart Reading [178 книги] [2017, MP3]",
            field("Издательство", "Smart Reading"),
        ),
    ]
    for index, (subject, body) in enumerate(cases):
        book = parse(subject, body, str(9000000 + index))
        assert book.authors == []
        assert book.narrators == []


def test_parenthesized_audiojournal_release_type_is_removed():
    book = parse(
        "Компьютерра №24(788) 23 июня 2009 года (аудиожурнал)",
        field("Тип", "аудиожурнал"),
        "2065492",
    )
    assert book.title == "Компьютерра №24(788) 23 июня 2009 года"
