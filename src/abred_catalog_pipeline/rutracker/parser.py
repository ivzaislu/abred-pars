from __future__ import annotations

import base64
import hashlib
import re
from difflib import SequenceMatcher
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ..models import (
    ParsedBook,
    ParsedChapter,
    ParsedSeriesEntry,
    ParsedTorrent,
    ParsedTorrentFile,
)


_AUDIO_EXTS = {".mp3", ".m4a", ".m4b", ".aac", ".ogg", ".opus", ".flac", ".wav", ".wma"}
_TOPIC_ID_RE = re.compile(r"(?:[?&]t=|/t)(\d+)")
_NUMBER_RE = re.compile(r"\d+")

DEFAULT_AUDIOBOOK_FORUM_IDS = (
    574, 1036, 400, 2388, 2387, 661, 2348, 2127,
    2137, 499, 490, 467, 402, 399, 695, 2152,
    530, 2342, 2325, 2165, 716, 403, 1350,
)


@dataclass(slots=True)
class TrackerRow:
    topic_id: str
    topic_url: str
    torrent_url: str
    title: str
    forum_id: str = ""
    forum_name: str = ""
    size_bytes: int = 0
    seeders: int = 0
    leechers: int = 0


@dataclass(slots=True)
class TopicSeries:
    external_id: str
    name: str
    entries: list[ParsedSeriesEntry]


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _node_text(node) -> str:
    if node is None:
        return ""
    markup = re.sub(r"(?is)<wbr\b[^>]*>", "", str(node))
    clone = BeautifulSoup(markup, "html.parser")
    return _clean(clone.get_text(" ", strip=False))


def _decode_html(content: bytes, response_encoding: str | None = None) -> str:
    for enc in (response_encoding, "cp1251", "utf-8"):
        if not enc:
            continue
        try:
            return content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            pass
    return content.decode("utf-8", errors="replace")


def _topic_id(url: str) -> str:
    parsed = urlparse(url)
    values = parse_qs(parsed.query).get("t")
    if values and values[0].isdigit():
        return values[0]
    m = _TOPIC_ID_RE.search(url)
    return m.group(1) if m else ""


def _info_hash_from_magnet(magnet_uri: str) -> str:
    if not magnet_uri:
        return ""
    try:
        xt_values = parse_qs(urlparse(magnet_uri).query).get("xt") or []
    except Exception:
        xt_values = []
    for xt in xt_values:
        value = (xt or "").strip()
        if not value.casefold().startswith("urn:btih:"):
            continue
        raw = value.split(":", 2)[-1].strip()
        if re.fullmatch(r"[0-9a-fA-F]{40}", raw):
            return raw.lower()
        if re.fullmatch(r"[A-Z2-7a-z2-7]{32}", raw):
            try:
                decoded = base64.b32decode(raw.upper())
            except Exception:
                continue
            if len(decoded) == 20:
                return decoded.hex()
    return ""


def _forum_id(url: str) -> str:
    values = parse_qs(urlparse(url).query).get("f")
    return values[0] if values and values[0].isdigit() else ""


def _int(text: str) -> int:
    m = _NUMBER_RE.search((text or "").replace(" ", ""))
    return int(m.group()) if m else 0


def _size_bytes(raw: str) -> int:
    value = _clean(raw).replace(",", ".")
    if not value:
        return 0
    if value.isdigit():
        return int(value)
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(B|KB|KiB|MB|MiB|GB|GiB|TB|TiB|байт|КБ|МБ|ГБ|ТБ)?", value, re.I)
    if not m:
        return 0
    number = float(m.group(1))
    unit = (m.group(2) or "B").casefold()
    powers = {
        "b": 0, "байт": 0,
        "kb": 1, "kib": 1, "кб": 1,
        "mb": 2, "mib": 2, "мб": 2,
        "gb": 3, "gib": 3, "гб": 3,
        "tb": 4, "tib": 4, "тб": 4,
    }
    return int(number * (1024 ** powers.get(unit, 0)))


def _duration_seconds(raw: str) -> int:
    value = _clean(raw)
    clock_matches = list(
        re.finditer(r"(?<!\d)(\d{1,3}):(\d{2}):(\d{2})(?!\d)", value)
    )
    if clock_matches:
        durations = [
            int(match.group(1)) * 3600
            + int(match.group(2)) * 60
            + int(match.group(3))
            for match in clock_matches
        ]
        if len(clock_matches) > 1 and all(
            re.fullmatch(r"\s*\+\s*", value[left.end():right.start()])
            for left, right in zip(clock_matches, clock_matches[1:])
        ):
            return sum(durations)
        return durations[0]
    minute_matches = list(
        re.finditer(r"(?<!\d)(\d{1,3}):(\d{2})(?!\d)", value)
    )
    if minute_matches:
        durations = [
            int(match.group(1)) * 60 + int(match.group(2))
            for match in minute_matches
        ]
        if len(minute_matches) > 1 and all(
            re.fullmatch(r"\s*\+\s*", value[left.end():right.start()])
            for left, right in zip(minute_matches, minute_matches[1:])
        ):
            return sum(durations)
        return durations[0]
    m = re.search(
        r"(?<!\d)(\d{1,3})\s*"
        r"(?:ч(?:ас(?:а|ов)?)?\.?)"
        r"(?:\s*(\d{1,2})\s*(?:м(?:ин(?:ут(?:а|ы)?)?)?\.?))?"
        r"(?:\s*(\d{1,2})\s*(?:с(?:ек(?:унд(?:а|ы)?)?)?\.?))?",
        value,
        re.I,
    )
    if m:
        h = int(m.group(1))
        minute = int(m.group(2) or 0)
        sec = int(m.group(3) or 0)
        return h * 3600 + minute * 60 + sec
    return 0


def _duration_from_extra_info(raw: str) -> int:
    """Read only an explicitly labelled total from a free-form info block."""
    match = re.search(
        r"(?i)\b(?:общее(?:\s+время)?\s+звучани[ея]|продолжительность)\b"
        r"\s*[:\-]?\s*.{0,80}",
        _clean(raw),
    )
    return _duration_seconds(match.group(0)) if match else 0


def parse_tracker_html(html: str, base_url: str) -> list[TrackerRow]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[TrackerRow] = []
    for row in soup.select("table#tor-tbl > tbody > tr"):
        details = row.select_one("td.t-title-col div.t-title a.tLink")
        download = row.select_one("td.tor-size a.tr-dl")
        if details is None or download is None:
            continue
        topic_url = urljoin(base_url.rstrip("/") + "/forum/", details.get("href", ""))
        topic_id = _topic_id(topic_url)
        if not topic_id:
            continue
        torrent_url = urljoin(base_url.rstrip("/") + "/forum/", download.get("href", ""))
        forum_link = row.select_one("td.f-name-col div.f-name a")
        forum_id = ""
        forum_name = ""
        if forum_link is not None:
            forum_name = _clean(forum_link.get_text(" ", strip=True))
            forum_id = _forum_id(forum_link.get("href", ""))
        size_cell = row.select_one("td.tor-size")
        size_raw = ""
        if size_cell is not None:
            size_raw = size_cell.get("data-ts_text") or size_cell.get_text(" ", strip=True)
        seed_cell = row.select_one("td:nth-child(7)")
        leech_cell = row.select_one("td:nth-child(8)")
        seeders = _int(seed_cell.get_text(" ", strip=True)) if seed_cell and "дн" not in seed_cell.get_text(" ").casefold() else 0
        leechers = _int(leech_cell.get_text(" ", strip=True)) if leech_cell else 0
        rows.append(TrackerRow(
            topic_id=topic_id, topic_url=topic_url, torrent_url=torrent_url,
            title=_node_text(details), forum_id=forum_id, forum_name=forum_name,
            size_bytes=_size_bytes(size_raw), seeders=seeders, leechers=leechers,
        ))
    return rows


def parse_forum_html(html: str, base_url: str, forum_id: str | int = "") -> list[TrackerRow]:
    soup = BeautifulSoup(html, "html.parser")
    inferred_forum = str(forum_id or "")
    if not inferred_forum:
        canonical = soup.select_one('link[rel="canonical"][href*="viewforum.php?f="]')
        if canonical:
            inferred_forum = _forum_id(canonical.get("href", ""))
    forum_name_node = soup.select_one("h1.maintitle a, h1.maintitle")
    forum_name = _node_text(forum_name_node) if forum_name_node else ""

    out: list[TrackerRow] = []
    seen: set[str] = set()
    for row in soup.select("table.vf-table tr[id^='tr-'], table.forum tr[id^='tr-']"):
        details = row.select_one('a.torTopic[href*="viewtopic.php?t="], a.tt-text[href*="viewtopic.php?t="]')
        download = row.select_one('a[href*="dl.php?t="]')
        if details is None or download is None:
            continue
        topic_url = urljoin(base_url.rstrip("/") + "/forum/", details.get("href", ""))
        topic_id = _topic_id(topic_url)
        if not topic_id or topic_id in seen:
            continue
        seen.add(topic_id)
        torrent_url = urljoin(base_url.rstrip("/") + "/forum/", download.get("href", ""))
        seeds = row.select_one(".seedmed")
        leeches = row.select_one(".leechmed")
        out.append(TrackerRow(
            topic_id=topic_id, topic_url=topic_url, torrent_url=torrent_url,
            title=_node_text(details), forum_id=inferred_forum, forum_name=forum_name,
            size_bytes=_size_bytes(download.get_text(" ", strip=True)),
            seeders=_int(seeds.get_text(" ", strip=True)) if seeds else 0,
            leechers=_int(leeches.get_text(" ", strip=True)) if leeches else 0,
        ))
    return out


