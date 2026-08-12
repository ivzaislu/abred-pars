from pathlib import Path

PARSER = Path("src/abred_catalog_pipeline/rutracker/parser.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


text = PARSER.read_text(encoding="utf-8")
old = '''def _clean_catalog_title_candidate(value: str, authors: list[str]) -> str:\n    text = normalize_topic_subject_title(value, authors) if value else ""\n    if not text:\n        return ""\n    text = re.sub(r"(?i)\\s+[-–—]\\s*CD\\s+к\\s+книге\\s*$", "", text).strip()\n'''
new = '''def _clean_catalog_title_candidate(value: str, authors: list[str]) -> str:\n    text = normalize_topic_subject_title(value, authors) if value else ""\n    if not text:\n        return ""\n\n    # Legacy RuTracker topics often use "Аудиокнига" as a release-type\n    # prefix rather than as part of the literary title. Strip it only at the\n    # beginning of the normalized candidate, optionally after a stray dash.\n    # Do not remove the word when it appears later in a genuine title.\n    text = re.sub(\n        r"(?i)^(?:[-–—]\\s*)?(?:аудиокнига|audio\\s*book)"\n        r"(?:\\s*[:：.]\\s*|\\s+(?=[«\\\"“]))",\n        "",\n        text,\n        count=1,\n    ).strip()\n    text = re.sub(r"(?i)\\s+[-–—]\\s*CD\\s+к\\s+книге\\s*$", "", text).strip()\n'''
text = replace_once(text, old, new, "catalog title cleanup anchor")
PARSER.write_text(text, encoding="utf-8")
print("patched RuTracker audiobook title prefix cleanup")
