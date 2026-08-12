from pathlib import Path

PARSER = Path("src/abred_catalog_pipeline/rutracker/parser.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


text = PARSER.read_text(encoding="utf-8")

text = replace_once(
    text,
    '''_CATALOG_TITLE_TECH_RE = re.compile(\n    r"(?i)(?:"\n    r"\\b(?:аудиокнига|audio\\s*book|mp3pro|mp3|мр3|m4a|m4b|aac|ogg|opus|flac|wav|wma)\\b|"\n    r"\\brip\\s+\\d{2,4}\\s*(?:kbps|kbit/s|kb/s|кбит/с|кб/с)?\\b|"\n    r"\\b\\d{2,4}\\s*(?:kbps|kbit/s|kb/s|кбит/с|кб/с)\\b"\n    r")"\n)\n''',
    '''_CATALOG_TITLE_TECH_RE = re.compile(\n    r"(?i)(?:"\n    r"\\b(?:mp3pro|mp3|мр3|m4a|m4b|aac|ogg|opus|flac|wav|wma)\\b|"\n    r"\\brip\\s+\\d{2,4}\\s*(?:kbps|kbit/s|kb/s|кбит/с|кб/с)?\\b|"\n    r"\\b\\d{2,4}\\s*(?:kbps|kbit/s|kb/s|кбит/с|кб/с)\\b"\n    r")"\n)\n''',
    "generic technical title regex",
)

old = '''def _clean_catalog_title_candidate(value: str, authors: list[str]) -> str:\n    text = normalize_topic_subject_title(value, authors) if value else ""\n    if not text:\n        return ""\n    text = re.sub(r"(?i)\\s+[-–—]\\s*CD\\s+к\\s+книге\\s*$", "", text).strip()\n    marker = re.search(r"(?i)\\s*\\(\\s*(?:аудиокнига|audio\\s*book)\\s*\\)", text)\n    if marker and marker.start() >= 3:\n        text = text[:marker.start()]\n    else:\n        marker = _CATALOG_TITLE_TECH_RE.search(text)\n        if marker and marker.start() >= 3:\n            text = text[:marker.start()]\n'''
new = '''def _clean_catalog_title_candidate(value: str, authors: list[str]) -> str:\n    text = normalize_topic_subject_title(value, authors) if value else ""\n    if not text:\n        return ""\n\n    # Legacy RuTracker topics often use "Аудиокнига" as a release-type\n    # prefix rather than as part of the literary title. Strip it only at the\n    # beginning of the normalized candidate, optionally after a stray dash.\n    text = re.sub(\n        r"(?i)^(?:[-–—]\\s*)?(?:аудиокнига|audio\\s*book)"\n        r"(?:\\s*[:：.]\\s*|\\s+(?=[«\\\"“]))",\n        "",\n        text,\n        count=1,\n    ).strip()\n    text = re.sub(r"(?i)\\s+[-–—]\\s*CD\\s+к\\s+книге\\s*$", "", text).strip()\n\n    # Parenthesized release type remains a safe boundary. In the middle of a\n    # genuine title, however, the word "аудиокнига" is not technical by\n    # itself. Treat it as a boundary only when an actual codec/rip marker\n    # immediately follows (e.g. "аудиокнига MP3").\n    marker = re.search(r"(?i)\\s*\\(\\s*(?:аудиокнига|audio\\s*book)\\s*\\)", text)\n    if not marker:\n        marker = re.search(\n            r"(?i)\\s+(?:аудиокнига|audio\\s*book)\\s+"\n            r"(?=(?:mp3pro|mp3|мр3|m4a|m4b|aac|ogg|opus|flac|wav|wma|rip\\b))",\n            text,\n        )\n    if marker and marker.start() >= 3:\n        text = text[:marker.start()]\n    else:\n        marker = _CATALOG_TITLE_TECH_RE.search(text)\n        if marker and marker.start() >= 3:\n            text = text[:marker.start()]\n'''
text = replace_once(text, old, new, "catalog title cleanup anchor")

old_select = '''        equivalent = bool(subject_key and body_key and subject_key == body_key)\n        related = not subject or _body_title_related_to_subject(body, subject)\n        if related and (not subject or equivalent or _subject_still_looks_dirty(subject)):\n            return body\n'''
new_select = '''        equivalent = bool(subject_key and body_key and subject_key == body_key)\n        related = not subject or _body_title_related_to_subject(body, subject)\n        people_suffix = False\n        if subject and body and subject.casefold().startswith(body.casefold()):\n            suffix = _clean(subject[len(body):])\n            match = re.fullmatch(r"\\((.+)\\)", suffix)\n            if match and authors:\n                inside = _clean(match.group(1))\n                people_suffix = (\n                    "/" in inside\n                    and any(\n                        _same_person(part, author) or _author_prefix_matches(part, [author])\n                        for part in (_clean(x) for x in inside.split("/"))\n                        for author in authors\n                    )\n                )\n        if related and (not subject or equivalent or people_suffix or _subject_still_looks_dirty(subject)):\n            return body\n'''
text = replace_once(text, old_select, new_select, "topic title selection rule")

PARSER.write_text(text, encoding="utf-8")
print("patched RuTracker audiobook title prefix cleanup")
