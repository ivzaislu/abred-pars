from pathlib import Path

P = Path('src/abred_catalog_pipeline/rutracker/parser.py')


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one match, found {count}')
    return text.replace(old, new, 1)


s = P.read_text(encoding='utf-8')

anchor = '''    if len(text) >= 2 and (text[0], text[-1]) in {("\\\"", "\\\""), ("