def _label_value(text: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        m = re.search(rf"(?im)^\s*{re.escape(label)}\s*(?:\n\s*)?[:：]\s*(.+?)\s*$", text)
        if m:
            return _clean(m.group(1))
    return ""


def _normalized_post_label(value: str) -> str:
    return _clean(value).rstrip(":：").strip().casefold()


_STANDALONE_PEOPLE_NOISE = {
    "преподобный", "архимандрит", "диакон",
    "митрополит", "епископ", "архиепископ", "протоиерей",
    "иеромонах", "священник", "отец",
    "др", "др.", "другие",
    # Service prose seen in RuTracker people metadata, not a person.  The
    # release marker ``(ЛИ)`` is narrator-specific and must not be handled
    # here: ``Ли`` is also a legitimate surname (Bruce Lee, Harper Lee).
    "подробности далее",
    # A profession-only fragment from a comma-separated author field.  The
    # actual person's name is supplied by the following fragment.
    "ученый каббалист", "учёный каббалист",
}


def _normalize_person_item(value: str) -> str:
    item = _clean(value)
    if not item:
        return ""

    marker = item.strip("()[]{}<>«»\"' .,:;").casefold()
    if not marker or re.fullmatch(r"[-–—_]+", marker):
        return ""
    if marker in _STANDALONE_PEOPLE_NOISE:
        return ""

    # `(ЛИ)` is a RuTracker service/release marker, not part of a narrator
    # display name. Remove it only as a trailing parenthesized suffix so
    # legitimate aliases in parentheses remain untouched.
    item = re.sub(r"(?i)\s*\(\s*ЛИ\s*\)\s*$", "", item).strip()
    if not item:
        return ""

    # Repair only unmatched edge brackets. Balanced aliases such as
    # ``Сергей Русскин (Сундар Дути)`` must stay untouched.
    edge_pairs = (("[", "]"), ("(", ")"), ("{", "}"))
    for opening, closing in edge_pairs:
        if item.endswith(closing) and item.count(closing) > item.count(opening):
            item = item[:-1].rstrip()
        if item.startswith(opening) and item.count(opening) > item.count(closing):
            item = item[1:].lstrip()
    item = re.sub(r"\s+([)\]}])", r"\1", item)
    item = re.sub(r"([(\[{])\s+", r"\1", item)

    # Remove only a sentence-ending dot after a full final token.
    # Initials such as "Райро А." remain unchanged.
    match = re.search(r"([A-Za-zА-Яа-яЁё-]+)\.$", item)
    if match and len(match.group(1)) > 1:
        item = item[:-1].rstrip()

    return item


def _split_people_top_level(value: str) -> list[str]:
    """Split comma-like separators without breaking parenthesized aliases."""
    out: list[str] = []
    current: list[str] = []
    stack: list[str] = []
    pairs = {"(": ")", "[": "]", "{": "}"}
    closings = set(pairs.values())

    for char in value:
        if char in pairs:
            stack.append(pairs[char])
        elif char in closings and stack and char == stack[-1]:
            stack.pop()
        if char in ",;/" and not stack:
            part = _clean("".join(current))
            if part:
                out.append(part)
            current = []
            continue
        current.append(char)

    part = _clean("".join(current))
    if part:
        out.append(part)
    return out


def _looks_like_conjoined_person(value: str) -> bool:
    candidate = _clean(value)
    candidate = re.sub(
        r"(?i)^(?:архимандрит|протоиерей|иеромонах|священник|отец)\s+",
        "",
        candidate,
    )
    return _looks_like_person_prefix(candidate)


def _split_people(value: str) -> list[str]:
    if not value:
        return []
    out: list[str] = []
    for top_level_item in _split_people_top_level(value):
        top_level_item = re.sub(r"(?i)\s+и\s+др\.?\s*$", "", top_level_item).strip()
        conjunction_parts = re.split(r"\s+и\s+", top_level_item, flags=re.I)
        raw_items = (
            conjunction_parts
            if len(conjunction_parts) > 1
            and all(_looks_like_conjoined_person(part) for part in conjunction_parts)
            else [top_level_item]
        )
        for raw_item in raw_items:
            item = _normalize_person_item(raw_item)
            if item and item not in out:
                out.append(item)
    return out


def _normalize_author_item(value: str) -> str:
    item = _normalize_person_item(value)
    if re.fullmatch(
        r"(?i)(?:до|с|по|после)\s+(?:\d{1,2}|[IVXLCDM]+)\s*"
        r"(?:век(?:а|ов)?|в\.)",
        item,
    ):
        return ""
    # Some old templates put the literal label into the value itself.
    # Restrict removal to a leading label followed by a capitalized name so
    # values such as "автор неизвестен" and "Анонимный автор" stay intact.
    item = re.sub(
        r"^(?i:автор\s+(?:стихов\s+и\s+текста\s*[-–—:]*\s*))"
        r"(?=[A-ZА-ЯЁ])",
        "",
        item,
    ).strip()
    item = re.sub(r"^[Аа]втор\s+(?=[A-ZА-ЯЁ])", "", item).strip()
    item = re.sub(
        r"^(?i:профессор|академик)\s+(?=[A-ZА-ЯЁ])",
        "",
        item,
    ).strip()
    # Keep contribution roles out of the person display name. The role itself
    # cannot currently be represented by the feed schema.
    item = re.sub(
        r"(?i)\s+[-–—]\s+(?:идея|стихи|текст|сценарий|составитель)\s*$",
        "",
        item,
    ).strip()
    item = re.sub(
        r"(?i)\s*\(\s*(?:перевод|пер\.)\b[^()]*\)\s*$",
        "",
        item,
    ).strip()
    return item


def _strip_person_honorific(value: str) -> str:
    return re.sub(
        r"(?i)^(?:(?:(?:заслуженн(?:ый|ая|ые)|народн(?:ый|ая|ые))\s+|"
        r"(?:засл|нар)\.\s*)артист(?:ка|ы)?(?:\s+(?:РФ|России|СССР))?)"
        r"\s*[,;:—–-]*\s*",
        "",
        _clean(value),
    ).strip()


def _normalize_narrator_item(value: str) -> str:
    marker = _clean(value).strip("()[]{}<>«»\"' .,:;").casefold()
    if marker == "ли":
        return ""
    item = _normalize_person_item(value)
    if not item:
        return ""
    if re.match(r"(?i)^(?:синхронный\s+)?перевод(?:чик|чики)?\b", item):
        return ""
    if re.match(r"(?i)^реж(?:исс[её]р)?\.?\s*[:：]", item):
        return ""

    item = re.sub(
        r"(?i)^(?:акт(?:ер|ёр|риса)?\.?|лектор|диктор)\s*[:：]\s*",
        "",
        item,
    )
    item = re.sub(r"(?i)^от\s+автора\s*[-–—:：]\s*", "", item)
    item = re.sub(r"^[-–—:：]+\s*", "", item)

    # Cast lists commonly use ``Role — Person``. Select the final segment only
    # when it independently looks like a person, so hyphenated names survive.
    role_parts = re.split(r"\s+[-–—]\s+", item)
    if len(role_parts) > 1:
        candidate = _strip_person_honorific(role_parts[-1])
        candidate_people = _split_people(candidate)
        if candidate_people and all(_looks_like_person_prefix(x) for x in candidate_people):
            item = candidate

    item = _strip_person_honorific(item)
    return _normalize_person_item(item)


def _topic_narrators(value: str, authors: list[str]) -> list[str]:
    raw = _clean(value)
    if not raw:
        return []
    marker = re.sub(r"(?i)\s*\(\s*ЛИ\s*\)\s*$", "", raw).strip().casefold()
    if marker in {"автор", "читает автор", "чтец автор", "исполняет автор"}:
        return list(authors)
    if marker in {
        "заслуженные и народные артисты россии",
        "разные исполнители",
        "участники конкурса",
    }:
        return [re.sub(r"(?i)\s*\(\s*ЛИ\s*\)\s*$", "", raw).strip()]
    if re.match(r"(?i)^радиопостановка\b", raw):
        return []

    # Narrator fields sometimes describe the action instead of containing only
    # a display name. Keep the person and discard the prose/music credit.
    raw = re.sub(
        r"(?i)^(?:(?:заслуженн(?:ый|ая)|народн(?:ый|ая))\s+"
        r"артист(?:ка)?(?:\s+(?:РФ|России|СССР))?)\s*[,;:—–-]\s*",
        "",
        raw,
    )
    raw = re.sub(r"(?i)^коран\s+читает\s+", "", raw)
    raw = re.sub(r"(?i)^читает\s+и\s+по[её]т\s+", "", raw)
    raw = re.sub(r"(?i)^читает\s+", "", raw)
    raw = re.split(r"(?i)\.\s*музыка\b", raw, maxsplit=1)[0]
    raw = re.split(r"(?i)\s+-\s+(?:читает|исполняет|передразнивает)\b", raw, maxsplit=1)[0]

    # A few children's releases use a complete comma-separated cast in the
    # compact form ``Character-Person``.  Transform it only when at least two
    # credits share that exact shape; this avoids splitting an ordinary
    # hyphenated personal name.
    compact_items = _split_people_top_level(raw)
    compact_people: list[str] = []
    for compact_item in compact_items:
        match = re.match(
            r"^(.+\s.+?)-([A-ZА-ЯЁ][A-Za-zА-Яа-яЁё.'’]+"
            r"(?:\s+[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё.'’]+){1,3})$",
            compact_item,
        )
        if not match or not _looks_like_person_prefix(match.group(2)):
            compact_people = []
            break
        compact_people.append(match.group(2))
    if len(compact_people) >= 2:
        raw = ", ".join(compact_people)

    out: list[str] = []
    for value in _split_people(raw):
        item = _normalize_narrator_item(value)
        for person in _split_people(item):
            if person and person not in out:
                out.append(person)
    return out


def _joint_author_performers(post_text: str) -> list[str]:
    """Extract named readers from an explicit joint creator/performer block."""
    section = re.search(
        r"(?is)(?:^|\n)\s*автор\s+и\s+исполнитель\s*"
        r"(?:\n\s*)?[:：]\s*(.+?)(?=\n\s*жанр\s*(?:\n\s*)?[:：])",
        post_text,
    )
    if not section:
        return []
    out: list[str] = []
    for match in re.finditer(r"(?im)\bчитает\s+([^\n]+)", section.group(1)):
        person = _normalize_author_item(match.group(1))
        if (
            person
            and not any(ch.isdigit() for ch in person)
            and not _RELEASE_META_RE.search(person)
            and person not in out
        ):
            out.append(person)
    return out


def _post_cast_narrators(post) -> list[str]:
    """Extract people from a standalone `Исполнители:` or `В ролях:` list."""
    if post is None:
        return []
    lines = [_clean(x) for x in post.get_text("\n", strip=True).splitlines()]
    starts = {
        "исполнители", "в ролях",
        "действующие лица и исполнители",
    }
    start = next((i for i, line in enumerate(lines) if _normalized_post_label(line) in starts), None)
    if start is None:
        return []
    direct_people_list = _normalized_post_label(lines[start]) == "в ролях"
    out: list[str] = []
    for line in lines[start + 1:]:
        folded = _normalized_post_label(line)
        if folded in _KNOWN_POST_LABELS:
            break
        if out and re.match(
            r"(?i)^(?:премьера\b|автор\s+радиоверсии\b|"
            r"режисс[её]р(?:-постановщик)?\b|композитор\b|"
            r"звукорежисс[её]р(?:ы)?\b|шеф-редактор\b|редактор\b|"
            r"продюсер\b|при\s+финансовой\s+поддержке\b|"
            r"набор\s+в\s+группу\b|помощь\s*\|\s*донаты\b)",
            line,
        ):
            break
        if re.match(r"(?i)^(?:в эпизодах|музыка\b|оркестр\b)", line):
            continue
        match = re.match(r"^.+?\s+[-–—]\s+(.+)$", line)
        people_value = match.group(1) if match else (line if direct_people_list else "")
        people = _split_people(people_value)
        if not match:
            people = [person for person in people if _looks_like_person_prefix(person)]
        for raw_person in people:
            normalized = _normalize_narrator_item(raw_person)
            for person in _split_people(normalized):
                if person and person not in out:
                    out.append(person)
    return out


def _post_field(post, labels: tuple[str, ...]) -> str:
    if post is None:
        return ""
    wanted = {_normalized_post_label(x) for x in labels}
    for bold in post.select("span.post-b"):
        label = _normalized_post_label(bold.get_text(" ", strip=True))
        if label not in wanted:
            continue
        values: list[str] = []
        node = bold.next_sibling
        while node is not None:
            name = getattr(node, "name", None)
            classes = set(getattr(node, "get", lambda *_: [])("class") or []) if name else set()
            if name in {"br", "hr"} or (name == "span" and "post-br" in classes):
                break
            if name == "span" and "post-b" in classes:
                nested_text = _node_text(node)
                # Some old topics bold both the label and its value.  A bold
                # sibling is a boundary only when it actually looks like a
                # known field label; otherwise it is the field value.
                if _normalized_post_label(nested_text) in _KNOWN_POST_LABELS:
                    break
            if hasattr(node, "get_text"):
                text = _node_text(node)
            else:
                text = str(node)
            text = _clean(text).lstrip(":：").strip()
            if text:
                values.append(text)
            node = node.next_sibling
        value = _clean(" ".join(values)).lstrip(":：").strip()
        if value:
            return value
    return ""


def _post_field_present(post, post_text: str, labels: tuple[str, ...]) -> bool:
    wanted = {_normalized_post_label(x) for x in labels}
    if post is not None:
        for bold in post.select("span.post-b"):
            label = _normalized_post_label(bold.get_text(" ", strip=True))
            if label in wanted:
                return True
    for label in labels:
        if re.search(rf"(?im)^\s*{re.escape(label)}\s*(?:\n\s*)?[:：]", post_text or ""):
            return True
    return False


def _description_from_post(post) -> str:
    if post is None:
        return ""
    for bold in post.select("span.post-b"):
        if _normalized_post_label(bold.get_text(" ", strip=True)) != "описание":
            continue
        parts: list[str] = []
        node = bold.next_sibling
        while node is not None:
            name = getattr(node, "name", None)
            if name == "hr":
                break
            if name == "div" and "sp-wrap" in (node.get("class") or []):
                break
            if hasattr(node, "get_text"):
                text = node.get_text(" ", strip=True)
            else:
                text = str(node)
            text = _clean(text).lstrip(":：").strip()
            if text:
                parts.append(text)
            node = node.next_sibling
        return _clean(" ".join(parts))[:10000]
    return ""


_RELEASE_META_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:19|20)\d{2}\b|"
    r"\b(?:\d{2,4}\s*)?(?:k|к)(?:bps|bs)\b|"
    r"\b\d{2,4}\s*(?:кбит|кб\/с|kb\/s)\b|"
    r"\b(?:mp3|m4a|m4b|aac|ogg|opus|flac|wav|wma)\b|"
    r"\b(?:web|digital\s+release|cd|lossless)\b|"
    r"битрейт|аудиокниг|исполнител|читает|чтец"
    r")"
)

