"""Split mixed VOD categories into separate views: movies, TV shows and adult entries.

Some portals keep TV shows (is_series=1) inside ordinary VOD categories next to movies.
TiviMate files each category under Movies or TV Shows and can't draw the other kind of
entry, so a mixed category shows blank tiles. Adult entries (flagged `censored`) are mixed
into ordinary categories too. A category is therefore served as up to four:

  movies        the original category id: clean movies
  shows         id + 1,000,000, "<title> (SERIES)": clean shows
  adult_movies  id + 2,000,000, "Adult | <title>": adult movies
  adult_shows   id + 3,000,000, "Adult | <title> (SERIES)": adult shows

Only non-empty views exist, and each is re-sliced into full pages with an exact total. A
category that is entirely adult is not split into adult views: it keeps its id and is simply
labelled (the `adult` flag on the entry, or the caller's own rename when nothing else splits).

Everything here is independent of the network and of main.py's globals: the crawl takes an
injected page fetcher, and the lookups take the index as an argument.
"""
import asyncio
import json
import logging
import math
import os
from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping

import adult

logger = logging.getLogger("split")

ID_OFFSET = 1_000_000
# The view each virtual id offset (in multiples of ID_OFFSET) stands for; the real id is "movies".
_OFFSET_BUCKETS = {1: "shows", 2: "adult_movies", 3: "adult_shows"}
_BUCKET_OFFSETS = {bucket: offset for offset, bucket in _OFFSET_BUCKETS.items()}
BUCKETS = ("movies", "shows", "adult_movies", "adult_shows")
TITLE_SUFFIX = " (SERIES)"
# The orders TiviMate requests: the default one (home rows) and newest first (the grid).
SORTS = ("", "added")
# The only request shape that is rewritten; anything else (movie_id, search, other
# filters...) goes to the portal untouched.
_REQUEST_PARAMS = {"type", "action", "category", "genre", "sortby", "p", "JsHttpRequest"}
# Live per-genre listings. The stock STB sends a few more parameters; they only change the
# channel set when fav/hd are on, and the order is the portal's default either way.
_LIVE_PARAMS = {"type", "action", "genre", "p", "JsHttpRequest", "sortby", "fav", "hd", "force_ch_link_check"}
_FILE_VERSION = 2


class TooLarge(Exception):
    pass


@dataclass
class SplitCategory:
    title: str
    template: dict  # a portal page without "data": keeps total_items/max_page_items types
    buckets: dict[str, dict[str, list[dict]]]  # view -> sortby -> entries in the portal's order
    adult: bool = False  # the whole category is adult: its own title carries the prefix


def is_show(item: dict) -> bool:
    return item.get("is_series") in (1, "1", True)


def looks_mixed(sample: list[dict]) -> bool:
    """Mostly movies, but at least one show."""
    shows = sum(1 for item in sample if is_show(item))
    return 0 < shows < len(sample) - shows


def probe_pages(pages: int, sampled: int, count: int) -> list[int]:
    """Extra pages to look at in a category whose first `sampled` pages held nothing of interest.

    All remaining pages if there are at most `count` of them, otherwise `count` pages spread
    evenly from the first one after the sample to the last.
    """
    remaining = pages - sampled
    if remaining <= 0 or count <= 0:
        return []
    if remaining <= count:
        return list(range(sampled + 1, pages + 1))
    if count == 1:
        return [pages]
    return sorted({sampled + 1 + round(i * (remaining - 1) / (count - 1)) for i in range(count)})


def virtual_id(category_id: str, bucket: str) -> str:
    return str(int(category_id) + _BUCKET_OFFSETS[bucket] * ID_OFFSET)


def split_id(category_id) -> tuple[str, str] | None:
    """(real id, view) for a virtual category id, or None if it isn't one."""
    try:
        number = int(category_id)
    except (TypeError, ValueError):
        return None
    offset, real = divmod(number, ID_OFFSET)
    bucket = _OFFSET_BUCKETS.get(offset)
    return (str(real), bucket) if bucket and real > 0 else None


