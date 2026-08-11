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

# Explicit AudioBookRed RuTracker catalog supplied by the project owner.
# Keep this source scoped to audiobook sections only.
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
    """Read RuTracker text while treating ``<wbr>`` as a zero-width break.

    ``get_text(" ")`` is normally desirable because adjacent inline elements
    still need a visible separator.  RuTracker also inserts ``<wbr>`` inside
    long words, however, and BeautifulSoup would turn that marker into a real
    space.  Remove only the wbr markup first, then let BeautifulSoup preserve
    normal element boundaries.
    """
    if node is None:
        return ""
    markup = re.sub(r"(?is)<wbr\b[^>]*>", "", str(node))
    clone = BeautifulSoup(markup, "html.parser")
    return _clean(clone.get_text(" ", strip=False))


def _decode_html(content: bytes, response_encoding: str | None = None) -> str:
    # RuTracker pages in the supplied real fixtures declare windows-1251.
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
    """Return a normalized 40-char hex BTIH from a magnet URI when present."""
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
    m = re.search(r"(?<!\d)(\d{1,3}):(\d{2}):(\d{2})(?!\d)", value)
    if m:
        h, minute, sec = map(int, m.groups())
        return h * 3600 + minute * 60 + sec
    m = re.search(r"(?<!\d)(\d{1,3}):(\d{2})(?!\d)", value)
    if m:
        minute, sec = map(int, m.groups())
        return minute * 60 + sec
    return 0


def parse_tracker_html(html: str, base_url: str) -> list[TrackerRow]:
    """Parse tracker.php result rows retained for source-side search compatibility."""
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
            topic_id=topic_id,
            topic_url=topic_url,
            torrent_url=torrent_url,
            title=_node_text(details),
            forum_id=forum_id,
            forum_name=forum_name,
            size_bytes=_size_bytes(size_raw),
            seeders=seeders,
            leechers=leechers,
        ))
    return rows


def parse_forum_html(html: str, base_url: str, forum_id: str | int = "") -> list[TrackerRow]:
    """Parse the real viewforum.php audiobook list.

    The supplied RuTracker page uses table.vf-table rows with a.torTopic and
    dl.php?t=... links. Rows without a downloadable torrent (announcements,
    notices, moderation-only topics) are intentionally excluded.
    """
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
            topic_id=topic_id,
            topic_url=topic_url,
            torrent_url=torrent_url,
            title=_node_text(details),
            forum_id=inferred_forum,
            forum_name=forum_name,
            size_bytes=_size_bytes(download.get_text(" ", strip=True)),
            seeders=_int(seeds.get_text(" ", strip=True)) if seeds else 0,
            leechers=_int(leeches.get_text(" ", strip=True)) if leeches else 0,
        ))
    return out


def _label_value(text: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        # Real RuTracker post markup places the bold label and ': value' on
        # adjacent text lines. Accept both same-line and next-line forms.
        m = re.search(rf"(?im)^\s*{re.escape(label)}\s*(?:\n\s*)?[:：]\s*(.+?)\s*$", text)
        if m:
            return _clean(m.group(1))
    return ""


_STANDALONE_PEOPLE_NOISE = {
    # Honorifics / clerical ranks occasionally leak out as their own list
    # entries when an old RuTracker template separates them with commas.
    # Keep ranks when they are part of a full display name; filter only an
    # entry that consists solely of the service word.
    "преподобный", "архимандрит", "диакон",
    "митрополит", "епископ", "архиепископ", "протоиерей",
    "иеромонах", "священник", "отец",
    # Collection shorthand is not a person.
    "др", "др.", "другие",
}


def _split_people(value: str) -> list[str]:
    if not value:
        return []
    out: list[str] = []
    for item in re.split(r"\s*(?:,|;|/| и )\s*", value):
        item = _clean(item)
        if not item or item.casefold() in _STANDALONE_PEOPLE_NOISE:
            continue
        if item not in out:
            out.append(item)
    return out[:12]


def _post_field(post, labels: tuple[str, ...]) -> str:
    if post is None:
        return ""
    wanted = {x.casefold() for x in labels}
    for bold in post.select("span.post-b"):
        label = _clean(bold.get_text(" ", strip=True)).casefold()
        if label not in wanted:
            continue
        values: list[str] = []
        node = bold.next_sibling
        while node is not None:
            name = getattr(node, "name", None)
            classes = set(getattr(node, "get", lambda *_: [])("class") or []) if name else set()
            if name in {"br", "hr"} or (name == "span" and "post-b" in classes):
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
    """Return whether a RuTracker metadata label is explicitly present.

    Presence is deliberately separate from value parsing: repair code must not
    interpret a missing field on a partial/error page as an authoritative empty
    value.
    """
    wanted = {x.casefold() for x in labels}
    if post is not None:
        for bold in post.select("span.post-b"):
            label = _clean(bold.get_text(" ", strip=True)).casefold()
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
        if _clean(bold.get_text(" ", strip=True)).casefold() != "описание":
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
    "митрополит", "епископ", "архиепископ", "архимандрит", "протоиерей",
    "иеромонах", "священник", "отец",
}


