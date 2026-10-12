"""Split mixed movie/TV-show VOD categories into a movies view and a "(SERIES)" view.

Some portals keep TV shows (is_series=1) inside ordinary VOD categories next to movies.
TiviMate files each category under Movies or TV Shows and can't draw the other kind of
entry, so a mixed category shows blank tiles. For categories that are mostly movies but
contain shows, the filtered endpoint serves the original category with only its movies and a
virtual "<title> (SERIES)" category with only its shows, both re-sliced into full pages with
exact totals.

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

logger = logging.getLogger("split")

ID_OFFSET = 1_000_000
TITLE_SUFFIX = " (SERIES)"
# The orders TiviMate requests: the default one (home rows) and newest first (the grid).
SORTS = ("", "added")
# The only request shape that is rewritten; anything else (movie_id, search, other
# filters...) goes to the portal untouched.
_REQUEST_PARAMS = {"type", "action", "category", "genre", "sortby", "p", "JsHttpRequest"}
_FILE_VERSION = 1


class TooLarge(Exception):
    pass


@dataclass
class SplitCategory:
    title: str
    template: dict  # a portal page without "data": keeps total_items/max_page_items types
    movies: dict[str, list[dict]]  # sortby -> entries in the portal's order
    shows: dict[str, list[dict]]


def is_show(item: dict) -> bool:
    return item.get("is_series") in (1, "1", True)


def looks_mixed(sample: list[dict]) -> bool:
    """Mostly movies, but at least one show."""
    shows = sum(1 for item in sample if is_show(item))
    return 0 < shows < len(sample) - shows


def probe_pages(pages: int, sampled: int, count: int) -> list[int]:
    """Extra pages to look at in a category whose first `sampled` pages held no show.

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


def virtual_id(category_id: str) -> str:
    return str(int(category_id) + ID_OFFSET)


def real_id(category_id) -> str | None:
    """The real category id behind a virtual one, or None if it isn't virtual."""
    try:
        number = int(category_id)
    except (TypeError, ValueError):
        return None
    return str(number - ID_OFFSET) if number >= ID_OFFSET else None


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


def add_virtual_categories(content: bytes, index: Mapping[str, SplitCategory]) -> bytes:
    """Insert a "<title> (SERIES)" entry after each split category in a category listing."""
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
        if isinstance(category, dict) and str(category.get("id")) in index:
            new_id = virtual_id(str(category["id"]))
            out.append({
                **category,
                "id": new_id if isinstance(category["id"], str) else int(new_id),
                "title": f"{category.get('title', '')}{TITLE_SUFFIX}",
            })
            added = True
    if not added:
        return content
    data["js"] = out
    return json.dumps(data).encode()


def page_response(
    params: Mapping[str, str], index: Mapping[str, SplitCategory], is_blocked: Callable[[str, str], bool]
) -> dict | None:
    """The `js` of a re-sliced page for a request on a split category, or None to pass it through.

    `is_blocked(title, real_category_id)` says whether the category as listed (the movies view
    under its own title, the shows view under "<title> (SERIES)") is hidden by the block/allow
    lists; a hidden category is passed through so the usual item filtering applies.
    """
    if params.get("type") != "vod" or params.get("action") != "get_ordered_list":
        return None
    if not set(params) <= _REQUEST_PARAMS:
        return None
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

    category = params.get("category", "")
    entry, view, original = index.get(category), "movies", category
    if entry is None:
        original = real_id(category)
        entry, view = (index.get(original) if original else None), "shows"
    if entry is None:
        return None
    if is_blocked(entry.title if view == "movies" else entry.title + TITLE_SUFFIX, original):
        return None

    items = (entry.movies if view == "movies" else entry.shows)[sortby]
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


def build_category(title: str, crawls: Mapping[str, tuple[dict, list[dict]]]) -> SplitCategory:
    """Partition each crawled order into movies and shows."""
    return SplitCategory(
        title=title,
        template=crawls[SORTS[0]][0],
        movies={sort: [i for i in items if not is_show(i)] for sort, (_, items) in crawls.items()},
        shows={sort: [i for i in items if is_show(i)] for sort, (_, items) in crawls.items()},
    )


def save(index: Mapping[str, SplitCategory], path: str) -> None:
    payload = {
        "version": _FILE_VERSION,
        "categories": {
            category_id: {"title": e.title, "template": e.template, "movies": e.movies, "shows": e.shows}
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
    if not isinstance(payload, dict) or payload.get("version") != _FILE_VERSION:
        return {}
    index = {}
    for category_id, raw in (payload.get("categories") or {}).items():
        try:
            movies, shows = raw["movies"], raw["shows"]
            if not all(sort in movies and sort in shows for sort in SORTS):
                continue
            index[str(category_id)] = SplitCategory(raw["title"], raw["template"], movies, shows)
        except (KeyError, TypeError):
            continue
    return index