_AUTHOR_PREFIX_NOISE = {
    "и", "др", "and", "et", "al",
    "академик", "профессор",
    "митрополит", "епископ", "архиепископ", "архимандрит", "протоиерей",
    "иеромонах", "монах", "священник", "отец",
}


def _person_key(value: str) -> tuple[str, ...]:
    return tuple(sorted(re.findall(r"[a-zа-яё0-9]+", _clean(value).casefold(), flags=re.I)))


def _person_tokens_ordered(value: str) -> list[str]:
    return re.findall(r"[a-zа-яё0-9]+", _clean(value).casefold(), flags=re.I)


def _same_person(left: str, person: str) -> bool:
    left_key = _person_key(left)
    person_key = _person_key(person)
    return bool(left_key and person_key and left_key == person_key)


def _token_close(left: str, right: str) -> bool:
    if left == right:
        return True
    if len(left) == 1 < len(right):
        return right.startswith(left)
    if len(right) == 1 < len(left):
        return left.startswith(right)
    if min(len(left), len(right)) < 4:
        return False
    return SequenceMatcher(None, left, right).ratio() >= 0.84


def _author_prefix_matches(left: str, authors: list[str]) -> bool:
    author_tokens = [
        token
        for author in authors
        for token in _person_key(author)
        if token not in _AUTHOR_PREFIX_NOISE
    ]
    if not author_tokens:
        return False

    variants = [left]
    without_parens = _clean(re.sub(r"\([^()]*\)", " ", left))
    before_paren = _clean(left.split("(", 1)[0])
    for candidate in (without_parens, before_paren):
        if candidate and candidate not in variants:
            variants.append(candidate)

    for variant in variants:
        ordered = [x for x in _person_tokens_ordered(variant) if x not in _AUTHOR_PREFIX_NOISE]
        token_variants: list[list[str]] = [ordered]
        for pos in range(max(0, len(ordered) - 1)):
            merged = ordered[pos] + ordered[pos + 1]
            if any(_token_close(merged, candidate) for candidate in author_tokens):
                token_variants.append(ordered[:pos] + [merged] + ordered[pos + 2:])

        for left_tokens in token_variants:
            deduped: list[str] = []
            for token in left_tokens:
                if token not in deduped:
                    deduped.append(token)
            meaningful = [x for x in deduped if len(x) > 1]
            if not meaningful:
                continue

            matched = 0
            used: set[int] = set()
            for token in meaningful:
                hit = None
                for idx, candidate in enumerate(author_tokens):
                    if idx in used:
                        continue
                    if _token_close(token, candidate):
                        hit = idx
                        break
                if hit is None:
                    break
                used.add(hit)
                matched += 1
            else:
                if matched >= min(2, len(meaningful)):
                    return True
    return False


def _strip_release_suffix(raw_topic_title: str) -> tuple[str, bool]:
    value = _clean(raw_topic_title)
    removed_release_group = False
    # Old subjects may append navigation notes and loose audio metadata after
    # the normal bracketed release group.
    value = re.sub(r"\s*\+?\s*<[^<>]{1,80}>\s*$", "", value)
    loose_meta = re.search(
        r"(?i)(?:\s*,\s*(?:mp3|m4[ab]|aac|ogg|opus|flac|wav|wma|"
        r"\d{2,4}\s*(?:kbps|кбит(?:/с)?|кб/с)))+\s*$",
        value,
    )
    if loose_meta:
        value = _clean(value[:loose_meta.start()])
        removed_release_group = True
    while value:
        match = re.search(r"\s*\[([^\[\]]*)\]\s*$", value)
        if not match:
            break
        content = _clean(match.group(1))
        people = _split_people(content)
        people_release_group = bool(
            re.search(r"[,;/]", content)
            and len(people) >= 2
            and all(_looks_like_person_prefix(person) for person in people)
        )
        if (
            not removed_release_group
            and not _RELEASE_META_RE.search(content)
            and not people_release_group
        ):
            break
        value = _clean(value[:match.start()])
        removed_release_group = True

    match = re.search(r"\s*\(([^()]*)\)\s*$", value)
    if match and _RELEASE_META_RE.search(_clean(match.group(1))):
        value = _clean(value[:match.start()])
        removed_release_group = True

    if not removed_release_group:
        for candidate in re.finditer(r"\s*\[([^\[\]]*)\]", value):
            if not _RELEASE_META_RE.search(_clean(candidate.group(1))):
                continue
            tail = _clean(value[candidate.end():])
            if re.fullmatch(
                r"(?i)(?:\([^()]{1,120}\)\s*)?(?:[-–—:]\s*)?"
                r"(?:указан(?:а|о|ы)?|уточнен(?:а|о|ы)?|исправлен(?:а|о|ы)?|"
                r"в официальном описании|официальн\w*|примечани\w*).*",
                tail,
            ):
                value = _clean(value[:candidate.start()])
                removed_release_group = True
                break

    parts = re.split(r"\s*/\s*", value)
    technical_tail = 0
    if len(parts) >= 3:
        for part in reversed(parts):
            piece = _clean(part)
            if piece and _RELEASE_META_RE.search(piece):
                technical_tail += 1
                continue
            break
        if technical_tail >= 2 and technical_tail < len(parts):
            value = _clean(" / ".join(parts[:-technical_tail]))
            removed_release_group = True

    if removed_release_group:
        value = re.sub(r"[\s,;:/\-–—]+$", "", value)
    return _clean(value), removed_release_group


_GENERIC_SUBJECT_PREFIX_RE = re.compile(
    r"(?i)^(?:сборники?|аудиокниги?|радиоспектакли?)\s*[-–—:]\s+"
)

_ANONYMOUS_AUTHOR_CONTEXT_RE = re.compile(
    r"(?i)\b(?:имя\s+автора\s+не\s+раскрывается|"
    r"автор\s+(?:неизвестен|не\s+указан))\b"
)


def _strip_anonymous_release_prefix(value: str) -> str:
    """Drop a brand/surname prefix before an explicit audiobook marker.

    This is used only when the post itself says that the real author is not
    disclosed.  It therefore cleans the catalog title without inventing the
    visible prefix as an author.
    """
    return _clean(re.sub(
        r"(?i)^.{2,80}?\s+[-–—]\s+(?=(?:аудиокнига|audio\s*book)\s+[«\"“])",
        "",
        value,
        count=1,
    ))


def normalize_topic_subject_title(raw_topic_title: str, authors: list[str]) -> str:
    value, _ = _strip_release_suffix(raw_topic_title)
    value = _GENERIC_SUBJECT_PREFIX_RE.sub("", value, count=1)
    sep_re = re.compile(r"(?:\s+[-–—]\s*|\s*[-–—]\s+|:\s+)")
    for match in sep_re.finditer(value):
        left = _clean(value[:match.start()])
        right = _clean(value[match.end():])
        if not left or not right:
            continue
        exact = any(_same_person(left, author) for author in authors)
        fuzzy = _author_prefix_matches(left, authors)
        if exact or fuzzy:
            value = right
            break

    if authors:
        handled_quote = False
        closed_quote = re.match(
            r'^(.{2,100}?)\s*[«"“](.+?)[»"”](\s*(?:(?:cd|диск)\s*\d+))?$',
            value,
            flags=re.I,
        )
        if closed_quote:
            left = _clean(closed_quote.group(1))
            right = _clean(
                f"{closed_quote.group(2)} {closed_quote.group(3) or ''}"
            )
            if right and (
                any(_same_person(left, author) for author in authors)
                or _author_prefix_matches(left, authors)
            ):
                value = right
                handled_quote = True
        if not handled_quote:
            quote = re.match(r'^(.{2,100}?)\s*[«"“](.+)$', value)
            if quote:
                left = _clean(quote.group(1))
                right = _clean(quote.group(2)).strip('«»"“” ')
                # This branch repairs genuinely unclosed legacy title quotes.
                # A closing quote followed by a subtitle is an internal quote,
                # not a delimiter between the author and the full title.
                if right and not re.search(r'[»"”]', quote.group(2)) and (
                    any(_same_person(left, author) for author in authors)
                    or _author_prefix_matches(left, authors)
                ):
                    value = right
                    handled_quote = True
        if not handled_quote:
            for dot in re.finditer(r"\.\s+", value):
                left = _clean(value[:dot.start() + 1])
                # Only the author separator is removed here. Stripping quote
                # characters from both ends also removed a legitimate final
                # quote from titles such as the old Shrimad Bhagavatam topics.
                right = _clean(value[dot.end():])
                if not right:
                    continue
                if (
                    any(_same_person(left, author) for author in authors)
                    or _author_prefix_matches(left, authors)
                ):
                    value = right
                    break

    segments = re.split(r"\s+[-–—]\s*|\s*[-–—]\s+", value)
    if len(segments) >= 3:
        kept: list[str] = []
        for idx, segment in enumerate(segments):
            piece = _clean(segment)
            if 0 < idx < len(segments) - 1 and piece:
                if any(_same_person(piece, author) for author in authors) or _author_prefix_matches(piece, authors):
                    continue
            if piece:
                kept.append(piece)
        if len(kept) >= 2 and len(kept) < len(segments):
            value = " - ".join(kept)
    return _clean(value)


_KNOWN_POST_LABELS = {
    "год выпуска", "год издания", "автор", "авторы", "aвтор", "aвторы",
    "автор (никнейм)", "фамилия имя автора", "фио автора",
    "фамилия автора", "фамилии автора", "имя автора", "имена автора",
    "фамилия и имя автора", "фамилии авторов",
    "исполнитель", "исполнители", "читает", "текст читает", "чтец",
    "автор и исполнитель", "жанр", "жанры", "прочитано по изданию",
    "тип", "тип издания", "категория", "формат", "язык", "страна",
    "аудиокодек", "аудио кодек", "битрейт", "битрейт аудио", "вид битрейта",
    "частота дискретизации", "количество каналов (моно-стерео)",
    "время звучания", "продолжительность", "продолжительность общая", "описание", "доп. информация",
    "другие версии", "другие раздачи",
    "цикл/серия", "цикл", "серия", "номер книги", "номер в серии", "№ книги",
}


def _title_tokens(value: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-zа-яё0-9]+", _clean(value).casefold(), flags=re.I)
        if len(token) > 1
    }


def _body_title_related_to_subject(body_title: str, subject_title: str) -> bool:
    body = _clean(body_title)
    subject = _clean(subject_title)
    if not body or not subject:
        return False
    body_cf = body.casefold()
    subject_cf = subject.casefold()
    if body_cf in subject_cf or subject_cf in body_cf:
        return True
    body_tokens = _title_tokens(body)
    subject_tokens = _title_tokens(subject)
    if not body_tokens or not subject_tokens:
        return False
    overlap = body_tokens & subject_tokens
    return bool(overlap) and (len(overlap) / len(body_tokens)) >= 0.67


def _alnum_compact(value: str) -> tuple[str, list[int]]:
    compact: list[str] = []
    positions: list[int] = []
    for idx, ch in enumerate(value):
        if ch.isalnum():
            compact.append(ch.casefold())
            positions.append(idx)
    return "".join(compact), positions