def _person_key(value: str) -> tuple[str, ...]:
    """Normalize a displayed person name for conservative author-prefix matching."""
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
    # Legacy topics freely mix full given names with initials.  Treat a
    # one-letter token as an initial only when it agrees with the first
    # letter of the full token; this is conservative enough for person names
    # and fixes e.g. ``Пушкин Александр`` vs ``Пушкин А.С.``.
    if len(left) == 1 < len(right):
        return right.startswith(left)
    if len(right) == 1 < len(left):
        return left.startswith(right)
    if min(len(left), len(right)) < 4:
        return False
    return SequenceMatcher(None, left, right).ratio() >= 0.84


def _author_prefix_matches(left: str, authors: list[str]) -> bool:
    """Match a subject prefix to one or several parsed authors.

    RuTracker subjects are old and inconsistent: spaces can be inserted by
    soft-break markup (``Реверт е``), one letter may be missing in a name,
    honorifics/aliases may be present, initials may replace full given names,
    and author order may differ from the metadata field.  Match each
    meaningful prefix token to an author token with a very small fuzzy
    tolerance, while still refusing unrelated title fragments.
    """
    author_tokens = [
        token
        for author in authors
        for token in _person_key(author)
        if token not in _AUTHOR_PREFIX_NOISE
    ]
    if not author_tokens:
        return False

    # Parenthesised aliases/titles often follow an otherwise exact author
    # name: ``Трунгпа Чогъям (Чогьям Трунгпа Ринпоче)``.  Try the complete
    # display string as well as the text outside / before the parentheses.
    variants = [left]
    without_parens = _clean(re.sub(r"\([^()]*\)", " ", left))
    before_paren = _clean(left.split("(", 1)[0])
    for candidate in (without_parens, before_paren):
        if candidate and candidate not in variants:
            variants.append(candidate)

    for variant in variants:
        ordered = [x for x in _person_tokens_ordered(variant) if x not in _AUTHOR_PREFIX_NOISE]
        token_variants: list[list[str]] = [ordered]
        # RuTracker inserts soft breaks inside long words.  If a displayed
        # author contains ``Ге нрих`` while metadata contains ``Генрих``, try
        # only adjacent-token merges that are actually explained by an author
        # token.  This avoids globally gluing normal multi-word names.
        for pos in range(max(0, len(ordered) - 1)):
            merged = ordered[pos] + ordered[pos + 1]
            if any(_token_close(merged, candidate) for candidate in author_tokens):
                token_variants.append(ordered[:pos] + [merged] + ordered[pos + 2:])

        for left_tokens in token_variants:
            # Duplicate author tokens occur in malformed legacy subjects such as
            # ``Акунин Борис Акунин - ...``.  They add no identity information.
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
                # Two explained tokens are enough for a person name; a one-token
                # author remains accepted only on an exact/fuzzy token match.
                if matched >= min(2, len(meaningful)):
                    return True
    return False


