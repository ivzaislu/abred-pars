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


def test_lee_is_an_author_surname_not_the_li_release_marker():
    book = parse(
        "Ли Брюс - Путь совершенства [Дунин Александр, 2019, 56 kbps, MP3]",
        field("Фамилия автора", "Ли")
        + field("Имя автора", "Брюс")
        + field("Исполнитель", "Дунин Александр"),
        "5678853",
    )
    assert book.title == "Путь совершенства"
    assert book.authors == ["Ли Брюс"]
    assert book.narrators == ["Дунин Александр"]

    service_marker = parse(
        "Автор - Название [ЛИ, 2020, MP3]",
        field("Автор", "Автор") + field("Исполнитель", "ЛИ"),
    )
    assert service_marker.narrators == []


def test_academic_honorific_is_removed_from_author_and_title_prefix():
    book = parse(
        "Академик Фотий Шипунов - Для полноценной жизни народа нужны принципы "
        "[Иоанн Николаев, 2010, 128 kbps]",
        '<span class="post-align">Для полноценной жизни народа нужны принципы</span><br>'
        + field("Автор", "Академик Фотий Шипунов")
        + field("Исполнитель", "Иоанн Николаев"),
        "3034421",
    )
    assert book.title == "Для полноценной жизни народа нужны принципы"
    assert book.authors == ["Фотий Шипунов"]


def test_exact_series_prefix_concatenation_uses_the_confirmed_body_title():
    book = parse(
        "Юдаков А. Ю. [Alex Crazy] - Вселенная Метро 2033 Путник "
        "[Никита Зуров, (ЛИ), 2020, 192 kbps, MP3]",
        '<span class="post-align">Путник</span><br>'
        + field("Фамилия автора", "Юдаков")
        + field("Имя автора", "А. Ю. [Alex Crazy]")
        + field("Исполнитель", "Никита Зуров")
        + field("Цикл/серия", "Вселенная Метро 2033"),
        "5877161",
    )
    assert book.title == "Путник"
    assert book.series_name == "Вселенная Метро 2033"


def test_multiple_subject_authors_are_recovered_without_an_author_field():
    people = (
        "Ницше Фридрих, Фрейд Зигмунд, Фромм Эрих, "
        "Камю Альбер, Сартр Жан-Поль"
    )
    book = parse(
        f"{people} - Сумерки богов [Мурашко Игорь, 2010, 96 kbps, MP3]",
        '<span class="post-align">'
        + people
        + " - Сумерки богов</span><br>"
        + field("Исполнитель", "Мурашко Игорь"),
        "3094260",
    )
    assert book.title == "Сумерки богов"
    assert book.authors == [
        "Ницше Фридрих",
        "Фрейд Зигмунд",
        "Фромм Эрих",
        "Камю Альбер",
        "Сартр Жан-Поль",
    ]


def test_cast_stops_before_production_credits_and_other_versions():
    body = (
        '<span class="post-align">Владимир Орлов «Альтист Данилов»</span><br>'
        + field("Автор", "Орлов Владимир")
        + "Действующие лица и исполнители:<br>"
        + "От автора – Кирилл Пирогов<br>"
        + "Данилов – Карэн Бадалов<br>"
        + "в других ролях – Александр Груздев, Илья Соболев<br>"
        + "Премьера на Радио Культура<br>"
        + "Автор радиоверсии – Татьяна Сахарова<br>"
        + "Другие версии:<br>"
        + "Орлов Владимир - Альтист Данилов [Олег Исаев, 2008, 128 kbps]<br>"
        + "Набор в группу «Хранители» - Помогите сохранить редкие раздачи"
    )
    book = parse(
        "Орлов Владимир - Альтист Данилов [К. Пирогов, 2012, MP3]",
        body,
        "4298333",
    )
    assert book.narrators == [
        "Кирилл Пирогов",
        "Карэн Бадалов",
        "Александр Груздев",
        "Илья Соболев",
    ]


