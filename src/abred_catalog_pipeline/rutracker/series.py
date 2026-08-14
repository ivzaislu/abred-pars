from __future__ import annotations

import re


# Old/malformed RuTracker markup can nest the value of `Цикл/серия` around
# subsequent metadata rows. BeautifulSoup then flattens all of them into one
# sibling value. These labels are hard boundaries: everything from the next
# field onward cannot be part of a series name.
_NEXT_FIELD_RE = re.compile(
    r"(?i)\s+(?=(?:"
    r"номер\s+(?:книги|в\s+(?:серии|цикле))|№\s*книги|"
    r"жанр(?:ы)?|издательство|категория|"
    r"аудиокодек|кодек|битрейт|вид\s+битрейта|качество|"
    r"время\s+звучания|общее\s+время\s+звучания|продолжительность|"
    r"описание|доп\.?\s*информация|дополнительная\s+информация|"
    r"год\s+выпуска|автор(?:ы)?|исполнитель(?:и)?|читает|чтец|"
    r"возрастн(?:ое|ые)\s+ограничени(?:е|я)"
    r")\s*[:：])"
)

_METADATA_LABEL_ONLY = {
    "номер книги",
    "номер в серии",
    "номер в цикле",
    "№ книги",
    "жанр",
    "жанры",
    "издательство",
    "категория",
    "аудиокодек",
    "кодек",
    "битрейт",
    "вид битрейта",
    "качество",
    "время звучания",
    "общее время звучания",
    "продолжительность",
    "описание",
    "доп. информация",
    "дополнительная информация",
    "год выпуска",
    "автор",
    "авторы",
    "исполнитель",
    "исполнители",
    "читает",
    "чтец",
}


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_series_name(value: str) -> str:
    """Вернуть только имя серии из потенциально повреждённого RuTracker field.

    Не обрезаем строку до размера backend-column: сначала отделяем следующие
    metadata-поля. Если надёжно выделить короткое имя всё равно не удалось,
    возвращаем пустое значение, чтобы одна битая карточка не блокировала весь
    feed структурной ошибкой.
    """
    text = _clean(value).strip(" -:：")
    if not text:
        return ""

    boundary = _NEXT_FIELD_RE.search(text)
    if boundary:
        text = _clean(text[:boundary.start()])

    text = re.sub(
        r"(?i)^/?(?:цикл\s*/\s*серия|серия\s*/\s*цикл|серия|цикл)"
        r"\s*(?:[:：\-]\s*)?",
        "",
        text,
        count=1,
    ).strip()

    # Common values are `Цикл «Перья»`, `Серия: "Название"` or just `Перья`.
    quoted = re.fullmatch(r"[«\"“](.+?)[»\"”]", text)
    if quoted:
        text = _clean(quoted.group(1))
    else:
        cycle_quote = re.fullmatch(r"(?i)(?:цикл|серия)?\s*[«\"“](.+?)[»\"”]", text)
        if cycle_quote:
            text = _clean(cycle_quote.group(1))

    text = _clean(text).strip(" -:：,;.")

    # Broken templates can expose the label of the next field itself as the
    # `Цикл/серия` value (production topic 4849650 produced "Номер книги").
    # A metadata label is not a catalog series name and must be discarded.
    if text.casefold() in _METADATA_LABEL_ONLY:
        return ""

    # A real series name can be long, but a flattened metadata block is not a
    # useful catalog value. Keep a margin well below Backend VARCHAR(512).
    if not text or len(text) > 240:
        return ""
    return text
