from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARSER = ROOT / "src/abred_catalog_pipeline/rutracker/parser.py"
TESTS = ROOT / "tests/test_rutracker.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"Refusing to patch {label}: expected exactly one matching current-main block, found {count}. "
            "The repository changed; refresh the patch instead of forcing it."
        )
    return text.replace(old, new, 1)


def main() -> None:
    parser = PARSER.read_text(encoding="utf-8")
    tests = TESTS.read_text(encoding="utf-8")

    parser = replace_once(
        parser,
        '''def _label_value(text: str, labels: tuple[str, ...]) -> str:\n    for label in labels:\n        m = re.search(rf"(?im)^\\s*{re.escape(label)}\\s*(?:\\n\\s*)?[:：]\\s*(.+?)\\s*$", text)\n        if m:\n            return _clean(m.group(1))\n    return ""\n\n\n_STANDALONE_PEOPLE_NOISE = {\n''',
        '''def _label_value(text: str, labels: tuple[str, ...]) -> str:\n    for label in labels:\n        m = re.search(rf"(?im)^\\s*{re.escape(label)}\\s*(?:\\n\\s*)?[:：]\\s*(.+?)\\s*$", text)\n        if m:\n            return _clean(m.group(1))\n    return ""\n\n\ndef _normalized_post_label(value: str) -> str:\n    return _clean(value).rstrip(":：").strip().casefold()\n\n\n_STANDALONE_PEOPLE_NOISE = {\n''',
        "normalized post label helper",
    )

    parser = replace_once(
        parser,
        '''def _post_field(post, labels: tuple[str, ...]) -> str:\n    if post is None:\n        return ""\n    wanted = {x.casefold() for x in labels}\n    for bold in post.select("span.post-b"):\n        label = _clean(bold.get_text(" ", strip=True)).casefold()\n        if label not in wanted:\n''',
        '''def _post_field(post, labels: tuple[str, ...]) -> str:\n    if post is None:\n        return ""\n    wanted = {_normalized_post_label(x) for x in labels}\n    for bold in post.select("span.post-b"):\n        label = _normalized_post_label(bold.get_text(" ", strip=True))\n        if label not in wanted:\n''',
        "post field label normalization",
    )

    parser = replace_once(
        parser,
        '''def _post_field_present(post, post_text: str, labels: tuple[str, ...]) -> bool:\n    wanted = {x.casefold() for x in labels}\n    if post is not None:\n        for bold in post.select("span.post-b"):\n            label = _clean(bold.get_text(" ", strip=True)).casefold()\n            if label in wanted:\n''',
        '''def _post_field_present(post, post_text: str, labels: tuple[str, ...]) -> bool:\n    wanted = {_normalized_post_label(x) for x in labels}\n    if post is not None:\n        for bold in post.select("span.post-b"):\n            label = _normalized_post_label(bold.get_text(" ", strip=True))\n            if label in wanted:\n''',
        "post field presence label normalization",
    )

    parser = replace_once(
        parser,
        '''    for bold in post.select("span.post-b"):\n        if _clean(bold.get_text(" ", strip=True)).casefold() != "описание":\n            continue\n''',
        '''    for bold in post.select("span.post-b"):\n        if _normalized_post_label(bold.get_text(" ", strip=True)) != "описание":\n            continue\n''',
        "description label normalization",
    )

    parser = replace_once(
        parser,
        '''_KNOWN_POST_LABELS = {\n    "год выпуска", "автор", "авторы", "фамилия автора", "имя автора",\n''',
        '''_KNOWN_POST_LABELS = {\n    "год выпуска", "автор", "авторы", "aвтор", "aвторы",\n    "фамилия автора", "имя автора", "фамилия и имя автора", "фамилии авторов",\n''',
        "known legacy labels",
    )

    parser = replace_once(
        parser,
        '''def _topic_authors(post, post_text: str) -> list[str]:\n    direct = (\n        _post_field(post, ("Автор", "Авторы", "Фамилия и имя автора"))\n        or _label_value(post_text, ("Автор", "Авторы", "Фамилия и имя автора"))\n    )\n''',
        '''def _topic_authors(post, post_text: str) -> list[str]:\n    direct_labels = (\n        "Автор", "Авторы", "Aвтор", "Aвторы",\n        "Фамилия и имя автора", "Фамилии авторов",\n    )\n    direct = _post_field(post, direct_labels) or _label_value(post_text, direct_labels)\n''',
        "legacy author labels",
    )

    parser = replace_once(
        parser,
        '''def _infer_author_from_subject(raw_topic_title: str, body_title: str) -> str:\n    raw_value = _clean(raw_topic_title)\n    value, _ = _strip_release_suffix(raw_topic_title)\n    sep_re = re.compile(r"(?:\\s+[-–—]\\s*|\\s*[-–—]\\s+)")\n    for match in sep_re.finditer(value):\n        left = _clean(value[:match.start()])\n        right = _clean(value[match.end():])\n        if not left or not right or not _looks_like_person_prefix(left):\n            continue\n        if _body_title_related_to_subject(body_title, right):\n            return left\n\n    colon = re.match(r"^(.{2,100}?):\\s+(.+)$", value)\n''',
        '''def _infer_author_from_subject(raw_topic_title: str, body_title: str) -> str:\n    raw_value = _clean(raw_topic_title)\n    value, _ = _strip_release_suffix(raw_topic_title)\n    body_clean, _ = _strip_release_suffix(body_title)\n    body_key, _ = _alnum_compact(body_clean)\n    value_key, _ = _alnum_compact(value)\n\n    def subject_match(right: str) -> bool:\n        right = _clean(right).strip('«»"“” ')\n        if not right:\n            return False\n        return (\n            _body_title_related_to_subject(body_title, right)\n            or _body_title_related_to_subject(right, body_title)\n            or bool(body_key and value_key and body_key == value_key)\n        )\n\n    sep_re = re.compile(r"(?:\\s+[-–—]\\s*|\\s*[-–—]\\s+)")\n    for match in sep_re.finditer(value):\n        left = _clean(value[:match.start()])\n        right = _clean(value[match.end():])\n        if not left or not right or not _looks_like_person_prefix(left):\n            continue\n        if subject_match(right):\n            return left\n\n    quote = re.match(r'^(.{2,100}?)\\s*[«"“](.+)$', value)\n    if quote:\n        left = _clean(quote.group(1))\n        right = _clean(quote.group(2))\n        if _looks_like_person_prefix(left) and subject_match(right):\n            return left\n\n    # Old topics also use "Author. Title" and "Surname I.O. Title".\n    # Include the separator dot in the candidate so a final initial keeps it.\n    for match in re.finditer(r"\\.\\s+", value):\n        left = _clean(value[:match.start() + 1])\n        right = _clean(value[match.end():])\n        if not left or not right or not _looks_like_person_prefix(left):\n            continue\n        if subject_match(right):\n            return left\n\n    colon = re.match(r"^(.{2,100}?):\\s+(.+)$", value)\n''',
        "strict legacy subject author inference",
    )

    parser = replace_once(
        parser,
        '''    segments = re.split(r"\\s+[-–—]\\s*|\\s*[-–—]\\s+", value)\n''',
        '''    if authors:\n        quote = re.match(r'^(.{2,100}?)\\s*[«"“](.+)$', value)\n        if quote:\n            left = _clean(quote.group(1))\n            right = _clean(quote.group(2)).strip('«»"“” ')\n            if right and (\n                any(_same_person(left, author) for author in authors)\n                or _author_prefix_matches(left, authors)\n            ):\n                value = right\n        else:\n            for dot in re.finditer(r"\\.\\s+", value):\n                left = _clean(value[:dot.start() + 1])\n                right = _clean(value[dot.end():]).strip('«»"“” ')\n                if not right:\n                    continue\n                if (\n                    any(_same_person(left, author) for author in authors)\n                    or _author_prefix_matches(left, authors)\n                ):\n                    value = right\n                    break\n\n    segments = re.split(r"\\s+[-–—]\\s*|\\s*[-–—]\\s+", value)\n''',
        "legacy author removal from title",
    )

    parser = replace_once(
        parser,
        '''    return ""\n\n\ndef _select_topic_title(raw_topic_title: str, body_title: str, authors: list[str]) -> str:\n''',
        '''    return ""\n\n\ndef _looks_like_subject_narrator(value: str, *, allow_single_alias: bool = False) -> bool:\n    text = _clean(value)\n    if not text or any(ch.isdigit() for ch in text) or _RELEASE_META_RE.search(text):\n        return False\n    if _looks_like_person_prefix(text):\n        return True\n    # One-word pseudonyms are ambiguous. Accept them only when the release\n    # explicitly carried the RuTracker (ЛИ) narrator marker.\n    return bool(\n        allow_single_alias\n        and re.fullmatch(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё'’\\-]{2,39}", text)\n    )\n\n\ndef _infer_narrators_from_subject(raw_topic_title: str) -> list[str]:\n    value = _clean(raw_topic_title)\n    match = re.search(r"\\[([^\\[\\]]*)\\]\\s*$", value)\n    if not match:\n        return []\n\n    release = _clean(match.group(1))\n    technical = _RELEASE_META_RE.search(release)\n    if not technical:\n        return []\n\n    people_segment = _clean(release[:technical.start()]).rstrip(" ,;/")\n    if not people_segment:\n        return []\n\n    had_li_marker = bool(re.search(r"(?i)\\(\\s*ЛИ\\s*\\)", people_segment))\n    out: list[str] = []\n    for candidate in _split_people(people_segment):\n        if not _looks_like_subject_narrator(candidate, allow_single_alias=had_li_marker):\n            continue\n        if candidate not in out:\n            out.append(candidate)\n    return out\n\n\ndef _select_topic_title(raw_topic_title: str, body_title: str, authors: list[str]) -> str:\n''',
        "strict subject narrator fallback",
    )

    parser = replace_once(
        parser,
        '''    author_labels = ("Автор", "Авторы", "Фамилия автора", "Имя автора", "Фамилия и имя автора")\n''',
        '''    author_labels = (\n        "Автор", "Авторы", "Aвтор", "Aвторы",\n        "Фамилия автора", "Имя автора", "Фамилия и имя автора", "Фамилии авторов",\n    )\n''',
        "parse_topic_html author labels",
    )

    parser = replace_once(
        parser,
        '''    narrators = _split_people(\n        _post_field(post, narrator_labels)\n        or _label_value(post_text, narrator_labels)\n    )\n''',
        '''    narrators = _split_people(\n        _post_field(post, narrator_labels)\n        or _label_value(post_text, narrator_labels)\n    )\n    if not narrators:\n        narrators = _infer_narrators_from_subject(raw_topic_title)\n''',
        "parse_topic_html narrator fallback",
    )

    regression_tests = r'''\n\ndef test_legacy_mixed_latin_author_label_is_parsed():\n    html = """\n    <html><body>\n      <h1 class="maintitle"><a id="topic-title">Вайн Барбара - Пятьдесят оттенков темноты[Кирсанов Сергей, 2015 г., 128 kbps, MP3]</a></h1>\n      <div class="post_body">\n        <span class="post-b">Aвтор</span>: Вайн Барбара<br>\n        <span class="post-b">Исполнитель</span>: Кирсанов Сергей<br>\n        <a class="magnet-link" href="magnet:?xt=urn:btih:0123456789012345678901234567890123456789">magnet</a>\n      </div>\n    </body></html>\n    """\n    book = parse_topic_html(html, "https://rutracker.org/forum/viewtopic.php?t=5158225", "https://rutracker.org")\n    assert book.authors == ["Вайн Барбара"]\n    assert book.narrators == ["Кирсанов Сергей"]\n\n\ndef test_legacy_plural_surname_author_label_and_li_narrator():\n    html = """\n    <html><body>\n      <h1 class="maintitle"><a id="topic-title">Хэйнс Дороти К., Матесон Ричард, Кинг Стивен - Ведьма [Владимир Князев(ЛИ), 2012 г., 256, MP3]</a></h1>\n      <div class="post_body">\n        <span class="post-b">Фамилии авторов</span>: Хэйнс Дороти К., Матесон Ричард, Кинг Стивен<br>\n        <span class="post-b">Исполнитель</span>: Владимир Князев(ЛИ)<br>\n        <a class="magnet-link" href="magnet:?xt=urn:btih:0123456789012345678901234567890123456789">magnet</a>\n      </div>\n    </body></html>\n    """\n    book = parse_topic_html(html, "https://rutracker.org/forum/viewtopic.php?t=4153936", "https://rutracker.org")\n    assert book.authors == ["Хэйнс Дороти К.", "Матесон Ричард", "Кинг Стивен"]\n    assert book.narrators == ["Владимир Князев"]\n\n\ndef test_bold_label_with_embedded_colon_is_recognized():\n    html = """\n    <html><body>\n      <h1 class="maintitle"><a id="topic-title">Фаулз Джон &quot;Коллекционер&quot; [А.Хорлин, Е.Морозова, 2005, 192 кбит/с]</a></h1>\n      <div class="post_body">\n        <span style="font-size:24px">Джон Фаулз &quot;Коллекционер&quot;</span><br>\n        <span class="post-b">Исполнители:</span> Александр Хорлин, Елена Морозова<br>\n        <span class="post-b">Описание:</span> Тестовое описание книги.<br>\n        <a class="magnet-link" href="magnet:?xt=urn:btih:0123456789012345678901234567890123456789">magnet</a>\n      </div>\n    </body></html>\n    """\n    book = parse_topic_html(html, "https://rutracker.org/forum/viewtopic.php?t=545082", "https://rutracker.org")\n    assert book.authors == ["Фаулз Джон"]\n    assert book.narrators == ["Александр Хорлин", "Елена Морозова"]\n    assert book.title == "Коллекционер"\n\n\ndef test_strict_legacy_author_fallback_formats():\n    cases = [\n        ('Евгений Весник &quot;Хмельные странички&quot; [Евгений Весник, 256 кбит/с]', 'Евгений Весник &quot;Хмельные странички&quot;', ["Евгений Весник"], "Хмельные странички"),\n        ('Кинки Фридман. &quot;Убийство по Гринвичу&quot;', 'Кинки Фридман. &quot;Убийство по Гринвичу&quot;', ["Кинки Фридман"], "Убийство по Гринвичу"),\n        ("Мигель Де Сервантес Сааведра. Дон Кихот", "Мигель Де Сервантес Сааведра. Дон Кихот", ["Мигель Де Сервантес Сааведра"], "Дон Кихот"),\n        ("Пушкин А.С. Евгений Онегин [Иннокентий Смоктуновский, 2005, 192 kbps]", "Пушкин А.С. Евгений Онегин", ["Пушкин А.С."], "Евгений Онегин"),\n        ("Киплинг Редьярд - Рикша-призрак. В кратере [А. Смоляков, М. Шашлова, М. Станкевич, 2010 г., 160 kbps, MP3]", "Рикша-призрак. В кратере", ["Киплинг Редьярд"], "Рикша-призрак. В кратере"),\n    ]\n    for index, (subject, body_title, authors, title) in enumerate(cases):\n        html = f"""\n        <html><body>\n          <h1 class="maintitle"><a id="topic-title">{subject}</a></h1>\n          <div class="post_body">\n            <span style="font-size:24px">{body_title}</span><br>\n            <a class="magnet-link" href="magnet:?xt=urn:btih:0123456789012345678901234567890123456789">magnet</a>\n          </div>\n        </body></html>\n        """\n        book = parse_topic_html(html, f"https://rutracker.org/forum/viewtopic.php?t={9000000 + index}", "https://rutracker.org")\n        assert book.authors == authors\n        assert book.title == title\n\n\ndef test_subject_narrator_fallback_is_conservative():\n    cases = [\n        ('Джеффри Дж.Фокс &quot;Как стать Волшебником Продаж&quot; [Виктор Петров, 2007]', ["Виктор Петров"]),\n        ('Евгений Весник &quot;Хмельные странички&quot; [Евгений Весник, 256 кбит/с]', ["Евгений Весник"]),\n        ("А.А.Ахматова - Стихотворения и поэма [И.Чурикова, А.Покровская, О.Остроумова и др., 2007, 192 кБ/с]", ["И.Чурикова", "А.Покровская", "О.Остроумова"]),\n        ("Тестовая книга [Комиссар (ЛИ), 2013 г., 256 kbps, MP3]", ["Комиссар"]),\n    ]\n    for index, (subject, expected) in enumerate(cases):\n        html = f"""\n        <html><body>\n          <h1 class="maintitle"><a id="topic-title">{subject}</a></h1>\n          <div class="post_body">\n            <span style="font-size:24px">Тестовая книга</span><br>\n            <a class="magnet-link" href="magnet:?xt=urn:btih:0123456789012345678901234567890123456789">magnet</a>\n          </div>\n        </body></html>\n        """\n        book = parse_topic_html(html, f"https://rutracker.org/forum/viewtopic.php?t={9100000 + index}", "https://rutracker.org")\n        assert book.narrators == expected\n\n\ndef test_explicit_narrators_win_over_subject_fallback():\n    html = """\n    <html><body>\n      <h1 class="maintitle"><a id="topic-title">Фаулз Джон &quot;Коллекционер&quot; [А.Хорлин, Е.Морозова, 2005, 192 кбит/с]</a></h1>\n      <div class="post_body">\n        <span class="post-b">Исполнители:</span> Александр Хорлин, Елена Морозова<br>\n        <a class="magnet-link" href="magnet:?xt=urn:btih:0123456789012345678901234567890123456789">magnet</a>\n      </div>\n    </body></html>\n    """\n    book = parse_topic_html(html, "https://rutracker.org/forum/viewtopic.php?t=545082", "https://rutracker.org")\n    assert book.narrators == ["Александр Хорлин", "Елена Морозова"]\n\n\ndef test_subject_fallback_refuses_non_person_va_release():\n    html = """\n    <html><body>\n      <h1 class="maintitle"><a id="topic-title">(musical performance, audio fantacy) VA - Мокрые Уши. Гоголь. - 2014, MP3, 320 kbps</a></h1>\n      <div class="post_body">\n        <span style="font-size:24px">Мокрые Уши. Гоголь.</span><br>\n        <span class="post-b">Жанр</span>: musical performance, audio fantacy<br>\n        <a class="magnet-link" href="magnet:?xt=urn:btih:0123456789012345678901234567890123456789">magnet</a>\n      </div>\n    </body></html>\n    """\n    book = parse_topic_html(html, "https://rutracker.org/forum/viewtopic.php?t=4809802", "https://rutracker.org")\n    assert book.authors == []\n    assert book.narrators == []\n'''

    if "test_legacy_mixed_latin_author_label_is_parsed" not in tests:
        tests += regression_tests

    PARSER.write_text(parser, encoding="utf-8")
    TESTS.write_text(tests, encoding="utf-8")

    # Remove the helper from the working tree after it has done its job so the
    # final branch diff can contain only parser.py + tests/test_rutracker.py.
    Path(__file__).unlink()

    print("Patched current RuTracker parser and regression tests.")
    print("Run: pytest -q")


if __name__ == "__main__":
    main()
