from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse


_SUSPICIOUS_TOKENS = (
    "static.rutracker",
    "/smiles/",
    "/images/smiles/",
    "emoji",
    "emoticon",
    "spacer",
    "pixel.gif",
    "blank.gif",
    "transparent",
    "banner",
    "badge",
    "button",
    "rank",
    "rating",
    "logo",
    "avatar",
    "icon_",
    "/icons/",
)


def _number(value: object) -> int | None:
    match = re.search(r"\d+", str(value or ""))
    if not match:
        return None
    try:
        parsed = int(match.group())
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _dimensions(node) -> tuple[int | None, int | None]:
    width = _number(node.get("width") or node.get("data-width") or node.get("data-w"))
    height = _number(node.get("height") or node.get("data-height") or node.get("data-h"))
    style = str(node.get("style") or "")
    if width is None:
        match = re.search(r"(?i)\bwidth\s*:\s*(\d+)px", style)
        width = int(match.group(1)) if match else None
    if height is None:
        match = re.search(r"(?i)\bheight\s*:\s*(\d+)px", style)
        height = int(match.group(1)) if match else None
    return width, height


def _suspicious_url(url: str) -> bool:
    folded = (url or "").casefold()
    return any(token in folded for token in _SUSPICIOUS_TOKENS)


def _valid_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme.casefold() in {"http", "https"} and bool(parsed.netloc)


def _score(node, url: str, *, post_image: bool) -> int | None:
    if not url or _suspicious_url(url):
        return None
    width, height = _dimensions(node)
    score = 20 if post_image else 0
    classes = " ".join(node.get("class") or []).casefold()
    if "img-right" in classes or "img-left" in classes:
        score += 8

    if width is None or height is None:
        # RuTracker's <var class=postImg title=...> frequently has no explicit
        # dimensions but is the canonical representation of a post image.
        return score + (12 if post_image else 1)

    if width < 80 or height < 80:
        return None
    ratio = width / height
    # Explicit horizontal strips/decor are never book covers.
    if ratio >= 1.35:
        return None
    # Extremely narrow assets are usually separators or broken thumbnails.
    if ratio < 0.28:
        return None
    if 0.45 <= ratio <= 0.82:
        score += 60
    elif 0.28 <= ratio < 0.45:
        score += 35
    elif 0.82 < ratio <= 1.05:
        score += 25
    else:
        score += 8
    # Prefer a useful image over a tiny thumbnail when aspect ratios tie.
    score += min(15, (width * height) // 100_000)
    return score


def select_cover_from_post(post, base_url: str) -> str:
    """Выбрать только правдоподобную книжную обложку из RuTracker post.

    Явно широкие/маленькие/static/smile/badge assets отбрасываются. Кандидаты с
    известными размерами ранжируются по book-like aspect ratio. Если размеры не
    опубликованы, canonical ``var.postImg`` остаётся допустимым, а обычный
    ``img`` получает минимальный приоритет. При отсутствии безопасного
    кандидата возвращается пустая строка — это лучше ложной декоративной полосы.
    """
    if post is None:
        return ""

    candidates: list[tuple[int, int, str]] = []
    order = 0
    for node in post.select("var.postImg[title]"):
        raw = str(node.get("title") or "").strip()
        url = raw if _valid_http_url(raw) else urljoin(base_url, raw)
        score = _score(node, url, post_image=True)
        if score is not None and _valid_http_url(url):
            candidates.append((score, -order, url))
        order += 1

    for node in post.select("img[src]"):
        raw = str(node.get("src") or "").strip()
        url = raw if _valid_http_url(raw) else urljoin(base_url, raw)
        score = _score(node, url, post_image=False)
        if score is not None and _valid_http_url(url):
            candidates.append((score, -order, url))
        order += 1

    if not candidates:
        return ""
    candidates.sort(reverse=True)
    return candidates[0][2]
