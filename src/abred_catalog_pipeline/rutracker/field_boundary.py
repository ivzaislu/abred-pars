from __future__ import annotations

"""Guard RuTracker metadata fields against nested-label spillover.

Some legacy topic markup wraps several metadata rows in one inline container.
The core parser already stops a field at a known ``span.post-b`` sibling, but
without this guard a known label nested inside the next sibling can be flattened
into the current value.  That can turn ``Фамилия автора: Мартин`` into a very
long string containing ``Имя автора``, narrator, genre and description fields.
"""

from bs4 import BeautifulSoup

from .parser import _KNOWN_POST_LABELS, _clean, _node_text, _normalized_post_label


_BOUNDARY_SENTINEL = "␞ABRED_FIELD_BOUNDARY␞"


def _node_text_until_nested_label(node) -> tuple[str, bool]:
    """Return node text before the first nested known metadata label."""
    if not hasattr(node, "select"):
        return str(node), False

    clone = BeautifulSoup(str(node), "html.parser")
    root = clone.find()
    if root is None:
        return "", False

    for nested in root.select("span.post-b"):
        label = _normalized_post_label(nested.get_text(" ", strip=True))
        if label not in _KNOWN_POST_LABELS:
            continue
        nested.insert_before(_BOUNDARY_SENTINEL)
        flattened = _node_text(root)
        return flattened.split(_BOUNDARY_SENTINEL, 1)[0], True

    return _node_text(node), False


def post_field_with_nested_label_guard(post, labels: tuple[str, ...]) -> str:
    """Parse one post metadata field and stop at nested known labels too."""
    if post is None:
        return ""

    wanted = {_normalized_post_label(value) for value in labels}
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
                direct_text = _node_text(node)
                # Preserve the core parser's existing rule: bold values are
                # allowed, while a bold known metadata label ends the field.
                if _normalized_post_label(direct_text) in _KNOWN_POST_LABELS:
                    break

            text, nested_boundary = _node_text_until_nested_label(node)
            text = _clean(text).lstrip(":：").strip()
            if text:
                values.append(text)
            if nested_boundary:
                break
            node = node.next_sibling

        value = _clean(" ".join(values)).lstrip(":：").strip()
        if value:
            return value
    return ""


__all__ = ["post_field_with_nested_label_guard"]
