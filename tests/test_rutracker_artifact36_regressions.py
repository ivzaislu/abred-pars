from abred_catalog_pipeline.rutracker.parser import _split_people, parse_topic_html


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


def series_spoiler(name: str, lines: str):
    return (
        '<div class="sp-wrap">'
        f'<div class="sp-head">Серия: {name}</div>'
        f'<div class="sp-body">{lines}</div>'
        '</div>'
    )


def test_people_split_respects_parentheses_and_collective_conjunctions():
    assert _split_people(
        "Рустем Булатов (Тэм, вокалист группы Lumen), Иван Иванов"
    ) == ["Рустем Булатов (Тэм, вокалист группы Lumen)", "Иван Иванов"]
    assert _split_people("PRV (psychedelic research volunteer, муж.)") == [
        "PRV (psychedelic research volunteer, муж.)"
    ]
    assert _split_people("Рустем Булатов (Тэм, вокалист группы Lumen )") == [
        "Рустем Булатов (Тэм, вокалист группы Lumen)"
    ]
    assert _split_people("Золотой Фонд звукозаписи для детей и юношества") == [
        "Золотой Фонд звукозаписи для детей и юношества"
    ]
    assert _split_people("М.Е. Литвак и Б.М. Литвак") == [
        "М.Е. Литвак",
        "Б.М. Литвак",
    ]


def test_author_role_suffixes_are_not_stored_in_names():
    book = parse(
        "Денис Денисов, Сергей Олексяк - Путешествие по звукам",
        field("Автор", "Денис Денисов - идея, Сергей Олексяк - стихи")
        + field("Исполнитель", "Юрий Гальцев"),
    )
    assert book.authors == ["Денис Денисов", "Сергей Олексяк"]


def test_author_time_range_note_is_not_stored_as_a_second_author():
    book = parse(
        "Английская поэзия в русских переводах (XIV — XIX века)",
        field("Авторы", "Английские поэты, до 19 века")
        + field("Исполнитель", "Лебедева Валерия"),
        "6627830",
    )
    assert book.authors == ["Английские поэты"]
    assert book.narrators == ["Лебедева Валерия"]


def test_translator_note_is_not_stored_inside_author_name():
    book = parse(
        "Лао Цзы - Дао Дэ Цзин",
        field("Автор", "Лао Цзы (перевод Ян Хин-шун и С. Митчелл)"),
        "3967010",
    )
    assert book.authors == ["Лао Цзы"]


def test_narrator_roles_translators_and_unmatched_bracket_are_cleaned():
    book = parse(
        "Булычев Кир - Можно попросить Нину?",
        field("Автор", "Булычев Кир")
        + field(
            "Исполнители",
            "Реж.: Креминский Дмитрий, акт.: Вадим Райкин, "
            "Лиза Уварова, перевод Юлия Жиронкина, Анастасия Михеева]",
        ),
    )
    assert book.narrators == ["Вадим Райкин", "Лиза Уварова", "Анастасия Михеева"]


def test_narrator_character_and_speaker_prefixes_are_removed():
    book = parse(
        "Александр Солодовников - Джон - весёлое сердце",
        field("Автор", "Александр Солодовников")
        + field(
            "Исполнители",
            "от автора - народный артист России Рафаэль Клейнер, "
            "Джон - Веселое сердце - Антон Макарский, "
            "Розина - Виктория Морозова, "
            "Мистер Комерциус и мистер Политикус - заслуженные артисты России "
            "Вячеслав Гарин и Алексей Ковалев, "
            "Лектор: Набиль Аль Авады, подробности далее",
        ),
    )
    assert book.narrators == [
        "Рафаэль Клейнер",
        "Антон Макарский",
        "Виктория Морозова",
        "Вячеслав Гарин",
        "Алексей Ковалев",
        "Набиль Аль Авады",
    ]


def test_abbreviated_narrator_honorific_is_removed():
    book = parse(
        "Бегство в Египет",
        field("Исполнитель", "нар.артист России Валерий Никитин"),
        "2030487",
    )
    assert book.narrators == ["Валерий Никитин"]


def test_unnamed_source_narrator_credits_are_preserved_verbatim():
    studio = parse(
        "Одесская Ариэлла - Дважды рожденные 2, Тэя",
        field("Автор", "Одесская Ариэлла")
        + field("Исполнитель", "Студия записи"),
        "5753797",
    )
    radio = parse(
        "Святогорец Паисий, монах - О помыслах",
        field("Автор", "Святогорец Паисий")
        + field("Исполнитель", 'диктор радио "Покров"'),
        "4129238",
    )
    assert studio.narrators == ["Студия записи"]
    assert radio.narrators == ['диктор радио "Покров"']
    assert radio.title == "О помыслах"


