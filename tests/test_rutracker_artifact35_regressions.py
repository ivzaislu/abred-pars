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


def test_identical_single_author_fields_are_not_concatenated():
    body = (
        field("Фамилия автора", "Вильмонт Екатерина")
        + field("Имя автора", "Вильмонт Екатерина")
        + field("Исполнитель", "Ненарокомова Татьяна")
    )
    book = parse(
        "Вильмонт Екатерина - Здравствуй, груздь! "
        "[Ненарокомова Татьяна, 2010 г., 96kbps, MP3]",
        body,
    )
    assert book.authors == ["Вильмонт Екатерина"]


def test_translation_credit_in_given_name_field_is_not_appended_to_author():
    body = (
        field("Фамилия автора", "Шри Двайпаяна Вьяса.")
        + field("Имя автора", "Перевод на русский Б.Ч. Бхарати Свами")
        + field("Исполнитель", "Б.Ч. Бхарати Свами")
    )
    book = parse(
        'Шри Двайпаяна Вьяса. Шримад Бхагаватам "Книга мудрецов" [2009, MP3]',
        body,
    )
    assert book.authors == ["Шри Двайпаяна Вьяса"]


def test_narrator_honorific_is_not_stored_as_a_second_person():
    body = (
        field("Автор", "Шри Двайпаяна Вьяса.")
        + field("Исполнитель", "Заслуженный артист РФ, Сергей Русскин (Сундар Дути)")
    )
    book = parse(
        'Шри Двайпаяна Вьяса. Шримад Бхагаватам "Поколения" [2011, 256, MP3]',
        body,
    )
    assert book.narrators == ["Сергей Русскин (Сундар Дути)"]


def test_author_dot_separator_does_not_remove_title_closing_quote():
    body = field("Автор", "Шри Двайпаяна Вьяса.")
    book = parse(
        'Шри Двайпаяна Вьяса. Шримад Бхагаватам "Неизреченная Песнь"',
        body,
    )
    assert book.title == 'Шримад Бхагаватам "Неизреченная Песнь"'


def test_internal_quoted_title_after_author_keeps_both_quotes():
    body = (
        "Эдвард Радзинский.<br>"
        '"Господи... спаси и усмири Россию". Николай II: Жизнь и смерть<br>'
        + field("Читает", "Юрий Заборовский")
    )
    book = parse(
        'Эдвард Радзинский - "Господи... спаси и усмири Россию". '
        "Николай II: Жизнь и смерть [Юрий Заборовский, 128 kbps, MP3]",
        body,
    )
    assert book.title == '"Господи... спаси и усмири Россию". Николай II: Жизнь и смерть'


def test_standalone_cast_list_is_used_for_radio_play_narrators():
    body = (
        field("Автор", "Агата Кристи")
        + "В ролях:<br>"
        + "Янина Ясовская, Всеволод Абдулов, Александр Литовкин, "
        + "Владислав Долгоруков, Марк Гейфман<br>"
        + field("Жанр", "радиоспектакль")
    )
    book = parse("Агата Кристи - Коттедж Соловей (радиоспектакль ) [128 kbps]", body)
    assert book.title == "Коттедж Соловей (радиоспектакль)"
    assert book.narrators == [
        "Янина Ясовская",
        "Всеволод Абдулов",
        "Александр Литовкин",
        "Владислав Долгоруков",
        "Марк Гейфман",
    ]


def test_people_only_release_group_is_removed_from_legacy_subject():
    body = (
        "Джек Лондон - Звёздный странник (Смирительная рубашка)<br>"
        + field("Исполнители", "Наталья Данилова, Вадим Никитин, Валерий Соловьёв")
    )
    book = parse(
        "Джек Лондон - Звёздный странник (Смирительная рубашка) "
        "[Н.Данилова, В.Никитин, В.Соловьев]",
        body,
    )
    assert book.authors == ["Джек Лондон"]
    assert book.title == "Звёздный странник (Смирительная рубашка)"


def test_bad_gateway_service_prefix_is_removed_from_title():
    body = field("Автор", "Романычева Елена") + field("Исполнитель", "автор")
    book = parse(
        "Романычева Елена - bad gateway - Внутренний фронт [автор, 2017, MP3]",
        body,
    )
    assert book.title == "Внутренний фронт"


def test_quran_reader_tail_and_dangling_release_bracket_are_removed():
    body = field("Исполнитель", "Абу Бакар Шатри")
    book = parse(
        "Священный КОРАН/Abu Bakar Shatri [MP3] [Абу Бакар Шатри]",
        body,
    )
    assert book.title == "Священный КОРАН"
    assert book.narrators == ["Абу Бакар Шатри"]


def test_sermon_title_supplies_explicit_pastor_as_author_and_speaker():
    book = parse(
        "Аудиопроповеди пастора Алексея Коломийцева",
        "Аудиопроповеди пастора Алексея Коломийцева<br>"
        "Краткая биография: Алексей Коломийцев пастор-учитель.<br>",
    )
    assert book.authors == ["Алексей Коломийцев"]
    assert book.narrators == ["Алексей Коломийцев"]


def test_related_download_is_not_used_when_current_link_is_absent():
    body = '<a href="dl.php?t=1453883">Сентябрь</a>'
    book = parse("Ноябрь. Жития Святых [2008, 96]", body, topic_id="1453947")
    assert book.title == "Ноябрь. Жития Святых"
    assert book.torrent.torrent_url == "https://rutracker.org/forum/dl.php?t=1453947"


def test_ambiguous_vysotsky_composition_does_not_invent_people():
    book = parse(
        'Литературно-музыкальная композиция "А мне удел от Бога дан" В.Высоцкий '
        "[1999, 128 kbps]",
        "В композиции звучат стихи Владимира Высоцкого и духовная музыка.<br>",
    )
    assert book.authors == []
    assert book.narrators == []