def _strip_release_suffix(raw_topic_title: str) -> tuple[str, bool]:
    """Remove RuTracker release metadata appended to a human book title.

    Audiobook topic names commonly end in one or more square-bracket groups,
    e.g. ``[Мария Орлова, 2026, 128 kbps, MP3]``.  We only start stripping
    when a group contains an unmistakable release marker (year, bitrate, audio
    format, etc.).  A few legacy topics append a short explanatory note after
    that release block; that whole technical tail is removed as one unit.
    """
    value = _clean(raw_topic_title)
    removed_release_group = False
    while value:
        match = re.search(r"\s*\[([^\[\]]*)\]\s*$", value)
        if not match:
            break
        content = _clean(match.group(1))
        if not removed_release_group and not _RELEASE_META_RE.search(content):
            break
        value = _clean(value[:match.start()])
        removed_release_group = True

    # A few releases use parenthesis for the technical suffix instead of [];
    # keep this conservative and remove it only when it contains a marker.
    match = re.search(r"\s*\(([^()]*)\)\s*$", value)
    if match and _RELEASE_META_RE.search(_clean(match.group(1))):
        value = _clean(value[:match.start()])
        removed_release_group = True

    # Legacy subjects sometimes append a correction/narrator note *after* a
    # normal release block, for example
    # ``Title [Reader, 2026, 96 kbps, MP3] (Other reader) - указан ...``.
    # The note prevents the simple trailing-group loop above from seeing the
    # release block.  Strip it only when the bracket itself is unmistakably
    # technical and the following text has the shape of a short annotation.
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

    # Very old RuTracker subjects also used slash-separated release metadata
    # instead of brackets, for example ``Title / 2007 / MP3 / 64 kbps``.
    # Strip only a run of at least two *consecutive trailing* technical
    # segments.  That keeps normal bilingual/alternate titles containing a
    # slash intact while removing unmistakable release tails.
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
        # Removing release metadata from ``Book 1, [release]`` must not leave
        # a dangling catalogue separator behind.
        value = re.sub(r"[\s,;:/\-–—]+$", "", value)
    return _clean(value), removed_release_group


_GENERIC_SUBJECT_PREFIX_RE = re.compile(
    r"(?i)^(?:сборники?|аудиокниги?|радиоспектакли?)\s*[-–—:]\s+"
)


def normalize_topic_subject_title(raw_topic_title: str, authors: list[str]) -> str:
    """Turn a RuTracker subject into a clean work title when body metadata lacks it.

    Example:
      ``Барро Сандра - Забытая жена [Мария Орлова, 2026, 128 kbps, MP3]``
      -> ``Забытая жена``

    The author prefix is removed only when it matches an author parsed from
    the post itself, so a legitimate dash inside a title is not guessed away.
    Older topics also use ``Автор: Название`` or omit the space before a dash.
    """
    value, _ = _strip_release_suffix(raw_topic_title)

    # A few old subjects carry a generic rubric before the real title.  This
    # is navigation noise, not part of the work title.
    value = _GENERIC_SUBJECT_PREFIX_RE.sub("", value, count=1)

    # Consider only separator forms that visually divide a prefix from a title:
    # a dash with whitespace on at least one side, or a colon followed by space.
    # This deliberately does not split hyphenated surnames such as
    # ``Перес-Реверте``.
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

    # Some old collection subjects insert the author between a rubric/series
    # and the work title: ``Жемчужины мудрости - Ледбитер Ч. У. - Ясновидение``.
    # Remove only a whole separator-delimited segment that matches a parsed
    # author, preserving both the collection prefix and the actual title.
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
    "год выпуска", "автор", "авторы", "фамилия автора", "имя автора",
    "исполнитель", "читает", "чтец", "жанр", "жанры", "прочитано по изданию",
    "тип издания", "категория", "аудиокодек", "битрейт", "вид битрейта",
    "частота дискретизации", "количество каналов (моно-стерео)",
    "время звучания", "продолжительность", "описание", "доп. информация",
    "цикл/серия", "цикл", "серия", "номер книги", "номер в серии", "№ книги",
}


def _title_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zа-яё0-9]+", _clean(value).casefold(), flags=re.I)
        if len(token) > 1
    }