def test_legacy_hypnosis_subject_is_not_invented_as_an_author():
    book = parse(
        '"Русскоязычная Модель Эриксоновского Гипноза" '
        "(С. Горин) Семинар 30часов",
        field("Исполнитель", "Сергей Горин")
        + field("Жанр", "Нейролингвистическое программирование"),
        "161972",
    )
    assert book.authors == []
    assert book.narrators == ["Сергей Горин"]
    assert book.title == (
        '"Русскоязычная Модель Эриксоновского Гипноза" '
        "(С. Горин) Семинар 30часов"
    )


def test_collective_author_and_narrator_note_from_real_legacy_shape():
    book = parse(
        "Золотой Фонд звукозаписи для детей и юношества - "
        "От Сказки к Сказке - 4 [А.Папанов, М.Куприянова и др., 2007, 256 kbps]",
        field("Автор", "Золотой Фонд звукозаписи для детей и юношества")
        + field("Исполнитель", "Разные актеры, подробности далее"),
        "1532792",
    )
    assert book.authors == ["Золотой Фонд звукозаписи для детей и юношества"]
    assert book.narrators == ["Разные актеры"]


def test_total_duration_is_recovered_from_legacy_extra_information():
    clock_book = parse(
        "Александр Солодовников - Джон - весёлое сердце",
        field("Автор", "Александр Солодовников")
        + field("Доп. информация", "Общее звучание 42:54 Помощь проекту"),
        "2496534",
    )
    words_book = parse(
        "Золотой Фонд - От Сказки к Сказке - 4",
        field("Автор", "Золотой Фонд")
        + field(
            "Дополнительная информация",
            "Общее время звучания 6 ч. 10 мин. Оцифровка записей",
        ),
        "1532792",
    )
    assert clock_book.duration_seconds == 42 * 60 + 54
    assert words_book.duration_seconds == 6 * 3600 + 10 * 60


def test_total_duration_can_be_a_standalone_legacy_field_with_seconds():
    book = parse(
        "Первый Открытый Конкурс Чтецов",
        field("Общее время звучания", "5 час. 38 мин. 33 сек."),
        "2829095",
    )
    assert book.duration_seconds == 5 * 3600 + 38 * 60 + 33


def test_plus_separated_duration_variants_are_summed():
    book = parse(
        "Булатов Рустем - Простор оков",
        field("Автор", "Булатов Рустем")
        + field("Исполнитель", "Рустем Булатов (Тэм, вокалист группы Lumen )")
        + field("Время звучания", "00:36:08 + 00:29:21"),
        "4445105",
    )
    assert book.narrators == ["Рустем Булатов (Тэм, вокалист группы Lumen)"]
    assert book.duration_seconds == 36 * 60 + 8 + 29 * 60 + 21


def test_unlabelled_times_in_extra_information_are_not_used_as_duration():
    book = parse(
        "Тестовая книга",
        field("Автор", "Тестовый Автор")
        + field("Доп. информация", "Глава 01 — 42:54; запись 6 ч. 10 мин."),
    )
    assert book.duration_seconds == 0


def test_genre_suffix_is_not_kept_in_legacy_subject_title():
    book = parse(
        "Татьяна Толстая на философском факультете МГУ (аудио) "
        "[Встречи с писателями]",
        field("Жанр", "Встречи с писателями")
        + field("Продолжительность", "2ч 35мин"),
        "2332733",
    )
    assert book.title == "Татьяна Толстая на философском факультете МГУ (аудио)"
    assert book.genres == ["Встречи с писателями"]
    assert book.duration_seconds == 2 * 3600 + 35 * 60


def test_series_track_list_is_not_mistaken_for_books():
    body = (
        field("Автор", "Хьорт Микаэль")
        + field("Цикл/серия", "Себастиан Бергман")
        + series_spoiler(
            "Себастиан Бергман",
            "1. ЧАСТЬ ПЕРВАЯ #01 [38:10]<br>"
            "2. ЧАСТЬ ПЕРВАЯ #02 [35:29]<br>"
            "2. ЧАСТЬ ВТОРАЯ #02 [57:11]<br>"
            "3. Эпилог [10:11]<br>",
        )
    )
    book = parse("Хьорт Микаэль - Себастиан Бергман 6, Высшая справедливость", body)
    assert book.series_name == "Себастиан Бергман"
    assert book.series_entries == []
    assert book.series_external_id == ""