def is_splittable_id(category_id) -> bool:
    text = str(category_id)
    return text.isdigit() and 0 < int(text) < ID_OFFSET


def _to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _same_type(example, value: int):
    return str(value) if isinstance(example, str) else value


def view_title(entry: SplitCategory, bucket: str, prefix: str) -> str:
    """The title a view is listed under."""
    title = adult.prefixed(entry.title, prefix) if prefix and (entry.adult or bucket.startswith("adult_")) else entry.title
    return title + TITLE_SUFFIX if bucket.endswith("shows") else title


def add_virtual_categories(content: bytes, index: Mapping[str, SplitCategory], prefix: str) -> bytes:
    """Insert each split category's extra views after it in a category listing."""
    if not index:
        return content
    try:
        data = json.loads(content)
    except ValueError:
        return content
    categories = data.get("js") if isinstance(data, dict) else None
    if not isinstance(categories, list):
        return content
    out, added = [], False
    for category in categories:
        out.append(category)
        entry = index.get(str(category.get("id"))) if isinstance(category, dict) else None
        if entry is None:
            continue
        for bucket in BUCKETS[1:]:
            if bucket in entry.buckets:
                new_id = virtual_id(str(category["id"]), bucket)
                out.append({
                    **category,
                    "id": new_id if isinstance(category["id"], str) else int(new_id),
                    "title": view_title(SplitCategory(str(category.get("title", "")), entry.template, entry.buckets, entry.adult), bucket, prefix),
                })
                added = True
    if not added:
        return content
    data["js"] = out
    return json.dumps(data).encode()


def page_response(
    params: Mapping[str, str],
    index: Mapping[str, SplitCategory],
    is_blocked: Callable[[str, str], bool],
    prefix: str,
    *,
    live: bool = False,
) -> dict | None:
    """The `js` of a re-sliced page for a request on a split category, or None to pass it through.

    `is_blocked(title, real_category_id)` says whether the view, under the title it is listed
    with, is hidden by the block/allow lists; a hidden view is passed through so the usual item
    filtering applies. `live` selects live TV genres (`type=itv`, `genre=`) instead of VOD
    categories (`type=vod`, `category=`).
    """
    if params.get("type") != ("itv" if live else "vod") or params.get("action") != "get_ordered_list":
        return None
    if not set(params) <= (_LIVE_PARAMS if live else _REQUEST_PARAMS):
        return None
    if live:
        if params.get("fav", "0") not in ("", "0") or params.get("hd", "0") not in ("", "0"):
            return None
        sortby = SORTS[0]
    else:
        if params.get("genre", "0") not in ("", "0"):
            return None
        sortby = params.get("sortby", "")
        if sortby not in SORTS:
            return None
    try:
        page = int(params.get("p", "1"))
    except ValueError:
        return None
    if page < 1:
        return None

    category = params.get("genre" if live else "category", "")
    entry, bucket, original = index.get(category), "movies", category
    if entry is None:
        parsed = split_id(category)
        if parsed is None:
            return None
        original, bucket = parsed
        entry = index.get(original)
    if entry is None or bucket not in entry.buckets:
        return None
    if is_blocked(view_title(entry, bucket, prefix), original):
        return None

    items = entry.buckets[bucket][sortby]
    per_page = _to_int(entry.template.get("max_page_items")) or 14
    js = dict(entry.template)
    js["total_items"] = _same_type(entry.template.get("total_items", 0), len(items))
    if "cur_page" in js:
        js["cur_page"] = _same_type(js["cur_page"], page)
    js["data"] = items[(page - 1) * per_page:page * per_page]
    return js


FetchPage = Callable[[int], Awaitable["dict | None"]]


