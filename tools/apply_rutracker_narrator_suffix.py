from pathlib import Path

P = Path("src/abred_catalog_pipeline/rutracker/parser.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


s = P.read_text(encoding="utf-8")

old = """    title = _select_topic_title(raw_topic_title, body_title, authors) or f\"RuTracker {topic_id}\"\n    narrators = _split_people(\n        _post_field(post, narrator_labels)\n        or _label_value(post_text, narrator_labels)\n    )\n    if not narrators:\n        narrators = _infer_narrators_from_subject(raw_topic_title)\n"""
new = """    title = _select_topic_title(raw_topic_title, body_title, authors) or f\"RuTracker {topic_id}\"\n    narrators = _split_people(\n        _post_field(post, narrator_labels)\n        or _label_value(post_text, narrator_labels)\n    )\n    if not narrators:\n        narrators = _infer_narrators_from_subject(raw_topic_title)\n\n    # Some legacy subjects duplicate a confirmed narrator in a final [Name]\n    # suffix. Remove only that exact people suffix after narrator metadata has\n    # been parsed; unrelated bracketed title/series qualifiers stay intact.\n    suffix = re.search(r\"\\s*\\[([^\\[\\]]+)\\]\\s*$\", title)\n    if suffix and narrators:\n        person = _clean(suffix.group(1))\n        if any(_same_person(person, narrator) for narrator in narrators):\n            title = _clean(title[:suffix.start()]).rstrip(\" ,;:/-–—\")\n"""
s = replace_once(s, old, new, "parse_topic narrator block")
P.write_text(s, encoding="utf-8")
print("patched confirmed narrator title suffix cleanup")