def test_contents_and_real_cycle_list_are_distinguished_on_same_page():
    body = (
        field("Авторы", "Юрт Микаэль, Русенфельдт Ханс")
        + field("Исполнитель", "Станислав Иванов")
        + field("Цикл/серия", "Себастиан Бергман")
        + field("Номер книги", "6")
        + '<div class="sp-wrap"><div class="sp-head">Содержание</div>'
        '<div class="sp-body">'
        "01. ЧАСТЬ ПЕРВАЯ #01 [34:42]<br>"
        "02. ЧАСТЬ ПЕРВАЯ #02 [35:29]<br>"
        "01. ЧАСТЬ ВТОРАЯ #01 [38:10]<br>"
        "02. ЧАСТЬ ВТОРАЯ #02 [57:11]<br>"
        "</div></div>"
        + '<div class="sp-wrap">'
        '<div class="sp-head">Произведения цикла «Себастиан Бергман»</div>'
        '<div class="sp-body">'
        "📘 1. Тёмные тайны (2010)<br>"
        "📘 2. Ученик (2011)<br>"
        "📘 3. Могила в горах (2012)<br>"
        "📘 4. Немая девочка (2014)<br>"
        "📘 5. Провал (2015)<br>"
        "📘 6. Высшая справедливость (2018) данная книга<br>"
        "📚 Циклы<br>"
        "</div></div>"
    )
    book = parse(
        "Юрт Микаэль, Русенфельдт Ханс - Себастиан Бергман 6, "
        "Высшая справедливость [Станислав Иванов, 2022, MP3]",
        body,
        "6185508",
    )
    assert book.series_name == "Себастиан Бергман"
    assert book.series_position == 6
    assert [(x.position, x.title) for x in book.series_entries] == [
        (1, "Тёмные тайны (2010)"),
        (2, "Ученик (2011)"),
        (3, "Могила в горах (2012)"),
        (4, "Немая девочка (2014)"),
        (5, "Провал (2015)"),
        (6, "Высшая справедливость (2018)"),
    ]
    assert book.series_entries[-1].external_id == "6185508"


def test_series_entry_stops_before_description_and_extra_information():
    body = (
        field("Автор", "Эпплгейт Кэтрин")
        + field("Цикл/серия", "Аниморфы")
        + series_spoiler(
            "Аниморфы",
            '1. <a href="viewtopic.php?t=10">Вторжение</a><br>'
            '2. <a href="viewtopic.php?t=11">Тайна</a> Описание: длинный текст<br>'
            '3. <a href="viewtopic.php?t=12">Выбор</a> Доп. информация: релиз клуба<br>',
        )
    )
    book = parse("Эпплгейт Кэтрин - Аниморфы 1, Вторжение", body, "10")
    assert [(x.position, x.title) for x in book.series_entries] == [
        (1, "Вторжение"),
        (2, "Тайна"),
        (3, "Выбор"),
    ]


def test_real_animorphs_heading_keeps_current_entry_and_drops_description():
    body = (
        field("Фамилия автора", "Эпплгейт")
        + field("Имя автора", "Кэтрин")
        + field("Исполнитель", "ANTON_K(ЛИ)")
        + field("Цикл/серия", "Аниморфы")
        + field("Номер книги", "1")
        + '<div class="sp-wrap">'
        '<div class="sp-head">Цикл Аниморфы (Вышедшие в России):</div>'
        '<div class="sp-body">'
        "1)Вторжение<br>2)Пришелец<br>3)Столкновение<br>4)Послание<br>"
        "5)Хищник<br>6)Пленник<br>7)Незнакомец<br>8)Чужой<br>"
        "9)Секрет<br>10)Андроид<br>11)Забвение<br>12)Аллергия<br>"
        "13)Выбор<br>14)Разведка<br>15)Тайна<br>"
        "</div></div>"
        + field("Описание", "Длинное описание этой книги")
    )
    book = parse(
        "Эпплгейт Кэтрин - Аниморфы 1, Вторжение "
        "[ANTON K (ЛИ), 2018, 112 kbps, MP3]",
        body,
        "5732834",
    )
    assert book.series_name == "Аниморфы"
    assert book.series_position == 1
    assert len(book.series_entries) == 15
    assert book.series_entries[0].external_id == "5732834"
    assert book.series_entries[-1].title == "Тайна"
    assert all("Описание" not in x.title for x in book.series_entries)


