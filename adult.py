"""Label categories that hold censored content with an "Adult" title prefix.

A category counts as adult when the portal flags it `censored`, or when entries inside it are
flagged (observed: a live genre that says censored=0 whose channels are all censored=1). The
title prefix is added to the listings the proxy serves, so it is visible to every app and, on
the filtered endpoint, to the block/allow lists (BLOCKED_CATEGORY_NAMES=ADULT hides them all).

Pure functions only: the caller decides which categories are adult and passes the ids in.
"""
import json
import os
import re
from typing import Callable, Collection, Iterable, Mapping

_FILE_VERSION = 1


def is_censored(entry) -> bool:
    return isinstance(entry, dict) and entry.get("censored") in (1, "1", True)


def censored_ids(entries: Iterable[dict], id_field: str) -> set[str]:
    """Ids (taken from `id_field`) of the entries that are flagged censored."""
    return {str(e[id_field]) for e in entries if is_censored(e) and e.get(id_field) is not None}


def _categories(content: bytes) -> list | None:
    try:
        data = json.loads(content)
    except ValueError:
        return None
    categories = data.get("js") if isinstance(data, dict) else None
    return categories if isinstance(categories, list) else None


def flagged_categories(content: bytes) -> set[str]:
    """Ids of the categories in a category listing that the portal itself flags censored."""
    return {str(c["id"]) for c in _categories(content) or [] if is_censored(c) and c.get("id") is not None}


def prefixed(title: str, prefix: str) -> str:
    """`Adult | <title>`, unless the title already starts with the prefix as a word."""
    if not prefix or re.match(re.escape(prefix) + r"(?![^\W_])", title, re.IGNORECASE):
        return title
    return f"{prefix} | {title}"


def mark_categories(
    content: bytes,
    adult_ids: Collection[str],
    prefix: str,
    resolve_id: Callable[[str], str] = str,
) -> bytes:
    """Prefix the title of every adult category in a category listing.

    A category is adult if it carries the portal's own flag or `resolve_id(its id)` is in
    `adult_ids` (the resolver maps a virtual category id back to the real one).
    """
    if not prefix:
        return content
    categories = _categories(content)
    if categories is None:
        return content
    out, changed = [], False
    for category in categories:
        if isinstance(category, dict) and isinstance(category.get("title"), str) and (
            is_censored(category) or resolve_id(str(category.get("id"))) in adult_ids
        ):
            title = prefixed(category["title"], prefix)
            if title != category["title"]:
                category = {**category, "title": title}
                changed = True
        out.append(category)
    if not changed:
        return content
    data = json.loads(content)
    data["js"] = out
    return json.dumps(data).encode()


def save(ids_by_type: Mapping[str, Collection[str]], path: str) -> None:
    payload = {"version": _FILE_VERSION, "categories": {t: sorted(ids) for t, ids in ids_by_type.items()}}
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(payload, f)
    os.replace(tmp_path, path)


def load(path: str) -> dict[str, set[str]]:
    try:
        with open(path) as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict) or payload.get("version") != _FILE_VERSION:
        return {}
    categories = payload.get("categories")
    if not isinstance(categories, dict):
        return {}
    return {str(t): {str(i) for i in ids} for t, ids in categories.items() if isinstance(ids, list)}
