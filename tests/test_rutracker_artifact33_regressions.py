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


def field(label: str, value: str, *, post_break: bool = False):
    boundary = '<span class="post-br"><br></span>' if post_break else "<br>"
    return f'<span class="post-b">{label}</span>: {value}{boundary}'


def test_post_br_is_a_metadata_field_boundary():
    body = (
        field("Автор", "Смирнова Инна", post_break=True)
        + field("Звукорежисёр", "Елена Сытникова", post_break=True)
        + field("Читает", "Вячеслав Герасимов", post_break=True)
        + field("Издательство", "Рипол Классик", post_break=True)
    )
    book = parse("Смирнова Инна - Ясновидение [Вячеслав Герасимов, 2003, MP3]", body)
    assert book.authors == ["Смирнова Инна"]
    assert book.narrators == ["Вячеслав Герасимов"]


def test_duplicate_given_and_family_value_is_not_repeated():
    body = (
        field("Фамилия автора", "Далай-лама XIV, Чодрон")
        + field("Имя автора", "Далай-лама XIV, Тубтен")
        + field("Исполнитель", "Лобсанг Тенпа")
    )
    book = parse("Далай-лама XIV, Тубтен Чодрон - Буддизм [Лобсанг Тенпа, 2019, MP3]", body)
    assert book.authors == ["Далай-лама XIV", "Тубтен Чодрон"]


def test_radio_play_role_list_yields_people_not_description():
    body = (
        field("Автор", "Диккенс Чарльз")
        + field("Исполнитель", "Радиопостановка по одноименному роману", post_break=True)
        + "Исполнители:<br>"
        + "От автора - Алексей Консовский<br>"
        + "Дэви - Валентина Сперантова<br>"
        + "Моряки - Николай Салант, Михаил Названов<br>"
        + "Музыка - А. Крейн<br>"
        + field("Жанр", "драма")
    )
    book = parse("Диккенс Чарльз - Дэвид Копперфильд [1946, MP3]", body)
    assert book.narrators == [
        "Алексей Консовский",
        "Валентина Сперантова",
        "Николай Салант",
        "Михаил Названов",
    ]


def test_collective_narrator_is_not_split_on_conjunction():
    body = field("Исполнитель", "заслуженные и народные артисты России")
    book = parse("Ноябрь. Жития Святых [2008, 96]", body)
    assert book.narrators == ["заслуженные и народные артисты России"]


def test_current_topic_download_wins_over_related_downloads():
    body = '''
    <a href="dl.php?t=1453883">Сентябрь</a>
    <a href="dl.php?t=1453915">Октябрь</a>
    <a href="dl.php?t=1453947">Ноябрь</a>
    '''
    book = parse("Ноябрь. Жития Святых [2008, 96]", body, topic_id="1453947")
    assert book.torrent.torrent_url == "https://rutracker.org/forum/dl.php?t=1453947"


def test_cycle_series_heading_is_normalized():
    body = (
        field("Автор", "Емельянов Дмитрий")
        + '<a class="postLink">Цикл/серия «Тверской Баскак»</a>:<br>'
        + "Тверской Баскак. Книга 6<br>"
    )
    book = parse("Емельянов Дмитрий - Тверской Баскак. Книга 6 [2025, MP3]", body)
    assert book.series_name == "Тверской Баскак"


def test_related_topic_link_does_not_create_false_series():
    body = (
        field("Автор", "Литвак М.Е.")
        + '<a class="postLink" href="viewtopic.php?t=2">'
        + '<span class="post-b">Цикл лекций-тренингов «Личная победа»</span></a><br>'
    )
    book = parse("Литвак М.Е. - Как узнать свой сценарий [автор, 2007, MP3]", body)
    assert book.series_name == ""


def test_author_prefix_inside_value_is_removed_conservatively():
    book = parse(
        "Абу Али аль Ашари - Шарх Акыда [Абу Али, 2012, MP3]",
        field("Автор", "Автор Абу Али аль Ашари") + field("Исполнитель", "Абу Али"),
    )
    assert book.authors == ["Абу Али аль Ашари"]


def test_quoted_title_with_disc_suffix_stays_balanced():
    body = field("Автор", "М.Е. Литвак и Б.М. Литвак")
    book = parse(
        "М.Е. Литвак и Б.М. Литвак - М.Е. Литвак и Б.М. Литвак "
        "«Актуальные проблемы Вашей жизни» cd3 [2009, 128 kbps]",
        body,
    )
    assert book.title == "Актуальные проблемы Вашей жизни cd3"


def test_single_unclosed_guillemet_at_title_end_is_healed():
    body = field("Автор", "Шарма Робин") + field("Исполнитель", "Алексей Данков")
    book = parse(
        "Шарма Робин - Как побеждать от монаха, который продал свой «феррари "
        "[Алексей Данков, 2017, MP3]",
        body,
    )
    assert book.title == "Как побеждать от монаха, который продал свой «феррари»"