def test_real_ordered_cycle_list_uses_source_order_and_current_topic():
    body = (
        field("Автор", "Одесская Ариэлла")
        + field("Исполнитель", "Студия записи")
        + field("Цикл/серия", "Дважды рожденные")
        + field("Номер книги", "2")
        + '<span class="post-b">Цикл «Дважды рожденные»:</span>\n'
        + '<ol type="1">'
        + '<li><a href="viewtopic.php?t=5753794">Эллай</a></li>'
        + '<li><span class="post-b">Тэя</span></li>'
        + '<li><span class="post-b">Дэкс</span></li>'
        + '<li><span class="post-b">Каэн</span></li>'
        + '<li><span class="post-b">Айрин</span></li>'
        + '</ol>'
    )
    book = parse(
        "Одесская Ариэлла - Дважды рожденные 2, Тэя",
        body,
        "5753797",
    )
    assert [(x.position, x.title) for x in book.series_entries] == [
        (1, "Эллай"),
        (2, "Тэя"),
        (3, "Дэкс"),
        (4, "Каэн"),
        (5, "Айрин"),
    ]
    assert book.series_entries[0].external_id == "5753794"
    assert book.series_entries[1].external_id == "5753797"


def test_series_update_and_audio_file_rows_are_discarded():
    body = (
        field("Автор", "Тестовый Автор")
        + field("Цикл/серия", "Настоящий цикл")
        + series_spoiler(
            "Настоящий цикл",
            '1. <a href="viewtopic.php?t=20">Первая книга</a><br>'
            "2. 05.2026 Торрент перезалит.<br>"
            "3. Глава 01.mp3 продолжительность: 35:10 bitrate: 128 kb/s<br>",
        )
    )
    book = parse("Тестовый Автор - Настоящий цикл 1, Первая книга", body, "20")
    assert [(x.position, x.title) for x in book.series_entries] == [(1, "Первая книга")]


def test_series_release_annotations_are_not_part_of_book_titles():
    body = (
        field("Автор", "Тестовый Автор")
        + field("Исполнитель", "Ирина Ерисанова")
        + field("Цикл/серия", "Настоящий цикл")
        + series_spoiler(
            "Настоящий цикл",
            "1. Стань диким! // Автор: Кейт Кери<br>"
            "2. Седьмой свиток, в исполнении Ирины Ерисановой<br>"
            "3. Дело бога Плутоса - данная раздача<br>"
            "4. Тайный дневник. Ирина Ерисанова, (ЛИ), 2021<br>",
        )
    )
    book = parse("Тестовый Автор - Настоящий цикл 3, Дело бога Плутоса", body)
    assert [(x.position, x.title) for x in book.series_entries] == [
        (1, "Стань диким!"),
        (2, "Седьмой свиток"),
        (3, "Дело бога Плутоса"),
        (4, "Тайный дневник"),
    ]


def test_large_numbered_contents_are_not_a_series_entry_list():
    lines = "".join(f"{index}. Глава {index}<br>" for index in range(1, 61))
    body = (
        field("Автор", "Тестовый Автор")
        + field("Цикл/серия", "Слова")
        + series_spoiler("Слова", lines)
    )
    book = parse("Тестовый Автор - Слова. Том IV", body)
    assert book.series_entries == []


def test_numbered_poems_with_author_credits_are_not_a_book_series():
    poets = (
        "Константин Бальмонт",
        "Сильва Капутикян",
        "Омар Хайям",
        "Марина Цветаева",
        "Анна Ахматова",
        "Борис Пастернак",
        "Осип Мандельштам",
    )
    lines = "".join(
        f"{index}. Стихотворение {index} ({poet})<br>"
        for index, poet in enumerate(poets, start=1)
    )
    body = (
        field("Автор", "Тестовый Автор")
        + field("Цикл/серия", "Поэтическая библиотека")
        + series_spoiler("Поэтическая библиотека", lines)
    )
    book = parse("Тестовый Автор - Поэтический сборник", body)
    assert book.series_entries == []


def test_unlinked_short_book_list_is_still_preserved():
    body = (
        field("Автор", "Тестовый Автор")
        + field("Цикл/серия", "Трилле")
        + series_spoiler("Трилле", "1. Подкидыш<br>2. Трон<br>3. Королевство<br>")
    )
    book = parse("Тестовый Автор - Трилле 1, Подкидыш", body)
    assert [(x.position, x.title) for x in book.series_entries] == [
        (1, "Подкидыш"),
        (2, "Трон"),
        (3, "Королевство"),
    ]