def _body_title_related_to_subject(body_title: str, subject_title: str) -> bool:
    """Return true only when a post-body title clearly describes the subject.

    RuTracker templates are inconsistent: the first body heading can be a
    clean subtitle, but it can also be an author alias or another unrelated
    line.  Accept body titles only when they are textually anchored to the
    cleaned topic subject.
    """
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
    # Require most body-title words to be explained by the subject and at
    # least one meaningful shared token.  This accepts harmless punctuation
    # / inflection differences but rejects an unrelated author-name heading.
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
    """Use a related body heading only to heal spacing damage in the subject.

    The body heading is *not* allowed to shorten a series title.  If its
    alphanumeric text occurs inside the subject and the corresponding subject
    span has more whitespace (typical ``wbr`` damage), replace only that span
    with the body spelling while preserving all subject prefixes/suffixes.
    """
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
    # Only use the body spelling to repair extra spaces introduced inside
    # words.  Equal/fewer spaces means the subject is already structurally
    # sound and remains authoritative.
    if span.count(" ") <= body.count(" "):
        return subject
    return _clean(subject[:first] + body + subject[last:])


def _looks_like_person_prefix(value: str) -> bool:
    """Conservative fallback for old topics that omit an explicit author field."""
    text = _clean(value)
    if not text or any(ch.isdigit() for ch in text):
        return False
    words = re.findall(r"[A-Za-zА-Яа-яЁё]+(?:-[A-Za-zА-Яа-яЁё]+)?\.?", text)
    if len(words) == 1:
        # Pen names in the ranobe sections are sometimes a single Latin token
        # (e.g. ``McEnroe``).  Accept them only in this narrow form; the caller
        # still requires the body heading to confirm the right-hand title.
        bare = words[0].strip(".")
        return bool(re.fullmatch(r"[A-Za-z][A-Za-z'-]{2,39}", bare) and bare[:1].isupper())
    if not 2 <= len(words) <= 4:
        return False
    noise = {"сборник", "сборники", "аудиокнига", "аудиокниги", "радиоспектакль", "радиоспектакли"}
    if any(word.casefold().strip(".") in noise for word in words):
        return False
    # Person-name tokens are normally capitalized or initials.  Reject title
    # phrases containing ordinary lowercase words.
    for word in words:
        bare = word.strip(".")
        if len(bare) == 1:
            continue
        if not bare[:1].isupper():
            return False
    return True


def _looks_like_strict_colon_person_prefix(value: str) -> bool:
    """Narrow fallback for old ``ФИО: Название`` subjects.

    Unlike the general person-prefix heuristic this form is accepted without
    a confirming body heading only for two or three Cyrillic proper-name
    tokens.  It intentionally rejects one-word work prefixes (``Дао: ...``),
    lowercase noun phrases (``Красная площадь: ...``), long headings, Latin
    slogans and anything containing digits.
    """
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
    # Reject an obvious adjective-led title phrase such as
    # ``Великий Гэтсби: ...``.  This fallback is intentionally biased toward
    # false negatives because ordinary colon titles must remain untouched.
    if re.search(r"(?:ый|ий|ая|яя|ое|ее|ые|ие)$", words[0].casefold()):
        return False
    return True


def _infer_author_from_subject(raw_topic_title: str, body_title: str) -> str:
    """Infer an author only when a clean body heading confirms the right side.

    This is used solely for legacy topics without ``Автор``/``Фамилия автора``.
    Requiring a person-looking prefix *and* a matching body title avoids
    guessing on legitimate dashed work titles such as ``Москва - Петушки``.
    A few older radio-play topics use ``ФИО: Название``; that colon form is
    accepted only when the cleaned body heading matches the right side itself.
    """
    raw_value = _clean(raw_topic_title)
    value, _ = _strip_release_suffix(raw_topic_title)
    sep_re = re.compile(r"(?:\s+[-–—]\s*|\s*[-–—]\s+)")
    for match in sep_re.finditer(value):
        left = _clean(value[:match.start()])
        right = _clean(value[match.end():])
        if not left or not right or not _looks_like_person_prefix(left):
            continue
        if _body_title_related_to_subject(body_title, right):
            return left

    # Colon is common punctuation inside real titles, so be stricter than for
    # dashes: require a person-looking prefix and an exact alphanumeric match
    # between the cleaned body heading and the right-hand title.
    colon = re.match(r"^(.{2,100}?):\s+(.+)$", value)
    if colon:
        left = _clean(colon.group(1))
        right = _clean(colon.group(2))
        body_clean, _ = _strip_release_suffix(body_title)
        right_key, _ = _alnum_compact(right)
        body_key, _ = _alnum_compact(body_clean)
        legacy_year_suffix = bool(
            re.search(r"\((?:19|20)\d{2}\)\s*$", raw_value)
        )
        if (
            left
            and right
            and _looks_like_person_prefix(left)
            and right_key
            and (
                body_key == right_key
                or legacy_year_suffix
                or _looks_like_strict_colon_person_prefix(left)
            )
        ):
            return left
    return ""


