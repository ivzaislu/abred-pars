from pathlib import Path

TEST = Path("tests/test_rutracker_title_cleanup.py")
text = TEST.read_text(encoding="utf-8")
block = r'''


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
'''
if "test_release_type_audiobook_prefix_is_removed_only_at_title_start" in text:
    raise SystemExit("tests already present")
TEST.write_text(text.rstrip() + block.rstrip() + "\n", encoding="utf-8")
print("added RuTracker audiobook prefix regression tests")