def _heal_subject_soft_breaks(subject_title: str, body_title: str) -> str:
    subject = _clean(subject_title)
    body = _clean(body_title)
    if not subject or not body:
        return subject
    body_key, _ = _alnum_compact(body)
    subject_key, subject_positions = _alnum_compact(subject)
    if len(body_key) < 4 or not subject_key:
        return subject
    offset = subject_key.find(body_key)
    if offset < 0:
        return subject
    first = subject_positions[offset]
    last = subject_positions[offset + len(body_key) - 1] + 1
    span = subject[first:last]
    if span.count(" ") <= body.count(" "):
        return subject
    return _clean(subject[:first] + body + subject[last:])


def _looks_like_person_prefix(value: str) -> bool:
    text = _clean(value)
    if not text or any(ch.isdigit() for ch in text):
        return False
    words = re.findall(r"[A-Za-zА-Яа-яЁё]+(?:-[A-Za-zА-Яа-яЁё]+)?\.?", text)
    if len(words) == 1:
        bare = words[0].strip(".")
        return bool(re.fullmatch(r"[A-Za-z][A-Za-z'-]{2,39}", bare) and bare[:1].isupper())
    if not 2 <= len(words) <= 4:
        return False
    noise = {"сборник", "сборники", "аудиокнига", "аудиокниги", "радиоспектакль", "радиоспектакли"}
    if any(word.casefold().strip(".") in noise for word in words):
        return False
    for word in words:
        bare = word.strip(".")
        if len(bare) == 1:
            continue
        if not bare[:1].isupper():
            return False
    return True


def _looks_like_strict_colon_person_prefix(value: str) -> bool:
    text = _clean(value)
    if not text or any(ch.isdigit() for ch in text):
        return False
    words = re.findall(r"[А-Яа-яЁё]+(?:-[А-Яа-яЁё]+)?", text)
    if len(words) not in {2, 3}:
        return False
    if _clean(" ".join(words)) != text:
        return False
    for word in words:
        if len(word) < 2 or not word[:1].isupper():
            return False
    if re.search(r"(?:ый|ий|ая|яя|ое|ее|ые|ие)$", words[0].casefold()):
        return False
    return True


def _looks_like_person_list(value: str) -> bool:
    """Recognize an explicit comma-separated list of complete people."""
    people = _split_people(value)
    return bool(
        2 <= len(people) <= 12
        and all(_looks_like_person_prefix(person) for person in people)
    )


def _infer_author_from_subject(raw_topic_title: str, body_title: str) -> str:
    raw_value = _clean(raw_topic_title)
    value, _ = _strip_release_suffix(raw_topic_title)
    body_clean, _ = _strip_release_suffix(body_title)
    body_key, _ = _alnum_compact(body_clean)
    value_key, _ = _alnum_compact(value)

    def subject_match(right: str) -> bool:
        right = _clean(right).strip('«»"“” ')
        if not right:
            return False
        body_tokens = _title_tokens(body_title)
        right_tokens = _title_tokens(right)
        overlap = body_tokens & right_tokens
        legacy_shared_title = bool(
            len(overlap) >= 4
            and len(overlap) / min(len(body_tokens), len(right_tokens)) >= 0.6
        )
        return (
            _body_title_related_to_subject(body_title, right)
            or _body_title_related_to_subject(right, body_title)
            or bool(body_key and value_key and body_key == value_key)
            or legacy_shared_title
        )

    sep_re = re.compile(r"(?:\s+[-–—]\s*|\s*[-–—]\s+)")
    for match in sep_re.finditer(value):
        left = _clean(value[:match.start()])
        right = _clean(value[match.end():])
        if (
            not left
            or not right
            or not (
                _looks_like_person_prefix(left)
                or _looks_like_person_list(left)
            )
        ):
            continue
        if subject_match(right):
            return left

    quote = re.match(r'^(.{2,100}?)\s*[«"“](.+)$', value)
    if quote:
        left = _clean(quote.group(1))
        right = _clean(quote.group(2))
        # This pattern is for ``Author "Title"``. A candidate that starts
        # with a quotation mark is the quoted title before its closing quote,
        # not an author (legacy topic 161972).
        if (
            not left.startswith(('«', '"', '“'))
            and _looks_like_person_prefix(left)
            and subject_match(right)
        ):
            return left

    # Old topics also use "Author. Title" and "Surname I.O. Title".
    # Include the separator dot in the candidate so a final initial keeps it.
    for match in re.finditer(r"\.\s+", value):
        left = _clean(value[:match.start() + 1])
        right = _clean(value[match.end():])
        if not left or not right or not _looks_like_person_prefix(left):
            continue
        if subject_match(right):
            return left

    colon = re.match(r"^(.{2,100}?):\s+(.+)$", value)
    if colon:
        left = _clean(colon.group(1))
        right = _clean(colon.group(2))
        body_clean, _ = _strip_release_suffix(body_title)
        right_key, _ = _alnum_compact(right)
        body_key, _ = _alnum_compact(body_clean)
        legacy_year_suffix = bool(re.search(r"\((?:19|20)\d{2}\)\s*$", raw_value))
        if (
            left and right and _looks_like_person_prefix(left) and right_key
            and (body_key == right_key or legacy_year_suffix or _looks_like_strict_colon_person_prefix(left))
        ):
            return left

    # Very old topics may have no separate body-title or author field.  Once a
    # technical release suffix was definitely removed, accept a strict person
    # prefix before a spaced dash (e.g. `Кристи Агата - Название [cast, kbps]`).
    if value != raw_value:
        legacy = re.match(r"^(.{2,100}?)\s+[-–—]\s+(.+)$", value)
        if legacy:
            left, right = _clean(legacy.group(1)), _clean(legacy.group(2))
            if _looks_like_person_prefix(left) and len(_title_tokens(right)) >= 2:
                return left
    return ""


def _looks_like_subject_narrator(value: str, *, allow_single_alias: bool = False) -> bool:
    text = _clean(value)
    if not text or any(ch.isdigit() for ch in text) or _RELEASE_META_RE.search(text):
        return False
    if _looks_like_person_prefix(text):
        return True
    # One-word pseudonyms are ambiguous. Accept them only when the release
    # explicitly carried the RuTracker (ЛИ) narrator marker.
    return bool(
        allow_single_alias
        and re.fullmatch(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё'’\-]{2,39}", text)
    )


def _infer_narrators_from_subject(raw_topic_title: str) -> list[str]:
    value = _clean(raw_topic_title)
    if re.search(r"(?i)\[\s*читают\s+авторы(?:\s*[,;]|\s*\])", value):
        return ["Авторы"]
    # The people/release group is not always the final bracket: an edition or
    # publisher group may follow it (e.g. `[cast, 1964, MP3] [Мелодия, WEB]`).
    for match in reversed(list(re.finditer(r"\[([^\[\]]*)\]", value))):
        release = _clean(match.group(1))
        technical = _RELEASE_META_RE.search(release)
        if not technical:
            continue
        people_segment = _clean(release[:technical.start()]).rstrip(" ,;/")
        if not people_segment:
            continue
        had_li_marker = bool(re.search(r"(?i)\(\s*ЛИ\s*\)", people_segment))
        out: list[str] = []
        for candidate in _split_people(people_segment):
            if not _looks_like_subject_narrator(candidate, allow_single_alias=had_li_marker):
                continue
            if candidate not in out:
                out.append(candidate)
        if out:
            return out
    return []


_CATALOG_TITLE_TECH_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:mp3pro|mp3|мр3|m4a|m4b|aac|ogg|opus|flac|wav|wma)\b|"
    r"\brip\s+\d{2,4}\s*(?:kbps|kbit/s|kb/s|кбит/с|кб/с)?\b|"
    r"\b\d{2,4}\s*(?:kbps|kbit/s|kb/s|кбит/с|кб/с)\b"
    r")"
)


def _is_author_only_title(value: str, authors: list[str]) -> bool:
    text = _clean(value).strip(" :：;,.«»\"“”")
    return bool(text and any(_same_person(text, author) for author in authors))


def _clean_catalog_title_candidate(value: str, authors: list[str]) -> str:
    text = normalize_topic_subject_title(value, authors) if value else ""
    if not text:
        return ""

    # Decorative separator rows can be flattened into the visual heading by
    # BeautifulSoup.  They carry no title information.
    text = re.sub(r"\s*(?:=\s*){3,}", ". ", text)

    # A broken upstream page once leaked the HTTP status text into the topic
    # title between the author and the actual work name.
    text = re.sub(r"(?i)^bad[ _-]*gateway\s*[-–—:]\s*", "", text, count=1)

    # Legacy RuTracker topics often use "Аудиокнига" as a release-type
    # prefix rather than as part of the literary title. Strip it only at the
    # beginning of the normalized candidate, optionally after a stray dash.
    text = re.sub(
        r"(?i)^(?:[-–—]\s*)?(?:аудиокнига|audio\s*book)"
        r"(?:\s*[:：.]\s*|\s+(?=[«\"“]))",
        "",
        text,
        count=1,
    ).strip()
    text = re.sub(
        r"(?i)^аудиосборник\s+(?=[«\"“])",
        "",
        text,
        count=1,
    ).strip()
    text = re.sub(
        r"(?i)\.\s*аудиоспектакль\s*$",
        "",
        text,
    ).strip()
    text = re.sub(
        r"(?i)^ислам\s*[-–—:]\s*(?=сборник\s+лекций\b)",
        "",
        text,
        count=1,
    ).strip()
    text = re.sub(r"(?i)\s+[-–—]\s*CD\s+к\s+книге\s*$", "", text).strip()

    # Parenthesized release type remains a safe boundary. In the middle of a
    # genuine title, however, the word "аудиокнига" is not technical by
    # itself. Treat it as a boundary only when an actual codec/rip marker
    # immediately follows (e.g. "аудиокнига MP3").
    marker = re.search(
        r"(?i)\s*\(\s*(?:аудиокнига|аудиоспектакль|"
        r"аудиожурнал|audio\s*book)\s*\)",
        text,
    )
    if not marker:
        marker = re.search(
            r"(?i)\s+(?:аудиокнига|audio\s*book)\s+"
            r"(?=(?:mp3pro|mp3|мр3|m4a|m4b|aac|ogg|opus|flac|wav|wma|rip\b))",
            text,
        )
    if marker and marker.start() >= 3:
        text = text[:marker.start()]
    else:
        marker = _CATALOG_TITLE_TECH_RE.search(text)
        if marker and marker.start() >= 3:
            text = text[:marker.start()]
            # Some malformed legacy subjects omit the opening release
            # bracket: ``..., 2016 г., 128 kbps, MP3]``.  Once the codec or
            # bitrate proves the boundary, the immediately preceding year is
            # release metadata too.
            text = re.sub(
                r"(?i)(?:,\s*|\s+)(?:19|20)\d{2}\s*г?\.?\s*,?\s*$",
                "",
                text,
            )
    # Cutting at a codec inside `[MP3]` can leave the opening bracket behind.
    text = re.sub(r"\s*\[\s*$", "", text)
    text = _clean(text).rstrip(" ,;:/-–—")
    # Some Quran topics append a Latin transliteration of the reciter after a
    # slash while the actual narrator is supplied in its own metadata field.
    text = re.sub(
        r"(?i)^(.+\b(?:коран|qur(?:['’])?an))\s*/\s*"
        r"[A-Z][A-Za-z ._'’\-]{2,80}$",
        r"\1",
        text,
    )
    text = re.sub(
        r"(?i)\s*\+\s*(?:DOC|DOCX|FB2|EPUB|MOBI|PDF)"
        r"(?:\s*,\s*(?:DOC|DOCX|FB2|EPUB|MOBI|PDF))*\s*$",
        "",
        text,
    )
    text = re.sub(r"^[-–—]+\s*", "", text)
    quoted = re.match(r'^[«"“](.+?)[»"”]\s+(.+)$', text)
    if quoted and authors:
        quoted_title = _clean(quoted.group(1))
        possible_author = _clean(quoted.group(2))
        if any(_same_person(possible_author, author) for author in authors) or _author_prefix_matches(possible_author, authors):
            text = quoted_title
    text = re.sub(r"\s+([)\]»”])", r"\1", text)
    text = re.sub(r"([(\[«“])\s+", r"\1", text)
    text = re.sub(r"(?<=[А-Яа-яЁё])\.(?=[А-ЯЁ][а-яё])", ". ", text)
    text = _clean(text).strip()
    if len(text) >= 2 and (text[0], text[-1]) in {("\"", "\""), ("«", "»"), ("“", "”")}:
        opening, closing = text[0], text[-1]
        interior = text[1:-1]
        # Keep the edge quotes of a quoted list. Removing them from
        # ``"Title one", "Title two"`` leaves an unbalanced result.
        if opening not in interior and closing not in interior:
            text = _clean(interior)
    if text.count("«") == text.count("»") + 1:
        text += "»"
    return text