def _select_topic_title(raw_topic_title: str, body_title: str, authors: list[str]) -> str:
    # The RuTracker topic subject is the catalogue identity.  Keep it intact
    # after removing author/release metadata; a body heading may be only a
    # subtitle or one volume name and must never shorten the subject.
    subject = normalize_topic_subject_title(raw_topic_title, authors)
    body = normalize_topic_subject_title(body_title, authors) if body_title else ""
    if subject:
        return _heal_subject_soft_breaks(subject, body)
    return body


def reconcile_repair_title(stored_title: str, parsed_title: str, authors: list[str]) -> str:
    """Choose a conservative title when repairing an existing RuTracker row.

    The repair may restore a fuller cleaned topic subject, but it must never
    collapse an existing series/volume title down to a subtitle.
    """
    stored = normalize_topic_subject_title(stored_title, authors)
    parsed = normalize_topic_subject_title(parsed_title, authors)
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

    # A remote full subject can restore a title that an older parser shortened.
    if stored_cf in parsed_cf:
        return parsed
    # Never go the other way: a body subtitle must not erase series/volume info.
    if parsed_cf in stored_cf:
        return stored

    if _body_title_related_to_subject(parsed, stored):
        # For punctuation/spacing variants prefer the candidate carrying at
        # least as much information as the stored title.
        if len(parsed_key) >= len(stored_key):
            return parsed
    return stored


def _topic_display_title(post, raw_topic_title: str, authors: list[str] | None = None) -> str:
    # In current audiobook releases the centered heading immediately preceding
    # metadata is the clean work title (for example "Путь строителя").
    if post is not None:
        for node in post.select("span.post-align")[:3]:
            text = _node_text(node)
            if not text:
                continue
            if re.fullmatch(r"(?i)(?:книга|том|часть)\s*\d+", text):
                continue
            if len(text) <= 300:
                return text

        # Some RuTracker templates put the clean book title as plain text on
        # the first line before ``Год выпуска`` instead of wrapping it in
        # ``span.post-align``. Prefer that line to the release subject.
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
    direct = _post_field(post, ("Автор", "Авторы")) or _label_value(post_text, ("Автор", "Авторы"))
    if direct:
        return _split_people(direct)
    surname = _post_field(post, ("Фамилия автора",)) or _label_value(post_text, ("Фамилия автора",))
    given = _post_field(post, ("Имя автора",)) or _label_value(post_text, ("Имя автора",))
    if surname or given:
        return [_clean(f"{surname} {given}")]
    return []


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
    r'(?i)^(?:серия|цикл|книги\s+цикла|книги\s+серии)\s*(?:[:：\-]\s*)?[«"“]?(.+?)[»"”]?$'
)


def _series_heading_name(value: str) -> str:
    text = _clean(value).strip(" -:：")
    if not text:
        return ""
    m = _SERIES_HEADING_RE.match(text)
    if not m:
        return ""
    name = _clean(m.group(1)).strip('«»"“” \t')
    # A bare label such as ``Серия`` is not a series name.
    if not name or name.casefold() in {"серия", "цикл"}:
        return ""
    return name