async def crawl(fetch_page: FetchPage, *, max_pages: int, concurrency: int, delay: float):
    """Fetch every page of one category and return (template, entries), or None if anything is off.

    `fetch_page(n)` returns a page's `js` (a dict with a `data` list) or None on failure. The
    result is only accepted when every page arrived and the distinct entries add up to the
    portal's own `total_items`: a partial or unstable list would be worse than none, because
    the caller then keeps serving the portal's raw pages.
    """
    first = await fetch_page(1)
    if first is None:
        return None
    total = _to_int(first.get("total_items"))
    per_page = _to_int(first.get("max_page_items")) or len(first["data"])
    if not total or not per_page:
        return None
    pages = math.ceil(total / per_page)
    if pages > max_pages:
        raise TooLarge(f"{pages} pages is over the cap of {max_pages}")

    gate = asyncio.Semaphore(max(1, concurrency))

    async def fetch(page: int):
        async with gate:
            result = await fetch_page(page)
            await asyncio.sleep(delay)
            return result

    rest = await asyncio.gather(*(fetch(page) for page in range(2, pages + 1)))
    if any(result is None for result in rest):
        return None
    items, seen = [], set()
    for result in [first, *rest]:
        for item in result["data"]:
            key = str(item.get("id"))
            if key not in seen:
                seen.add(key)
                items.append(item)
    if len(items) != total:
        logger.warning("Crawl returned %d distinct entries but the portal reports %d", len(items), total)
        return None
    return {key: value for key, value in first.items() if key != "data"}, items


def build_category(
    title: str,
    crawls: Mapping[str, tuple[dict, list[dict]]],
    *,
    category_adult: bool,
    adult_enabled: bool,
) -> tuple[SplitCategory | None, bool]:
    """Partition a fully crawled category into views.

    Returns (entry, whole_adult). `entry` is None when the category needs no split. A category is
    whole-adult when the portal flags it (`category_adult`) or every entry is censored; then there
    are no adult views, the category itself is the adult one. Otherwise censored entries form
    their own group. Within each group shows are split off only when the group is mostly movies
    with at least one show (the rule that keeps a show-heavy category's few movies in place).
    """
    first = crawls[SORTS[0]][1]
    whole = adult_enabled and (category_adult or (bool(first) and all(adult.is_censored(i) for i in first)))

    def group(item: dict) -> str:
        return "adult" if adult_enabled and not whole and adult.is_censored(item) else "main"

    split_shows = {g: looks_mixed([i for i in first if group(i) == g]) for g in ("main", "adult")}

    def bucket(item: dict) -> str:
        shows = is_show(item) and split_shows[group(item)]
        return ("adult_" if group(item) == "adult" else "") + ("shows" if shows else "movies")

    buckets: dict[str, dict[str, list[dict]]] = {}
    for sort, (_, items) in crawls.items():
        for item in items:
            buckets.setdefault(bucket(item), {s: [] for s in crawls})[sort].append(item)
    ordered = {name: buckets[name] for name in BUCKETS if name in buckets}
    if len(ordered) <= 1:
        return None, whole
    return SplitCategory(title=title, template=crawls[SORTS[0]][0], buckets=ordered, adult=whole), whole


def save(index: Mapping[str, SplitCategory], path: str) -> None:
    payload = {
        "version": _FILE_VERSION,
        "categories": {
            category_id: {"title": e.title, "template": e.template, "adult": e.adult, "buckets": e.buckets}
            for category_id, e in index.items()
        },
    }
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(payload, f)
    os.replace(tmp_path, path)


def load(path: str) -> dict[str, SplitCategory]:
    try:
        with open(path) as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict) or payload.get("version") not in (1, _FILE_VERSION):
        return {}
    index = {}
    for category_id, raw in (payload.get("categories") or {}).items():
        try:
            if payload["version"] == 1:  # movies/shows only, from before adult entries were split out
                buckets = {"movies": raw["movies"], "shows": raw["shows"]}
                buckets = {name: views for name, views in buckets.items() if any(views.values())}
            else:
                buckets = raw["buckets"]
            if "movies" not in buckets or not all(
                name in BUCKETS and all(sort in views for sort in SORTS) for name, views in buckets.items()
            ):
                continue
            index[str(category_id)] = SplitCategory(raw["title"], raw["template"], buckets, bool(raw.get("adult", False)))
        except (KeyError, TypeError, AttributeError):
            continue
    return index