def _subject_still_looks_dirty(value: str) -> bool:
    text = _clean(value)
    if not text:
        return False
    if _CATALOG_TITLE_TECH_RE.search(text):
        return True
    if "[" in text or "]" in text:
        return True
    return bool(re.match(r"(?i)^\([^)]*(?:audio|musical|performance)[^)]*\)\s*VA\s*[-–—]", text))


def _select_topic_title(raw_topic_title: str, body_title: str, authors: list[str]) -> str:
    subject = _clean_catalog_title_candidate(raw_topic_title, authors)
    body = _clean_catalog_title_candidate(body_title, authors) if body_title else ""
    if body and not _is_author_only_title(body, authors):
        subject_key, _ = _alnum_compact(subject)
        body_key, _ = _alnum_compact(body)
        equivalent = bool(subject_key and body_key and subject_key == body_key)
        related = not subject or _body_title_related_to_subject(body, subject)
        people_suffix = False
        if subject and body and subject.casefold().startswith(body.casefold()):
            suffix = _clean(subject[len(body):])
            match = re.fullmatch(r"\((.+)\)", suffix)
            if match and authors:
                inside = _clean(match.group(1))
                people_suffix = (
                    "/" in inside
                    and any(
                        _same_person(part, author) or _author_prefix_matches(part, [author])
                        for part in (_clean(x) for x in inside.split("/"))
                        for author in authors
                    )
                )
        if related and (not subject or equivalent or people_suffix or _subject_still_looks_dirty(subject)):
            return body
    if subject:
        return _heal_subject_soft_breaks(subject, body)
    return body


def reconcile_repair_title(stored_title: str, parsed_title: str, authors: list[str]) -> str:
    stored = _clean_catalog_title_candidate(stored_title, authors)
    parsed = _clean_catalog_title_candidate(parsed_title, authors)
    if not stored:
        return parsed
    if not parsed:
        return stored
    stored_cf = stored.casefold()
    parsed_cf = parsed.casefold()
    if stored_cf == parsed_cf:
        return parsed
    stored_key, _ = _alnum_compact(stored)
    parsed_key, _ = _alnum_compact(parsed)
    if stored_key and stored_key == parsed_key:
        return parsed
    if stored_cf in parsed_cf:
        return parsed
    if parsed_cf in stored_cf:
        return stored
    if _body_title_related_to_subject(parsed, stored) and len(parsed_key) >= len(stored_key):
        return parsed
    return stored


def _topic_display_title(post, raw_topic_title: str, authors: list[str] | None = None) -> str:
    if post is not None:
        for node in post.select("span.post-align")[:3]:
            text = _node_text(node)
            if not text:
                continue
            if re.fullmatch(
                r"(?i)помощь\s*\|\s*донаты\s*\|\s*donations",
                text,
            ):
                continue
            if re.fullmatch(
                r"(?i)набор\s+в\s+группу\s+«хранители»\s*[-–—]\s*"
                r"помогите\s+сохранить\s+редкие\s+раздачи",
                text,
            ):
                continue
            if re.fullmatch(r"(?i)(?:книга|том|часть)\s*\d+", text):
                continue
            if len(text) <= 300:
                return text
        for raw in post.get_text("\n", strip=True).splitlines()[:12]:
            text = _clean(raw).strip(":：")
            folded = text.casefold()
            if not text or text in {"[Код]", "Код"}:
                continue
            if folded in _KNOWN_POST_LABELS or any(folded.startswith(x + ":") for x in _KNOWN_POST_LABELS):
                break
            if len(text) <= 300 and not re.fullmatch(r"[:：]+", text):
                return text
    return normalize_topic_subject_title(raw_topic_title, list(authors or []))


def _topic_authors(post, post_text: str) -> list[str]:
    direct_labels = (
        "Автор", "Авторы", "Aвтор", "Aвторы",
        "Автор (никнейм)", "Фамилия Имя автора", "ФИО автора",
        "Фамилия и имя автора", "Фамилии авторов",
    )
    direct = _post_field(post, direct_labels) or _label_value(post_text, direct_labels)
    if direct:
        return [x for x in (_normalize_author_item(v) for v in _split_people(direct)) if x]
    surname_labels = ("Фамилия автора", "Фамилии автора")
    given_labels = ("Имя автора", "Имена автора")
    surname = _post_field(post, surname_labels)
    given = _post_field(post, given_labels)
    # An explicitly present but empty HTML field must stay empty. Text-regex
    # fallback otherwise consumes the following label as its value.
    if not surname and not _post_field_present(post, post_text, surname_labels):
        surname = _label_value(post_text, surname_labels)
    if not given and not _post_field_present(post, post_text, given_labels):
        given = _label_value(post_text, given_labels)
    if surname or given:
        # Several legacy templates accidentally put a translator credit into
        # the given-name field. It must not be joined to the real surname field.
        if re.match(r"(?i)^перевод(?:чик|чики)?\b|^перевод\s+на\b", given):
            given = ""
        if not _normalize_author_item(surname):
            surname = ""
        if not _normalize_author_item(given):
            given = ""
        if not surname and not given:
            return []
        surnames = _split_people(surname)
        given_names = _split_people(given)
        if surnames and not given_names:
            return [
                person for person in (_normalize_author_item(v) for v in surnames)
                if person
            ]
        if given_names and not surnames:
            return [
                person for person in (_normalize_author_item(v) for v in given_names)
                if person
            ]
        # Some legacy forms store one shared family name and several given
        # names: ``Кови`` + ``Шон, Стивен Р.``.
        if len(surnames) == 1 and len(given_names) > 1:
            return [
                _clean(f"{surnames[0]} {given_name}")
                for given_name in given_names
            ]
        # Corrupt comma placement can split two one-word credits across the
        # surname/name fields (topic 4336065).
        if (
            len(surnames) == len(given_names) == 1
            and surname.rstrip().endswith(",")
            and given.lstrip().startswith(",")
        ):
            return [surnames[0], given_names[0]]
        if len(surnames) == len(given_names) == 1 and _same_person(surnames[0], given_names[0]):
            value = _normalize_author_item(surnames[0])
            return [value] if value else []
        if len(surnames) == len(given_names) and len(surnames) > 1:
            return [
                family_name if _same_person(family_name, given_name)
                else _clean(f"{given_name} {family_name}")
                for family_name, given_name in zip(surnames, given_names)
            ]
        value = _normalize_author_item(_clean(f"{surname} {given}"))
        return [value] if value else []
    screenplay = re.search(
        r"(?im)(?:^|:\s*)авторы?\s+сценария\s*[:：]\s*"
        r"([^\n.]{3,240})",
        post_text,
    )
    if screenplay:
        return [
            person
            for person in (
                _normalize_author_item(value)
                for value in _split_people(screenplay.group(1))
            )
            if person
        ]
    joint = _joint_author_performers(post_text)
    if joint:
        return joint
    text_credit = re.search(
        r"(?im)^\s*текст\s*[:：]\s*(?:\n\s*)?"
        r"([A-ZА-ЯЁ][A-Za-zА-Яа-яЁё'’-]{2,39})\.\s+"
        r"(?=[A-ZА-ЯЁ])",
        post_text,
    )
    if text_credit:
        return [_normalize_author_item(text_credit.group(1))]
    return []


def _title_narrator(value: str) -> tuple[str, list[str]]:
    """Extract an explicit trailing `. Чтец Name` marker from a title."""
    text = _clean(value)
    match = re.search(r"(?i)(?:^|[.!?;])\s*чтец\s+(.+?)\s*$", text)
    if not match:
        return text, []
    person = _normalize_person_item(match.group(1))
    if not person or any(ch.isdigit() for ch in person) or _RELEASE_META_RE.search(person):
        return text, []
    clean_title = _clean(text[:match.start()]).rstrip(" .!?,;:/-–—")
    return clean_title, [person]


def _sermon_speaker_from_title(value: str, context: str = "") -> str:
    """Return the explicitly named pastor from a narrow sermon-title pattern."""
    text, _ = _strip_release_suffix(value)
    match = re.fullmatch(r"(?i)аудиопроповеди\s+пастора\s+(.+)", text)
    if not match:
        return ""
    person = _normalize_person_item(match.group(1))
    if not _looks_like_person_prefix(person):
        return ""
    # The heading uses the genitive case; the biography on this legacy page
    # provides the canonical nominative display name.
    biography = re.search(
        r"(?i:краткая\s+биография)\s*:\s*"
        r"([А-ЯЁ][А-Яа-яЁё'-]+(?:\s+[А-ЯЁ][А-Яа-яЁё'-]+){1,3})",
        context,
    )
    if biography:
        candidate = _clean(biography.group(1))
        if _looks_like_person_prefix(candidate):
            return candidate
    return person


def _cover_from_post(post, base_url: str) -> str:
    if post is None:
        return ""
    var = post.select_one("var.postImg[title]")
    if var and (var.get("title") or "").startswith(("http://", "https://")):
        return var.get("title", "")
    for image in post.select("img[src]"):
        src = image.get("src", "")
        if not src or "static.rutracker" in src or "/smiles/" in src:
            continue
        return urljoin(base_url, src)
    return ""


_SERIES_HEADING_RE = re.compile(
    r'(?i)^(?:цикл\s*/\s*серия|серия|цикл|книги\s+цикла|книги\s+серии|'
    r'произведения\s+цикла|произведения\s+серии)'
    r'\s*(?:[:：\-]\s*)?(.+?)$'
)


def _series_heading_name(value: str) -> str:
    text = _clean(value).strip(" -:：")
    if not text:
        return ""
    m = _SERIES_HEADING_RE.match(text)
    if not m:
        return ""
    raw_name = _clean(m.group(1))
    quoted = re.search(r'[«"“](.+?)[»"”]', raw_name)
    name = _clean(quoted.group(1) if quoted else raw_name).strip('«»"“” \t')
    name = re.sub(
        r"(?i)\s*\((?:вышедшие|изданные|опубликованные)\b[^()]*\)\s*$",
        "",
        name,
    ).strip()
    if not name or name.casefold() in {"серия", "цикл"}:
        return ""
    return name


def _normalize_series_name(value: str) -> str:
    text = _clean(value).strip(" -:：")
    text = re.sub(
        r"(?i)^/?(?:цикл\s*/\s*серия|серия|цикл)\s*(?:[:：\-]\s*)?",
        "",
        text,
        count=1,
    )
    quoted = re.fullmatch(r'[«"“](.+?)[»"”]', text)
    return _clean(quoted.group(1) if quoted else text)


def _infer_topic_series_name(post) -> str:
    if post is None:
        return ""
    for node in post.select("div.sp-head, span.post-b"):
        if node.name == "span" and node.find_parent("a", class_="postLink") is not None:
            continue
        name = _series_heading_name(_node_text(node))
        if name:
            return name
    for node in post.select("a.postLink"):
        text = _node_text(node)
        if not re.match(r"(?i)^(?:цикл\s*/\s*серия|серия\s*[:：])", text):
            continue
        name = _series_heading_name(text)
        if name:
            return name
    return ""