def _infer_topic_series_name(post) -> str:
    if post is None:
        return ""
    # Strong source-side signals only: a dedicated series/cycle heading in a
    # bold/link/spoiler header. This intentionally ignores prose mentions.
    for node in post.select("div.sp-head, a.postLink, span.post-b"):
        name = _series_heading_name(_node_text(node))
        if name:
            return name
    return ""


def _series_title_key(value: str) -> str:
    text = _clean(value).casefold().replace("ё", "е")
    # Normalize leading zeros in book numbers so ``Книга 01`` and ``Книга 1``
    # match when linking text-only RuTracker series lists to imported books.
    text = re.sub(r"(?<!\d)0+(\d+)", lambda m: str(int(m.group(1))), text)
    return "".join(re.findall(r"[a-zа-я0-9]+", text, flags=re.I))


def _series_hint_id(series_name: str, position: int, title: str) -> str:
    payload = f"{_series_title_key(series_name)}|{position}|{_series_title_key(title)}"
    return "series-hint:" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:24]


def _infer_series_position(title: str, body_title: str, series_name: str) -> int | None:
    for value in (title, body_title):
        text = _clean(value)
        if not text:
            continue
        m = re.search(r"(?i)\b(?:книга|том|часть)\s*(?:№\s*)?0*(\d{1,3})\b", text)
        if m:
            return int(m.group(1))
    # Legacy releases often use ``Series 4, Subtitle`` without a dedicated
    # number field. Only trust a number immediately after the known series name.
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


def _parse_series_lines(
    fragments: list[str],
    *,
    topic_url: str,
    base_url: str,
    series_name: str,
    current_position: int | None,
    current_title: str,
    authors: list[str],
    require_heading: bool,
) -> list[ParsedSeriesEntry]:
    current_topic = _topic_id(topic_url)
    entries: list[ParsedSeriesEntry] = []
    active = not require_heading
    seen_numbered = False

    for fragment in fragments:
        # A numbered line can be immediately followed by a new block without
        # another <br> (promo/footer/next spoiler). The list item itself uses
        # inline markup, so truncate at the first block boundary.
        fragment = re.split(r"(?is)<(?:div|table|hr)\b", fragment, maxsplit=1)[0]
        frag = BeautifulSoup(fragment, "html.parser")
        text = _clean(frag.get_text(" ", strip=True))
        if not text:
            continue

        heading = _series_heading_name(text)
        if heading and (not series_name or heading.casefold() == series_name.casefold()):
            active = True
            continue
        if require_heading and not active:
            # A heading can be nested inside the fragment rather than forming
            # the entire line (as on the supplied ``Лекарь Империи`` page).
            for node in frag.select("a.postLink, span.post-b, div.sp-head"):
                heading = _series_heading_name(_node_text(node))
                if heading and heading.casefold() == series_name.casefold():
                    active = True
                    break
            if not active:
                continue
            # If this fragment only contains the heading, consume it.
            if not re.search(r"(?:^|\s)\d{1,3}[.)]\s*", text):
                continue

        m = re.match(r"^(\d{1,3})[.)]\s*(.+)$", text)
        if not m:
            if active and seen_numbered:
                # Numbered series lists are contiguous on RuTracker. Stop at
                # the first unrelated line after the list to avoid parsing
                # comments/technical blocks as bibliography entries.
                break
            continue

        seen_numbered = True
        position = int(m.group(1))
        title = _clean(re.sub(r"\s*[-–—]\s*данный релиз\s*$", "", m.group(2), flags=re.I))
        if not title:
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
            or (
                current_title
                and _series_title_key(title) == _series_title_key(current_title)
            )
        )
        if not external_id and is_current and current_topic:
            external_url = topic_url
            external_id = current_topic
        if not external_id:
            # Text-only entries are still valuable: they preserve the source
            # series order and can be resolved to a real BookSource later.
            external_id = _series_hint_id(series_name, position, title)

        entries.append(ParsedSeriesEntry(
            external_id=external_id,
            external_url=external_url,
            title=title,
            position=position,
            authors=list(authors or []),
        ))

    return entries