def test_multiple_role_person_pairs_on_one_cast_line_are_normalized():
    book = parse(
        "Максим Горький - На дне [МХАТ, 2008, MP3]",
        field("Автор", "Максим Горький")
        + "Действующие лица и исполнители:<br>"
        + "Кривой Зоб - Григорий Конский, Татарин - А. Чебан<br>"
        + "Другие раздачи:<br>"
        + "Набор в группу «Хранители» - Помогите сохранить редкие раздачи",
        "2972335",
    )
    assert book.narrators == ["Григорий Конский", "А. Чебан"]


def test_attached_generic_cycle_heading_starts_series_list():
    body = (
        field("Автор", "Черчень Александра")
        + field("Исполнитель", "Анастасия Колесина")
        + field("Цикл/серия", "Фейри живут под холмами")
        + field("Номер книги", "1")
        + "Описание, к которому из-за старой разметки приклеились Книги цикла<br>"
        + "1. Дом на двоих<br>"
        + "2. Замок на двоих. Пряха короля эльфов<br>"
        + "3. Замок на двоих. Любовь короля эльфов<br>"
        + "4. Замок на двоих. Королева неблагого двора<br>"
    )
    book = parse(
        "Черчень Александра - Фейри живут под холмами 1, Дом на двоих",
        body,
        "6893870",
    )
    assert [(entry.position, entry.title) for entry in book.series_entries] == [
        (1, "Дом на двоих"),
        (2, "Замок на двоих. Пряха короля эльфов"),
        (3, "Замок на двоих. Любовь короля эльфов"),
        (4, "Замок на двоих. Королева неблагого двора"),
    ]


def test_named_cycle_heading_attached_to_extra_information_is_recognized():
    body = (
        field("Фамилия автора", "Тейлор")
        + field("Имя автора", "Деннис")
        + field("Исполнитель", "Кирилл Радциг")
        + field("Цикл/серия", "Вселенная Боба")
        + field("Номер книги", "03")
        + "Описание Доп. информация: Цикл «Вселенная Боба»:<br>"
        + '01. <a href="viewtopic.php?t=6103341">Мы — Легион. Мы — Боб</a><br>'
        + '02. <a href="viewtopic.php?t=6281103">Потому что нас много</a><br>'
        + "03. Все эти миры<br>"
        + "04. Heaven's River<br>"
    )
    book = parse(
        "Тейлор Деннис - Вселенная Боба 03, Все эти миры",
        body,
        "6361089",
    )
    assert [entry.position for entry in book.series_entries] == [1, 2, 3, 4]
    assert book.series_entries[2].external_id == "6361089"


def test_explicit_narration_order_uses_the_named_subseries_only():
    body = (
        '<span class="post-align">Алая Луна Зембабве</span><br>'
        + field("Фамилия автора", "де Камп")
        + field("Имя автора", "Лайон Спрэг")
        + field("Исполнитель", "ANNIGILIATOR10")
        + field("Цикл/серия", "Конан, Сага о Конане, Ветра Аквилонии")
        + '<span class="post-b">"Сага о Конане" '
        + "(перечень в порядке озвучания)</span><br>"
        + '01. <a href="viewtopic.php?t=6599066">Конан и Четыре Стихии.</a><br>'
        + '02. <a href="viewtopic.php?t=6615080">Конан бросает вызов.</a><br>'
        + '20. <a href="viewtopic.php?t=6848968">Алая Луна Зембабве.</a><br>'
        + "Перечень аудиокниг о Конане в хронологическом порядке<br>"
        + "01. Дважды рождённые<br>"
    )
    book = parse(
        "де Камп Лайон Спрэг - Конан, Сага о Конане, "
        "Ветра Аквилонии Алая Луна Зембабве",
        body,
        "6848968",
    )
    assert book.title == "Алая Луна Зембабве"
    assert book.series_position == 20
    assert [(entry.position, entry.title) for entry in book.series_entries] == [
        (1, "Конан и Четыре Стихии."),
        (2, "Конан бросает вызов."),
        (20, "Алая Луна Зембабве."),
    ]