def _series_title_key(value: str) -> str:
    text = _clean(value).casefold().replace("ё", "е")
    text = re.sub(r"(?<!\d)0+(\d+)", lambda m: str(int(m.group(1))), text)
    return "".join(re.findall(r"[a-zа-я0-9]+", text, flags=re.I))


def _series_hint_id(series_name: str, position: int, title: str) -> str:
    payload = f"{_series_title_key(series_name)}|{position}|{_series_title_key(title)}"
    return "series-hint:" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:24]


def _clean_series_entry_title(value: str, narrators: list[str]) -> str:
    title = _clean(value)
    # RuTracker link text is sometimes the literal URL, followed by narrator
    # notes or alternate editions.  The parsed entry already stores the first
    # topic link separately, so none of that belongs in its display title.
    title = re.split(r"(?i)\s+https?://", title, maxsplit=1)[0]
    title = re.split(
        r"(?i)\s+(?:описание|доп\.?\s*информация)\s*[:：]",
        title,
        maxsplit=1,
    )[0]
    title = re.split(r"(?i)\s*//\s*автор\s*[:：]", title, maxsplit=1)[0]
    title = re.split(r"(?i)\s*,?\s+в\s+исполнении\s+", title, maxsplit=1)[0]
    title = re.split(
        r"(?i)\s+книги\s+цикла\s+на\s+трекере\b",
        title,
        maxsplit=1,
    )[0]
    title = re.split(r"\s+\*(?=\S)", title, maxsplit=1)[0]
    title = re.sub(
        r"(?i)\s*[-–—]?\s*(?:данн(?:ый|ая)\s+(?:релиз|раздача|аудиокнига|книга)|"
        r"эта\s+раздача)\s*$",
        "",
        title,
    )
    # Some hand-written reading orders append publication/context notes to a
    # real title.  These phrases describe the entry but are not part of it.
    title = re.sub(
        r"(?i)\s*[;,]?\s*книга\s+вышла\b.*$",
        "",
        title,
    )
    title = re.sub(
        r"(?i)\s*[;,]?\s*т\s*\.?\s*е\s*\.?\s+как\s+раз\s+"
        r"перед\s+событиями\s+романа\b.*$",
        "",
        title,
    )

    # A linked series list may reproduce the complete RuTracker release title.
    # Remove a confirmed current narrator only when it is followed by release
    # metadata, never from an ordinary title occurrence.
    for narrator in sorted(narrators, key=len, reverse=True):
        if not narrator:
            continue
        title = re.sub(
            rf"(?i)\s*[.,;\[]*\s*{re.escape(narrator)}\s*,?\s*"
            rf"(?:\(\s*ЛИ\s*\)\s*,?\s*)?(?:19|20)\d{{2}}\s*\]?\s*$",
            "",
            title,
        )
    return _clean(title).rstrip(" ,;:/-–—")


def _looks_like_non_series_entry(value: str) -> bool:
    title = _clean(value)
    if not title or len(title) > 240:
        return True
    if re.search(
        r"(?i)(?:"
        r"\.(?:mp3|m4[ab]|aac|ogg|opus|flac|wav|wma)\b|"
        r"\b(?:bitrate|продолжительность)\b|"
        r"\b\d{2,4}\s*(?:kbps|кбит|кб/с|kb/s)\b|"
        r"[\[(]\d{1,3}:\d{2}(?::\d{2})?[\])]\s*$|"
        r"^(?:\d{1,2}\.)?\d{1,2}\.\d{4}\s+.*"
        r"(?:перезалит|добавлен|обновлен|обновлён|исправлен|заменен|заменён)"
        r")",
        title,
    ):
        return True
    return False


def _series_entries_look_like_contents(
    entries: list[ParsedSeriesEntry], *, series_name: str, current_title: str,
) -> bool:
    if not entries:
        return True
    unlinked = [entry for entry in entries if not entry.external_url]
    if len(unlinked) != len(entries):
        return False
    if len(entries) > 50:
        return True
    if len(entries) >= 10 and any(entry.position == 0 for entry in entries):
        return True

    # A heading equal to the current book, followed by unrelated numbered
    # paragraphs, is a contents/lecture list rather than a list of books.
    if _series_title_key(series_name) == _series_title_key(current_title):
        current_key = _series_title_key(current_title)
        related = any(
            _series_title_key(entry.title) in current_key
            or current_key in _series_title_key(entry.title)
            for entry in entries
        )
        if not related:
            return True

    # Poetry compilations often number tracks and append the poet in brackets.
    # A real series can contain an occasional parenthetical qualifier, so apply
    # this only to a substantial, overwhelmingly credited unlinked list.
    if len(entries) >= 6:
        credited = 0
        for entry in entries:
            match = re.search(r"\(([^()]*)\)\s*$", entry.title)
            if match and _looks_like_person_prefix(match.group(1)):
                credited += 1
        if credited / len(entries) >= 0.7:
            return True
    return False


def _infer_series_position(title: str, body_title: str, series_name: str) -> int | None:
    for value in (title, body_title):
        text = _clean(value)
        if not text:
            continue
        m = re.search(r"(?i)\b(?:книга|том|часть)\s*(?:№\s*)?0*(\d{1,3})\b", text)
        if m:
            return int(m.group(1))
    if series_name:
        for value in (title, body_title):
            text = _clean(value)
            if text.casefold().startswith(series_name.casefold()):
                tail = text[len(series_name):]
                m = re.match(r"^[\s.,:;\-–—]*0*(\d{1,3})(?:\D|$)", tail)
                if m:
                    return int(m.group(1))
    return None


def _series_fragments(container) -> list[str]:
    if container is None:
        return []
    return re.split(r"<br\s*/?>", container.decode_contents(), flags=re.I)


def _parse_ordered_series_list(
    container, *, topic_url: str, base_url: str, series_name: str,
    current_position: int | None, authors: list[str], narrators: list[str],
) -> list[ParsedSeriesEntry]:
    """Parse a source-authored ``<ol>`` immediately following a series heading."""
    current_topic = _topic_id(topic_url)
    start = _int(container.get("start", "1")) or 1
    entries: list[ParsedSeriesEntry] = []
    for offset, item in enumerate(container.find_all("li", recursive=False)):
        position = _int(item.get("value", "")) or start + offset
        title = _clean_series_entry_title(_node_text(item), narrators)
        if not title or _looks_like_non_series_entry(title):
            continue
        link = item.select_one('a[href*="viewtopic.php?t="]')
        external_url = ""
        external_id = ""
        if link is not None:
            external_url = urljoin(
                base_url.rstrip("/") + "/forum/", link.get("href", "")
            )
            external_id = _topic_id(external_url)
        if not external_id and current_position == position and current_topic:
            external_url = topic_url
            external_id = current_topic
        if not external_id:
            external_id = _series_hint_id(series_name, position, title)
        entries.append(ParsedSeriesEntry(
            external_id=external_id,
            external_url=external_url,
            title=title,
            position=position,
            authors=list(authors),
        ))
    return entries


def _parse_series_lines(
    fragments: list[str], *, topic_url: str, base_url: str, series_name: str,
    current_position: int | None, current_title: str, authors: list[str],
    narrators: list[str], require_heading: bool,
) -> list[ParsedSeriesEntry]:
    current_topic = _topic_id(topic_url)
    entries: list[ParsedSeriesEntry] = []
    active = not require_heading
    seen_numbered = False

    def is_list_heading(frag, text: str) -> bool:
        # ``Цикл/серия: Name`` is the ordinary metadata field.  It identifies
        # the current book's series but must not activate parsing of every
        # numbered block that follows (most often a chapter list).
        metadata_label = any(
            _normalized_post_label(_node_text(node))
            in {"цикл/серия", "цикл", "серия"}
            for node in frag.select("span.post-b")
        )
        if metadata_label and re.match(
            r"(?i)^(?:цикл\s*/\s*серия|цикл|серия)\s*[:：]",
            text,
        ):
            return False

        candidates = [text]
        candidates.extend(
            _node_text(node)
            for node in frag.select("a.postLink, span.post-b, div.sp-head")
        )
        series_key = _series_title_key(series_name)

        # Malformed legacy markup can leave an explicit heading attached to
        # the end of the preceding description after splitting on ``br``.
        if re.search(
            r"(?i)(?:содержание|книги|произведения)\s+"
            r"(?:цикла|серии)\s*[:：]?\s*$",
            text,
        ):
            return True

        named_cycle = re.search(
            r"(?i)(?:^|[:：]\s*)цикл\s*[«\"“](.+?)[»\"”]\s*[:：]?\s*$",
            text,
        )
        if (
            named_cycle
            and _series_title_key(named_cycle.group(1)) == series_key
        ):
            return True

        for candidate in candidates:
            candidate = _clean(candidate)
            heading = _series_heading_name(candidate)
            if heading and _series_title_key(heading) == series_key:
                return True

            # Some long-running fan series name a narrower saga in the list
            # heading and explicitly say that the following order is the
            # narration order.  Require both signals and a name contained in
            # the confirmed series metadata, so an arbitrary numbered block
            # cannot activate series parsing.
            if re.search(
                r"(?i)\bперечень\s+в\s+порядке\s+(?:озвучания|чтения)\b",
                candidate,
            ):
                quoted = re.search(r"[«\"“](.+?)[»\"”]", candidate)
                quoted_key = _series_title_key(quoted.group(1)) if quoted else ""
                if quoted_key and quoted_key in series_key:
                    return True

            bare = _clean(candidate).strip(" :：;,.«»\"“”")
            if (
                bare
                and frag.select_one('a[href*="viewtopic.php?t="]') is None
                and _series_title_key(bare) == series_key
            ):
                return True

            if re.fullmatch(
                r"(?i)(?:содержание|книги|произведения)\s+"
                r"(?:цикла|серии)\s*[:：]?",
                bare,
            ):
                return True

        return bool(re.match(
            r"(?i)^(?:весь\s+)?(?:под)?цикл\b.*(?:раздач|аудиокниг)",
            text,
        ))

    # A top-level numbered entry may itself contain an ``ol``/``ul`` for a
    # subcycle.  ``_series_fragments`` splits on ``br`` and therefore exposes
    # later nested items as if they belonged to the outer reading order.  Keep
    # the outer prefix and suppress fragments until the embedded list closes.
    visible_fragments: list[str] = []
    embedded_list_depth = 0
    for raw_fragment in fragments:
        opens = len(re.findall(r"(?i)<(?:ol|ul)\b", raw_fragment))
        closes = len(re.findall(r"(?i)</(?:ol|ul)\s*>", raw_fragment))
        if embedded_list_depth:
            embedded_list_depth = max(0, embedded_list_depth + opens - closes)
            continue

        opening = re.search(r"(?i)<(?:ol|ul)\b", raw_fragment)
        if opening is not None:
            prefix = raw_fragment[:opening.start()]
            prefix_text = _clean(
                BeautifulSoup(prefix, "html.parser").get_text(" ", strip=True)
            )
            is_numbered_parent = bool(re.match(
                r"^(?:[📘📗📙📕📚]\s*)?\d{1,3}[.)]\s*\S+",
                prefix_text,
            ))
            if not is_numbered_parent:
                # Older RuTracker markup sometimes wraps the *outer* series
                # lines in an ``ol`` without using ``li`` elements.  Preserve
                # that container: the ordinary numbered-line parser handles
                # its ``br``-separated entries.
                visible_fragments.append(raw_fragment)
                continue
            if prefix.strip():
                visible_fragments.append(prefix)
            embedded_list_depth = max(0, opens - closes)
            continue

        visible_fragments.append(raw_fragment)

    for fragment in visible_fragments:
        full_frag = BeautifulSoup(fragment, "html.parser")
        full_text = _clean(full_frag.get_text(" ", strip=True))
        if full_text and is_list_heading(full_frag, full_text):
            active = True
            continue

        fragment = re.split(r"(?is)<(?:div|table|hr)\b", fragment, maxsplit=1)[0]
        frag = BeautifulSoup(fragment, "html.parser")
        text = _clean(frag.get_text(" ", strip=True))
        if not text:
            continue

        if require_heading and not active:
            continue

        m = re.match(r"^(?:[📘📗📙📕📚]\s*)?(\d{1,3})[.)]\s*(.+)$", text)
        if not m:
            if active and seen_numbered:
                # Cycle spoilers sometimes interleave notes or an unnumbered
                # prequel between numbered books. A spoiler is bounded, so
                # skip that annotation and keep looking. For the whole post,
                # retain the hard boundary to avoid absorbing chapter lists.
                if not require_heading:
                    continue
                break
            continue

        seen_numbered = True
        position = int(m.group(1))
        title = _clean_series_entry_title(m.group(2), narrators)
        if not title or _looks_like_non_series_entry(title):
            continue

        link = frag.select_one('a[href*="viewtopic.php?t="]')
        external_url = ""
        external_id = ""
        if link is not None:
            external_url = urljoin(base_url.rstrip("/") + "/forum/", link.get("href", ""))
            external_id = _topic_id(external_url)

        is_current = (
            "данный релиз" in text.casefold()
            or (current_position is not None and position == current_position)
            or (current_title and _series_title_key(title) == _series_title_key(current_title))
        )
        if not external_id and is_current and current_topic:
            external_url = topic_url
            external_id = current_topic
        if not external_id:
            external_id = _series_hint_id(series_name, position, title)

        entries.append(ParsedSeriesEntry(
            external_id=external_id, external_url=external_url, title=title,
            position=position, authors=list(authors or []),
        ))

    return entries