def _parse_topic_series(
    post,
    topic_url: str,
    base_url: str,
    series_name: str,
    current_position: int | None,
    current_title: str = "",
    authors: list[str] | None = None,
) -> TopicSeries | None:
    if post is None or not series_name:
        return None
    current_topic = _topic_id(topic_url)
    all_entries: list[ParsedSeriesEntry] = []

    # Classic RuTracker template: cycle list lives in a spoiler.
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
            _series_fragments(body),
            topic_url=topic_url,
            base_url=base_url,
            series_name=series_name,
            current_position=current_position,
            current_title=current_title,
            authors=list(authors or []),
            require_heading=False,
        ))

    # Newer/alternate template: a bold ``Серия «... »`` search link followed
    # directly by a numbered plain-text list. This is the exact shape of the
    # supplied ``Лекарь Империи`` page.
    all_entries.extend(_parse_series_lines(
        _series_fragments(post),
        topic_url=topic_url,
        base_url=base_url,
        series_name=series_name,
        current_position=current_position,
        current_title=current_title,
        authors=list(authors or []),
        require_heading=True,
    ))

    if not all_entries:
        return None

    # Deduplicate entries collected from nested spoiler + whole-post parsing.
    deduped: dict[tuple[int, str], ParsedSeriesEntry] = {}
    for entry in all_entries:
        key = (entry.position, _series_title_key(entry.title))
        existing = deduped.get(key)
        # Prefer a real topic id over a synthetic text-only hint.
        if existing is None or (
            existing.external_id.startswith("series-hint:")
            and not entry.external_id.startswith("series-hint:")
        ):
            deduped[key] = entry
    entries = sorted(deduped.values(), key=lambda x: (x.position, x.title.casefold()))

    # Anchor the cache to the page we actually know how to refetch. Services
    # merge same-name RuTracker source-series rows, so importing book 2 before
    # book 1 does not create duplicate cycles.
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

    author_labels = ("Автор", "Авторы", "Фамилия автора", "Имя автора")
    narrator_labels = ("Исполнитель", "Читает", "Чтец")
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

    # Prefer a clean title from the body only when it is clearly related to
    # the cleaned topic subject.  This preserves canonical subtitle-only names
    # for series while rejecting unrelated first-line headings (for example an
    # alternate author spelling).
    body_title = (
        _post_field(post, ("Название", "Наименование", "Название книги", "Название произведения"))
        or _label_value(post_text, ("Название", "Наименование", "Название книги", "Название произведения"))
        or _topic_display_title(post, raw_topic_title, authors)
    )
    if not authors:
        inferred_author = _infer_author_from_subject(raw_topic_title, body_title)
        if inferred_author:
            authors = [inferred_author]
    title = _select_topic_title(raw_topic_title, body_title, authors) or f"RuTracker {topic_id}"
    narrators = _split_people(
        _post_field(post, ("Исполнитель", "Читает", "Чтец"))
        or _label_value(post_text, ("Исполнитель", "Читает", "Чтец"))
    )
    genre_value = _post_field(post, genre_labels) or _label_value(post_text, genre_labels)
    raw_genres = [_clean(x) for x in re.split(r"\s*[,;/]\s*", genre_value) if _clean(x)] if genre_value else []
    series_genre_hint = ""
    genres: list[str] = []
    for value in raw_genres:
        # Old topics sometimes put ``Серия: ...`` into the genre field. It is
        # useful as a source-series hint but must never surface as a genre chip.
        m = re.match(r"(?i)^серия\s*[:：]\s*(.+)$", value)
        if m:
            series_genre_hint = series_genre_hint or _clean(m.group(1))
            continue
        # ``аудиокнига`` is a carrier/category label, not a literary genre.
        if value.casefold() in {"аудиокнига", "audiobook"}:
            continue
        genres.append(value)
    description = _description_from_post(post) or _label_value(post_text, ("Описание",))
    if not description and post:
        description = _clean(post.get_text(" ", strip=True))[:5000]

    cover_url = _cover_from_post(post, base_url)
    duration = _duration_seconds(
        _post_field(post, ("Время звучания", "Продолжительность"))
        or _label_value(post_text, ("Время звучания", "Продолжительность"))
    )
    inferred_series_name = _infer_topic_series_name(post)
    series_name = _clean(
        _post_field(post, series_labels)
        or _label_value(post_text, series_labels)
        or inferred_series_name
        or series_genre_hint
    )
    if series_name and (inferred_series_name or series_genre_hint):
        metadata_fields_present.add("series")
    position_raw = (
        _post_field(post, position_labels)
        or _label_value(post_text, position_labels)
    )
    series_position = _int(position_raw) or _infer_series_position(title, body_title, series_name)
    topic_series = _parse_topic_series(
        post,
        topic_url,
        base_url,
        series_name,
        series_position,
        current_title=title,
        authors=authors,
    )

    magnet = ""
    magnet_link = soup.select_one('table.attach a.magnet-link[href^="magnet:?"], a.magnet-link[href^="magnet:?"], a[href^="magnet:?"]')
    if magnet_link:
        magnet = magnet_link.get("href", "")

    torrent_url = ""
    dl_link = soup.select_one('a.dl-stub[href*="dl.php?t="], a[href*="dl.php?t="]')
    if dl_link:
        torrent_url = urljoin(base_url.rstrip("/") + "/forum/", dl_link.get("href", ""))
    elif topic_id:
        torrent_url = f"{base_url.rstrip('/')}/forum/dl.php?t={topic_id}"

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
        torrent=ParsedTorrent(info_hash=_info_hash_from_magnet(magnet), magnet_uri=magnet, torrent_url=torrent_url),
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
            files.append(ParsedTorrentFile(index=index, path=path, size_bytes=size, media_type="audio" if ext in _AUDIO_EXTS else "other"))
    else:
        name = info.get(b"name.utf-8") or info.get(b"name") or b"audio"
        path = _decode_path_part(name) if isinstance(name, bytes) else str(name)
        size = int(info.get(b"length") or 0)
        ext = PurePosixPath(path).suffix.casefold()
        files.append(ParsedTorrentFile(index=0, path=path, size_bytes=size, media_type="audio" if ext in _AUDIO_EXTS else "other"))

    total = sum(x.size_bytes for x in files)
    return ParsedTorrent(
        info_hash=info_hash,
        magnet_uri=magnet_uri,
        torrent_url=torrent_url,
        total_size_bytes=total,
        files=files,
    )


def _chapter_title(path: str, fallback_index: int) -> str:
    name = PurePosixPath(path).name
    stem = PurePosixPath(name).stem
    return _clean(stem) or f"Файл {fallback_index + 1}"


def _natural_key(value: str):
    return [int(x) if x.isdigit() else x.casefold() for x in re.split(r"(\d+)", value)]


def _estimated_chapter_durations(total_seconds: int, audio_files: list[ParsedTorrentFile]) -> list[int]:
    """Distribute a source-reported total duration across torrent audio files.

    RuTracker topic pages commonly expose an accurate total playing time while
    torrent metadata exposes file sizes but no media durations. Audiobook
    releases normally use one codec/bitrate across chapters, so byte-weighted
    allocation is a much better server-side estimate than all-zero durations.
    The final chapter absorbs rounding so the estimates sum exactly to the
    source-reported total.
    """
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
    """Best-effort last-page detection from RuTracker viewforum pagination."""
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
    """HTTP adapter that sends every RuTracker request through a Cloudflare Worker."""

    def __init__(
        self,
        *,
        worker_url: str,
        worker_token: str,
        worker_token_header: str = "X-Proxy-Token",
        worker_mode: str = "mirror",
        base_url: str = "https://rutracker.org",
        delay_seconds: float = 0.15,
        timeout_seconds: float = 30.0,
        page_size: int = 50,
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

    async def _request(
        self,
        target_url: str,
        *,
        accept: str,
        referer: str = "",
    ) -> httpx.Response:
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
            target_url,
            accept="application/x-bittorrent,*/*;q=0.8",
            referer=referer,
        )
        data = response.content
        if not data.startswith(b"d"):
            content_type = response.headers.get("content-type", "")
            raise RuntimeError(
                f"worker did not return bencoded torrent (content-type={content_type or 'unknown'}, bytes={len(data)})"
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