def _parse_topic_series(
    post, topic_url: str, base_url: str, series_name: str,
    current_position: int | None, current_title: str = "",
    authors: list[str] | None = None, narrators: list[str] | None = None,
) -> TopicSeries | None:
    if post is None or not series_name:
        return None
    current_topic = _topic_id(topic_url)
    all_entries: list[ParsedSeriesEntry] = []

    for heading_node in post.select("span.post-b"):
        heading_name = _series_heading_name(_node_text(heading_node))
        if not heading_name or heading_name.casefold() != series_name.casefold():
            continue
        sibling = heading_node.next_sibling
        while sibling is not None and not getattr(sibling, "name", None):
            if _clean(str(sibling)):
                break
            sibling = sibling.next_sibling
        if getattr(sibling, "name", None) != "ol":
            continue
        all_entries.extend(_parse_ordered_series_list(
            sibling,
            topic_url=topic_url,
            base_url=base_url,
            series_name=series_name,
            current_position=current_position,
            authors=list(authors or []),
            narrators=list(narrators or []),
        ))

    for wrap in post.select("div.sp-wrap"):
        head = wrap.select_one("div.sp-head")
        body = wrap.select_one("div.sp-body")
        if head is None or body is None:
            continue
        head_text = _clean(head.get_text(" ", strip=True))
        heading_name = _series_heading_name(head_text)
        if (
            "цикл" not in head_text.casefold()
            and "серия" not in head_text.casefold()
            and series_name.casefold() not in head_text.casefold()
        ):
            continue
        if heading_name and heading_name.casefold() != series_name.casefold():
            continue
        all_entries.extend(_parse_series_lines(
            _series_fragments(body), topic_url=topic_url, base_url=base_url,
            series_name=series_name, current_position=current_position,
            current_title=current_title, authors=list(authors or []),
            narrators=list(narrators or []), require_heading=False,
        ))

    all_entries.extend(_parse_series_lines(
        _series_fragments(post), topic_url=topic_url, base_url=base_url,
        series_name=series_name, current_position=current_position,
        current_title=current_title, authors=list(authors or []),
        narrators=list(narrators or []), require_heading=True,
    ))

    if not all_entries:
        return None

    deduped: dict[tuple[int, str], ParsedSeriesEntry] = {}
    for entry in all_entries:
        key = (entry.position, _series_title_key(entry.title))
        existing = deduped.get(key)
        if existing is None or (
            existing.external_id.startswith("series-hint:")
            and not entry.external_id.startswith("series-hint:")
        ):
            deduped[key] = entry
    entries = sorted(deduped.values(), key=lambda x: (x.position, x.title.casefold()))
    if _series_entries_look_like_contents(
        entries, series_name=series_name, current_title=current_title,
    ):
        return None

    external_id = f"topic-series:{current_topic}" if current_topic else ""
    if not external_id:
        return None
    return TopicSeries(external_id=external_id, name=series_name, entries=entries)


def parse_topic_html(html: str, topic_url: str, base_url: str) -> ParsedBook:
    soup = BeautifulSoup(html, "html.parser")
    topic_id = _topic_id(topic_url)
    title_node = soup.select_one("a#topic-title, h1.maintitle a, h1.maintitle, .maintitle")
    raw_topic_title = _node_text(title_node) if title_node else ""
    post = soup.select_one("div.post_body, .post_body")
    post_text = post.get_text("\n", strip=True) if post else soup.get_text("\n", strip=True)

    metadata_fields_present: set[str] = set()
    if raw_topic_title:
        metadata_fields_present.add("title")

    author_labels = (
        "Автор", "Авторы", "Aвтор", "Aвторы",
        "Автор (никнейм)", "Фамилия Имя автора", "ФИО автора",
        "Фамилия автора", "Фамилии автора", "Имя автора", "Имена автора",
        "Фамилия и имя автора", "Фамилии авторов",
    )
    narrator_labels = ("Исполнитель", "Исполнители", "Читает", "Текст читает", "Чтец")
    genre_labels = ("Жанр", "Жанры")
    series_labels = ("Цикл/серия", "Цикл", "Серия")
    position_labels = ("Номер книги", "Номер в серии", "№ книги")

    if _post_field_present(post, post_text, author_labels):
        metadata_fields_present.add("authors")
    if _post_field_present(post, post_text, narrator_labels):
        metadata_fields_present.add("narrators")
    if _post_field_present(post, post_text, genre_labels):
        metadata_fields_present.add("genres")
    if _post_field_present(post, post_text, series_labels):
        metadata_fields_present.add("series")
    if _post_field_present(post, post_text, position_labels):
        metadata_fields_present.add("series_position")

    authors = _topic_authors(post, post_text)

    body_title = (
        _post_field(post, ("Название", "Наименование", "Название книги", "Название произведения"))
        or _label_value(post_text, ("Название", "Наименование", "Название книги", "Название произведения"))
        or _topic_display_title(post, raw_topic_title, authors)
    )
    if _ANONYMOUS_AUTHOR_CONTEXT_RE.search(post_text):
        raw_topic_title = _strip_anonymous_release_prefix(raw_topic_title)
        body_title = _strip_anonymous_release_prefix(body_title)
    sermon_speaker = (
        _sermon_speaker_from_title(raw_topic_title, post_text)
        or _sermon_speaker_from_title(body_title, post_text)
    )
    if not authors:
        inferred_author = _infer_author_from_subject(raw_topic_title, body_title)
        if inferred_author:
            # Important fix: inferred legacy subject can contain multiple people.
            authors = [
                x for x in (_normalize_author_item(v) for v in _split_people(inferred_author))
                if x
            ]
        elif sermon_speaker:
            authors = [sermon_speaker]

    title = _select_topic_title(raw_topic_title, body_title, authors) or f"RuTracker {topic_id}"
    narrators = _topic_narrators(
        _post_field(post, narrator_labels)
        or _label_value(post_text, narrator_labels),
        authors,
    )
    if not narrators:
        narrators = (
            _joint_author_performers(post_text)
            or _post_cast_narrators(post)
            or _infer_narrators_from_subject(raw_topic_title)
        )
    if not narrators and sermon_speaker:
        narrators = [sermon_speaker]
    title, explicit_title_narrators = _title_narrator(title)
    if explicit_title_narrators:
        for person in explicit_title_narrators:
            if person not in narrators:
                narrators.append(person)

    # Some legacy subjects duplicate a confirmed narrator in a final [Name]
    # suffix. Remove only that exact people suffix after narrator metadata has
    # been parsed; unrelated bracketed title/series qualifiers stay intact.
    suffix = re.search(r"\s*\[([^\[\]]+)\]\s*$", title)
    if suffix and narrators:
        person = _clean(suffix.group(1))
        if any(_same_person(person, narrator) for narrator in narrators):
            title = _clean(title[:suffix.start()]).rstrip(" ,;:/-–—")
    genre_value = _post_field(post, genre_labels) or _label_value(post_text, genre_labels)
    raw_genres = [_clean(x) for x in re.split(r"\s*[,;/]\s*", genre_value) if _clean(x)] if genre_value else []
    series_genre_hint = ""
    genres: list[str] = []
    for value in raw_genres:
        m = re.match(r"(?i)^серия\s*[:：]\s*(.+)$", value)
        if m:
            series_genre_hint = series_genre_hint or _clean(m.group(1))
            continue
        if value.casefold() in {"аудиокнига", "audiobook"}:
            continue
        genres.append(value)

    # Some legacy subjects append the genre in square brackets while the
    # post exposes the same value in a dedicated field. It is navigation
    # metadata, not part of the work title (topic 2332733).
    genre_suffix = re.search(r"\s*\[([^\[\]]+)\]\s*$", title)
    if genre_suffix:
        suffix_value = _clean(genre_suffix.group(1)).casefold()
        if any(suffix_value == genre.casefold() for genre in genres):
            title = _clean(title[:genre_suffix.start()]).rstrip(" ,;:/-–—")

    description = _description_from_post(post) or _label_value(post_text, ("Описание",))
    if not description and post:
        description = _clean(post.get_text(" ", strip=True))[:5000]

    cover_url = _cover_from_post(post, base_url)
    duration_labels = (
        "Время звучания",
        "Общее время звучания",
        "Общее звучание",
        "Продолжительность",
    )
    duration = _duration_seconds(
        _post_field(post, duration_labels)
        or _label_value(post_text, duration_labels)
    )
    if not duration:
        duration = _duration_from_extra_info(
            _post_field(post, ("Доп. информация", "Дополнительная информация"))
            or _label_value(post_text, ("Доп. информация", "Дополнительная информация"))
        )
    inferred_series_name = _infer_topic_series_name(post)
    series_name = _normalize_series_name(
        _post_field(post, series_labels)
        or _label_value(post_text, series_labels)
        or inferred_series_name
        or series_genre_hint
    )
    clean_body_title = _clean_catalog_title_candidate(body_title, authors)
    if (
        series_name
        and clean_body_title
        and title != clean_body_title
        and title.casefold().endswith(clean_body_title.casefold())
    ):
        prefix = _clean(title[:-len(clean_body_title)]).rstrip(" ,;:/-–—")
        # Repair only a proven concatenation: the complete prefix must be the
        # already-confirmed series field.  Numbered forms such as
        # ``Series 03, Book`` intentionally keep their existing display title.
        if _series_title_key(prefix) == _series_title_key(series_name):
            title = clean_body_title
    if series_name and re.search(r"(?i)\bаудиокнига\s*[:.]", title):
        candidate = re.split(r"(?i)\bаудиокнига\s*[:.]\s*", title, maxsplit=1)[-1]
        candidate = _clean(candidate).strip("«»\"'‘’ “”) ")
        if candidate:
            title = candidate
    if series_name and (inferred_series_name or series_genre_hint):
        metadata_fields_present.add("series")
    position_raw = _post_field(post, position_labels) or _label_value(post_text, position_labels)
    series_position = _int(position_raw) or _infer_series_position(title, body_title, series_name)
    topic_series = _parse_topic_series(
        post, topic_url, base_url, series_name, series_position,
        current_title=title, authors=authors, narrators=narrators,
    )
    if series_position is None and topic_series and topic_id:
        current_positions = {
            entry.position
            for entry in topic_series.entries
            if entry.external_id == topic_id
        }
        if len(current_positions) == 1:
            series_position = current_positions.pop()

    # Fallback extraction can populate people after the source-field presence
    # scan. Keep this diagnostic field aligned with the final record.
    if authors:
        metadata_fields_present.add("authors")
    if narrators:
        metadata_fields_present.add("narrators")

    magnet = ""
    magnet_link = soup.select_one(
        'table.attach a.magnet-link[href^="magnet:?"], '
        'a.magnet-link[href^="magnet:?"], a[href^="magnet:?"]'
    )
    if magnet_link:
        magnet = magnet_link.get("href", "")

    torrent_url = ""
    download_links = soup.select('a.dl-stub[href*="dl.php?t="], a[href*="dl.php?t="]')
    dl_link = next(
        (link for link in download_links if _topic_id(link.get("href", "")) == topic_id),
        None,
    )
    if dl_link:
        torrent_url = urljoin(base_url.rstrip("/") + "/forum/", dl_link.get("href", ""))
    elif topic_id:
        torrent_url = f"{base_url.rstrip('/')}/forum/dl.php?t={topic_id}"
    elif download_links:
        torrent_url = urljoin(
            base_url.rstrip("/") + "/forum/",
            download_links[0].get("href", ""),
        )

    return ParsedBook(
        external_id=topic_id or topic_url,
        external_url=topic_url,
        title=title,
        description=description,
        cover_url=cover_url,
        duration_seconds=duration,
        authors=authors,
        narrators=narrators,
        genres=genres,
        metadata_fields_present=metadata_fields_present,
        metadata_complete=bool(raw_topic_title and post is not None),
        series_name=series_name,
        series_external_id=topic_series.external_id if topic_series else "",
        series_position=series_position,
        series_entries=list(topic_series.entries) if topic_series else [],
        torrent=ParsedTorrent(
            info_hash=_info_hash_from_magnet(magnet),
            magnet_uri=magnet,
            torrent_url=torrent_url,
        ),
    )


class _BDecoder:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def parse(self):
        if self.pos >= len(self.data):
            raise ValueError("unexpected end of bencode")
        ch = self.data[self.pos:self.pos + 1]
        if ch == b"i":
            self.pos += 1
            end = self.data.index(b"e", self.pos)
            value = int(self.data[self.pos:end])
            self.pos = end + 1
            return value
        if ch == b"l":
            self.pos += 1
            out = []
            while self.data[self.pos:self.pos + 1] != b"e":
                out.append(self.parse())
            self.pos += 1
            return out
        if ch == b"d":
            self.pos += 1
            out = {}
            while self.data[self.pos:self.pos + 1] != b"e":
                key = self.parse()
                if not isinstance(key, bytes):
                    raise ValueError("bencode dict key must be bytes")
                out[key] = self.parse()
            self.pos += 1
            return out
        if ch.isdigit():
            colon = self.data.index(b":", self.pos)
            length = int(self.data[self.pos:colon])
            self.pos = colon + 1
            out = self.data[self.pos:self.pos + length]
            self.pos += length
            return out
        raise ValueError(f"invalid bencode at {self.pos}")


def _bencode(value) -> bytes:
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, list):
        return b"l" + b"".join(_bencode(x) for x in value) + b"e"
    if isinstance(value, dict):
        return b"d" + b"".join(_bencode(k) + _bencode(value[k]) for k in sorted(value)) + b"e"
    raise TypeError(type(value))


def _decode_path_part(value: bytes) -> str:
    for enc in ("utf-8", "cp1251"):
        try:
            return value.decode(enc)
        except UnicodeDecodeError:
            pass
    return value.decode("utf-8", errors="replace")


def parse_torrent_bytes(data: bytes, *, magnet_uri: str = "", torrent_url: str = "") -> ParsedTorrent:
    root = _BDecoder(data).parse()
    if not isinstance(root, dict) or b"info" not in root:
        raise ValueError("torrent metainfo has no info dictionary")
    info = root[b"info"]
    if not isinstance(info, dict):
        raise ValueError("invalid torrent info dictionary")
    info_hash = hashlib.sha1(_bencode(info)).hexdigest()

    files: list[ParsedTorrentFile] = []
    if b"files" in info:
        for index, item in enumerate(info.get(b"files") or []):
            if not isinstance(item, dict):
                continue
            parts = item.get(b"path.utf-8") or item.get(b"path") or []
            path = "/".join(_decode_path_part(x) for x in parts if isinstance(x, bytes))
            size = int(item.get(b"length") or 0)
            ext = PurePosixPath(path).suffix.casefold()
            files.append(ParsedTorrentFile(
                index=index, path=path, size_bytes=size,
                media_type="audio" if ext in _AUDIO_EXTS else "other",
            ))
    else:
        name = info.get(b"name.utf-8") or info.get(b"name") or b"audio"
        path = _decode_path_part(name) if isinstance(name, bytes) else str(name)
        size = int(info.get(b"length") or 0)
        ext = PurePosixPath(path).suffix.casefold()
        files.append(ParsedTorrentFile(
            index=0, path=path, size_bytes=size,
            media_type="audio" if ext in _AUDIO_EXTS else "other",
        ))

    return ParsedTorrent(
        info_hash=info_hash, magnet_uri=magnet_uri, torrent_url=torrent_url,
        total_size_bytes=sum(x.size_bytes for x in files), files=files,
    )


def _chapter_title(path: str, fallback_index: int) -> str:
    name = PurePosixPath(path).name
    stem = PurePosixPath(name).stem
    return _clean(stem) or f"Файл {fallback_index + 1}"


def _natural_key(value: str):
    return [int(x) if x.isdigit() else x.casefold() for x in re.split(r"(\d+)", value)]


def _estimated_chapter_durations(total_seconds: int, audio_files: list[ParsedTorrentFile]) -> list[int]:
    total_seconds = max(0, int(total_seconds or 0))
    if total_seconds <= 0 or not audio_files:
        return [0 for _ in audio_files]
    weights = [max(0, int(item.size_bytes or 0)) for item in audio_files]
    total_weight = sum(weights)
    if total_weight <= 0:
        base, remainder = divmod(total_seconds, len(audio_files))
        return [base + (1 if i < remainder else 0) for i in range(len(audio_files))]

    out: list[int] = []
    assigned = 0
    for index, weight in enumerate(weights):
        if index == len(weights) - 1:
            seconds = max(0, total_seconds - assigned)
        else:
            seconds = max(0, round(total_seconds * weight / total_weight))
            seconds = min(seconds, max(0, total_seconds - assigned))
        out.append(seconds)
        assigned += seconds
    if out and assigned != total_seconds:
        out[-1] = max(0, out[-1] + (total_seconds - assigned))
    return out


def detect_last_forum_page(html: str, *, forum_id: int, page_size: int = 50) -> int:
    soup = BeautifulSoup(html, "html.parser")
    last_page = 1
    page_size = max(1, int(page_size))
    for link in soup.select('a[href*="viewforum.php?f="]'):
        href = link.get("href", "")
        parsed = urlparse(urljoin("https://rutracker.org/forum/", href))
        qs = parse_qs(parsed.query)
        if str(qs.get("f", [""])[0]) != str(int(forum_id)):
            continue
        try:
            start = max(0, int(qs.get("start", ["0"])[0]))
        except (TypeError, ValueError):
            start = 0
        last_page = max(last_page, start // page_size + 1)
        text = _clean(link.get_text(" ", strip=True))
        if text.isdigit():
            last_page = max(last_page, int(text))
    text = _clean(soup.get_text(" ", strip=True))
    for pattern in (
        r"(?i)страниц(?:а|ы)?\s*[:：]?\s*(\d+)",
        r"(?i)страница\s+\d+\s+из\s+(\d+)",
        r"(?i)page\s+\d+\s+of\s+(\d+)",
    ):
        for match in re.finditer(pattern, text):
            last_page = max(last_page, int(match.group(1)))
    return max(1, last_page)


def hydrate_book_from_torrent(book: ParsedBook, torrent: ParsedTorrent) -> ParsedBook:
    book.torrent = torrent
    audio_files = sorted(
        (item for item in torrent.files if item.media_type == "audio"),
        key=lambda item: _natural_key(item.path),
    )
    if not audio_files:
        raise RuntimeError(f"RuTracker torrent metadata has no supported audio files: {book.external_url}")
    estimates = _estimated_chapter_durations(book.duration_seconds, audio_files)
    book.chapters = [
        ParsedChapter(
            external_id=str(item.index),
            position=pos,
            title=_chapter_title(item.path, pos),
            duration_seconds=estimates[pos],
            media_url=f"torrent://{torrent.info_hash}/{item.index}",
        )
        for pos, item in enumerate(audio_files)
    ]
    return book


class RuTrackerWorkerClient:
    def __init__(
        self, *, worker_url: str, worker_token: str,
        worker_token_header: str = "X-Proxy-Token", worker_mode: str = "mirror",
        base_url: str = "https://rutracker.org", delay_seconds: float = 0.15,
        timeout_seconds: float = 30.0, page_size: int = 50,
    ):
        self.worker_url = worker_url.rstrip("/")
        self.worker_token = worker_token
        self.worker_token_header = worker_token_header or "X-Proxy-Token"
        self.worker_mode = (worker_mode or "mirror").casefold()
        self.base_url = base_url.rstrip("/")
        self.delay_seconds = max(0.0, float(delay_seconds))
        self.page_size = max(1, int(page_size))
        self.client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout_seconds,
            headers={
                "User-Agent": "AudioBookRedCatalogPipeline/0.1.1",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
            },
        )

    def _headers(self) -> dict[str, str]:
        if self.worker_token_header.casefold() == "authorization":
            return {self.worker_token_header: f"Bearer {self.worker_token}"}
        return {self.worker_token_header: self.worker_token}

    def _request_url(self, target_url: str) -> str:
        if self.worker_mode == "fetch":
            sep = "&" if "?" in self.worker_url else "?"
            return f"{self.worker_url}{sep}url={quote(target_url, safe='')}"
        parsed = urlparse(target_url)
        suffix = parsed.path or "/"
        if parsed.query:
            suffix += "?" + parsed.query
        return self.worker_url + suffix

    async def _request(self, target_url: str, *, accept: str, referer: str = "") -> httpx.Response:
        if not self.worker_url:
            raise RuntimeError("RUTRACKER_WORKER_URL is required")
        if not self.worker_token:
            raise RuntimeError("RUTRACKER_WORKER_TOKEN is required")
        if self.delay_seconds:
            import asyncio
            await asyncio.sleep(self.delay_seconds)
        headers = self._headers()
        headers["Accept"] = accept
        headers["X-RuTracker-Target"] = target_url
        if referer:
            headers["Referer"] = referer
        response = await self.client.get(self._request_url(target_url), headers=headers)
        response.raise_for_status()
        return response

    async def get_html(self, target_url: str) -> str:
        response = await self._request(target_url, accept="text/html,application/xhtml+xml")
        return _decode_html(response.content, response.encoding)

    async def get_torrent(self, target_url: str, *, referer: str = "") -> bytes:
        response = await self._request(
            target_url, accept="application/x-bittorrent,*/*;q=0.8", referer=referer,
        )
        data = response.content
        if not data.startswith(b"d"):
            content_type = response.headers.get("content-type", "")
            raise RuntimeError(
                f"worker did not return bencoded torrent "
                f"(content-type={content_type or 'unknown'}, bytes={len(data)})"
            )
        return data

    def forum_url(self, forum_id: int, page: int) -> str:
        params = {"f": str(int(forum_id))}
        if int(page) > 1:
            params["start"] = str((int(page) - 1) * self.page_size)
        return f"{self.base_url}/forum/viewforum.php?{urlencode(params)}"

    def topic_url(self, topic_id: str) -> str:
        return f"{self.base_url}/forum/viewtopic.php?t={topic_id}"

    def torrent_url(self, topic_id: str) -> str:
        return f"{self.base_url}/forum/dl.php?t={topic_id}"

    async def aclose(self) -> None:
        await self.client.aclose()
