"""split.py: classification, ids, titles, building, paging, crawling and persistence (no network)."""
import asyncio
import json
import os
import tempfile

from _helpers import expect, finish

import split


def movie(i, adult=False):
    return {"id": f"m{i}", "name": f"Movie {i}", "is_series": "0", "censored": "1" if adult else "0"}


def show(i, adult=False):
    return {"id": f"s{i}", "name": f"Show {i}", "is_series": "1", "censored": "1" if adult else "0"}


def ids(entries):
    return [e["id"] for e in entries]


def roles(entry, sort=""):
    return {role: ids(by_sort[sort]) for role, by_sort in entry.buckets.items()}


# --- classification
expect("is_show truthy forms", [split.is_show({"is_series": v}) for v in ("1", 1, True)], [True] * 3)
expect("is_show falsy forms", [split.is_show({"is_series": v}) for v in ("0", 0, False, None, [], "")] + [split.is_show({})], [False] * 7)
expect("mixed = both kinds present, whatever the proportion",
       [split.looks_mixed(x) for x in ([movie(1), movie(2), movie(3), show(1)], [show(1), show(2), show(3), movie(1)], [show(1), movie(1)])], [True] * 3)
expect("not mixed: one kind or empty", [split.looks_mixed(x) for x in ([movie(1), movie(2)], [show(1), show(2)], [])], [False] * 3)

# --- ids
expect("virtual ids by role", [split.virtual_id("13", r) for r in ("other", "adult_main", "adult_other")], ["1000013", "2000013", "3000013"])
expect("split_id of virtual ids", [split.split_id(x) for x in ("1000013", "2000013", "3000013")], [("13", "other"), ("13", "adult_main"), ("13", "adult_other")])
expect("split_id of anything else", [split.split_id(x) for x in ("13", "4000013", "*", "", None, "0", "1000000")], [None] * 7)
expect("splittable ids", [split.is_splittable_id(x) for x in ("13", "*", "0", "1000013", "x")], [True, False, False, False, False])

# --- probe pages
expect("probe: spread evenly, ends on the last page", split.probe_pages(37, 3, 8), [4, 9, 13, 18, 23, 28, 32, 37])
expect("probe: few left -> all; none left or off -> []", (split.probe_pages(6, 3, 8), split.probe_pages(3, 3, 8), split.probe_pages(37, 3, 0)), ([4, 5, 6], [], []))
expect("probe: count of one is the last page", split.probe_pages(37, 3, 1), [37])

# --- names
expect("series_named", [split.series_named(t) for t in ("HINDI INDIAN SERIES (18+)", "TV|SERIES", "web series", "TAMIL NURSERY", "SERIESX", "KIDS")], [True, True, True, False, False, False])
expect("movies_title swaps the word, keeping its case",
       [split.movies_title(t) for t in ("HINDI INDIAN SERIES (18+)", "Hindi Series", "web series hd", "TV SERIES|HD", "Adult | X SERIES")],
       ["HINDI INDIAN MOVIES (18+)", "Hindi Movies", "web movies hd", "TV MOVIES|HD", "Adult | X MOVIES"])
expect("movies_title without the word falls back to a suffix", [split.movies_title("KIDS"), split.movies_title("SERIESX")], ["KIDS (MOVIES)", "SERIESX (MOVIES)"])

# --- build_category
T = {"total_items": "x", "max_page_items": 3, "selected_item": 0, "cur_page": 1}


def build(items, title="KIDS", **kw):
    return split.build_category(title, {"": (T, items), "added": (T, list(reversed(items)))}, **{"category_adult": False, "adult_enabled": True, **kw})


entry, whole = build([movie(1), show(1), movie(2), movie(3), show(2), movie(4), show(3), movie(5)])
expect("movies-primary: shows split out", (roles(entry), entry.adult, entry.shows_primary, whole),
       ({"main": ["m1", "m2", "m3", "m4", "m5"], "other": ["s1", "s2", "s3"]}, False, False, False))
expect("added order kept per view", ids(entry.buckets["main"]["added"]), ["m5", "m4", "m3", "m2", "m1"])
entry, _ = build([show(1), movie(1), show(2), show(3), movie(2), show(4)], title="WEB SERIES")
expect("a series-named category keeps its shows and moves the movies, though shows are the majority",
       (roles(entry), entry.shows_primary), ({"main": ["s1", "s2", "s3", "s4"], "other": ["m1", "m2"]}, True))
entry, _ = build([show(1), show(2), show(3), movie(1)])
expect("shows-majority, not series-named: the original keeps the movies, the shows go to (SERIES)", roles(entry), {"main": ["m1"], "other": ["s1", "s2", "s3"]})
entry, _ = build([movie(1), show(1)])
expect("a tie splits too", roles(entry), {"main": ["m1"], "other": ["s1"]})
expect("one kind only: nothing to split", (build([movie(1), movie(2)]), build([show(1), show(2)], title="X SERIES")), ((None, False), (None, False)))

mixed = [movie(1), movie(2), movie(3), movie(4), show(1), movie(5, True), movie(6, True), movie(7, True), show(2, True)]
entry, _ = build(mixed)
expect("adult entries leave the original; adult shows get their own view",
       roles(entry), {"main": ["m1", "m2", "m3", "m4"], "other": ["s1"], "adult_main": ["m5", "m6", "m7"], "adult_other": ["s2"]})
entry, _ = build([movie(1), movie(2), movie(5, True), show(2, True)])
expect("an adult tie splits as well", roles(entry), {"main": ["m1", "m2"], "adult_main": ["m5"], "adult_other": ["s2"]})
entry, _ = build([show(1, True), show(2, True), movie(1, True), movie(2), show(3)], title="WEB SERIES")
expect("series-named with adult entries: shows stay the main view in both groups",
       roles(entry), {"main": ["s3"], "other": ["m2"], "adult_main": ["s1", "s2"], "adult_other": ["m1"]})

entry, whole = build([movie(1), movie(2), show(1), movie(3)], category_adult=True)
expect("portal-flagged: whole adult, kinds still split, no adult views", (entry.adult, whole, roles(entry)), (True, True, {"main": ["m1", "m2", "m3"], "other": ["s1"]}))
expect("portal-flagged, one kind: rename only", build([movie(1), movie(2)], category_adult=True), (None, True))
expect("every entry censored, one kind: whole adult, rename only", build([movie(1, True), movie(2, True)]), (None, True))
entry, whole = build([movie(1, True), movie(2, True), show(1, True)])
expect("every entry censored with both kinds: whole adult, kinds split", (entry.adult, whole, sorted(entry.buckets)), (True, True, ["main", "other"]))
entry, whole = build(mixed, adult_enabled=False)
expect("adult labelling off: censored entries are ordinary", (sorted(entry.buckets), whole, ids(entry.buckets["main"][""])),
       (["main", "other"], False, ["m1", "m2", "m3", "m4", "m5", "m6", "m7"]))
expect("adult labelling off, one kind: no split", build([movie(1, True), movie(2)], adult_enabled=False), (None, False))

# --- titles and listing
movies_entry, _ = build(mixed)
series_entry, _ = build([show(1, True), show(2, True), movie(1, True), movie(2), show(3)], title="WEB SERIES")
whole_entry, _ = build([movie(1), movie(2), show(1)], category_adult=True)
whole_series, _ = build([show(1), movie(1)], title="WEB SERIES", category_adult=True)
titles = lambda e: [split.view_title(e, r, "Adult") for r in split.BUCKETS]
expect("titles, movies-primary", titles(movies_entry), ["KIDS", "KIDS (SERIES)", "Adult | KIDS", "Adult | KIDS (SERIES)"])
expect("titles, series-named", titles(series_entry), ["WEB SERIES", "WEB MOVIES", "Adult | WEB SERIES", "Adult | WEB MOVIES"])
expect("titles, whole-adult", (titles(whole_entry)[:2], titles(whole_series)[:2]), (["Adult | KIDS", "Adult | KIDS (SERIES)"], ["Adult | WEB SERIES", "Adult | WEB MOVIES"]))
expect("no prefix, no adult label", split.view_title(movies_entry, "adult_main", ""), "KIDS")

cats = json.dumps({"js": [{"id": "7", "title": "ACTION"}, {"id": "158", "title": "KIDS", "alias": "k", "censored": "0"}, {"id": "9", "title": "DRAMA"}]}).encode()
out = json.loads(split.add_virtual_categories(cats, {"158": movies_entry}, "Adult"))["js"]
expect("virtual entries follow the original, in role order", [(c["id"], c["title"]) for c in out],
       [("7", "ACTION"), ("158", "KIDS"), ("1000158", "KIDS (SERIES)"), ("2000158", "Adult | KIDS"), ("3000158", "Adult | KIDS (SERIES)"), ("9", "DRAMA")])
expect("other fields copied", (out[3]["alias"], out[3]["censored"]), ("k", "0"))
wcats = json.dumps({"js": [{"id": "5", "title": "WEB SERIES"}]}).encode()
expect("series-named entry lists a MOVIES view", [c["title"] for c in json.loads(split.add_virtual_categories(wcats, {"5": series_entry}, "Adult"))["js"]],
       ["WEB SERIES", "WEB MOVIES", "Adult | WEB SERIES", "Adult | WEB MOVIES"])
expect("no index / no match / junk -> same bytes", (split.add_virtual_categories(cats, {}, "Adult") is cats, split.add_virtual_categories(b"<html>", {"1": movies_entry}, "Adult"),
       split.add_virtual_categories(b'{"js": false}', {"1": movies_entry}, "Adult")), (True, b"<html>", b'{"js": false}'))
expect("int ids keep their type", [c["id"] for c in json.loads(split.add_virtual_categories(json.dumps({"js": [{"id": 158, "title": "K"}]}).encode(), {"158": movies_entry}, "Adult"))["js"]],
       [158, 1000158, 2000158, 3000158])

# --- pages
never = lambda title, real_id: False
index = {"158": movies_entry}
base = {"type": "vod", "action": "get_ordered_list", "category": "158", "genre": "0", "sortby": "", "p": "1"}


def page(idx=index, **over):
    return split.page_response({**base, **over}, idx, never, "Adult")


js = page()
expect("main view: exact total, page size from the portal", ([i["id"] for i in js["data"]], js["total_items"], js["max_page_items"]), (["m1", "m2", "m3"], "4", 3))
expect("last page partial, past the end empty", ([i["id"] for i in page(p="2")["data"]], page(p="3")["data"]), (["m4"], []))
expect("template extras kept, cur_page updated", (js["selected_item"], page(p="2")["cur_page"]), (0, 2))
expect("the other views", [([i["id"] for i in page(category=c)["data"]], page(category=c)["total_items"]) for c in ("1000158", "2000158", "3000158")],
       [(["s1"], "1"), (["m5", "m6", "m7"], "3"), (["s2"], "1")])
expect("added order", [i["id"] for i in page(category="2000158", sortby="added")["data"]], ["m7", "m6", "m5"])
expect("a series-named entry's main view is its shows", [i["id"] for i in page({"5": series_entry}, category="5")["data"]], ["s3"])
expect("absent genre/sortby/p default; JsHttpRequest tolerated",
       (split.page_response({k: v for k, v in base.items() if k not in ("genre", "sortby", "p")}, index, never, "Adult") is not None, page(JsHttpRequest="1-xml") is not None), (True, True))
for label, over in (("movie_id", {"movie_id": "5"}), ("search", {"search": "x"}), ("genre filter", {"genre": "12"}), ("other sort", {"sortby": "rating"}),
                    ("bad page", {"p": "x"}), ("page zero", {"p": "0"}), ("unknown category", {"category": "999"}), ("other type", {"type": "itv"}),
                    ("other action", {"action": "get_categories"}), ("virtual id of a missing view", {"category": "1000999"})):
    expect(f"passes through: {label}", page(**over), None)
sparse, _ = build([movie(1), movie(2), movie(5, True)])
expect("an id for a view that doesn't exist passes through", split.page_response({**base, "category": "3000158"}, {"158": sparse}, never, "Adult"), None)

seen = []
for c in ("158", "1000158", "2000158", "3000158"):
    split.page_response({**base, "category": c}, index, lambda t, i: seen.append((t, i)) or False, "Adult")
expect("blocked callback gets the displayed title and the REAL id", seen, [("KIDS", "158"), ("KIDS (SERIES)", "158"), ("Adult | KIDS", "158"), ("Adult | KIDS (SERIES)", "158")])
only_adult = lambda t, i: t.startswith("Adult")
expect("blocking the adult prefix hides only the adult views", [split.page_response({**base, "category": c}, index, only_adult, "Adult") is None for c in ("158", "1000158", "2000158", "3000158")], [False, False, True, True])
expect("a whole-adult entry is checked under its prefixed title", split.page_response(base, {"158": whole_entry}, only_adult, "Adult"), None)

# --- live per-genre pages
live_items = [{"id": f"c{i}", "tv_genre_id": "5", "censored": "1" if i in (2, 5) else "0"} for i in range(1, 8)]
live_entry, _ = build(live_items, title="SPORTS")
live_index = {"5": live_entry}


def lp(**over):
    return split.page_response({"type": "itv", "action": "get_ordered_list", "genre": "5", "p": "1", **over}, live_index, never, "Adult", live=True)


expect("live: clean channels, exact total", ([i["id"] for i in lp()["data"]], lp()["total_items"]), (["c1", "c3", "c4"], "5"))
expect("live: second page and the adult genre", ([i["id"] for i in lp(p="2")["data"]], [i["id"] for i in lp(genre="2000005")["data"]]), (["c6", "c7"], ["c2", "c5"]))
expect("live: default-valued extras tolerated, sortby ignored",
       [lp(sortby="number", fav="0", hd="0", force_ch_link_check="", JsHttpRequest="1-xml") is not None, lp(sortby="name") is not None], [True, True])
expect("live: filters and unknown params pass through", [lp(fav="1"), lp(hd="1"), lp(search="x"), lp(genre="999"), lp(p="0"), lp(genre="3000005")], [None] * 6)
expect("live: a vod-shaped request is not a live request", split.page_response({"type": "vod", "action": "get_ordered_list", "genre": "5", "p": "1"}, live_index, never, "Adult", live=True), None)

# --- crawl
failures_before = None


def make_fetcher(n_items, per_page=14, fail_page=None, dup=False, wrong_total=None, track=None):
    all_items = [{"id": str(i), "name": f"Item {i}"} for i in range(1, n_items + 1)]

    async def fetch(p):
        if track is not None:
            track["now"] += 1
            track["peak"] = max(track["peak"], track["now"])
            await asyncio.sleep(0.01)
            track["now"] -= 1
        if p == fail_page:
            return None
        chunk = all_items[:per_page] if dup and p == 2 else all_items[(p - 1) * per_page:p * per_page]
        return {"total_items": str(wrong_total or n_items), "max_page_items": per_page, "selected_item": 0, "data": chunk}
    return fetch, all_items


async def crawls():
    kw = dict(max_pages=100, concurrency=3, delay=0)
    fetch, all_items = make_fetcher(40)
    template, items = await split.crawl(fetch, **kw)
    expect("crawl: all pages in order", (ids(items) == ids(all_items), template), (True, {"total_items": "40", "max_page_items": 14, "selected_item": 0}))
    track = {"now": 0, "peak": 0}
    fetch, _ = make_fetcher(14 * 12, track=track)
    await split.crawl(fetch, **kw)
    expect("crawl: concurrency capped at 3 and really concurrent", (track["peak"] <= 3, track["peak"] >= 2), (True, True))
    for label, fk in (("a failed page", dict(fail_page=3)), ("a failed first page", dict(fail_page=1)), ("repeated entries", dict(dup=True)), ("a total mismatch", dict(wrong_total=45))):
        fetch, _ = make_fetcher(40, **fk)
        expect(f"crawl: {label} -> None", await split.crawl(fetch, **kw), None)
    fetch, _ = make_fetcher(14 * 5)
    try:
        await split.crawl(fetch, max_pages=4, concurrency=3, delay=0)
        raised = False
    except split.TooLarge:
        raised = True
    expect("crawl: over the cap -> TooLarge; at the cap is fine", (raised, (await split.crawl(fetch, max_pages=5, concurrency=3, delay=0)) is not None), (True, True))

    async def empty(p):
        return {"total_items": "0", "max_page_items": 14, "data": []}
    expect("crawl: empty category -> None", await split.crawl(empty, **kw), None)


asyncio.run(crawls())

# --- persistence
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "cache.json.split.json")
    split.save({"158": movies_entry, "5": series_entry}, path)
    loaded = split.load(path)
    expect("round trip keeps views, adult flag and shows_primary",
           (sorted(loaded), roles(loaded["158"], "added"), loaded["5"].shows_primary, loaded["158"].adult, loaded["158"].template),
           (["158", "5"], roles(movies_entry, "added"), True, False, T))
    expect("no temp file left behind", os.listdir(d), ["cache.json.split.json"])
    expect("missing / corrupt file -> {}", (split.load(os.path.join(d, "nope")), (open(path, "w").write("{bad"), split.load(path))[1]), ({}, {}))
    ok = {"title": "ok", "template": T, "adult": True, "buckets": {"main": {"": [], "added": []}}}
    json.dump({"version": 2, "categories": {"1": ok, "2": {**ok, "buckets": {"main": {"": []}}}, "3": "garbage", "4": {**ok, "buckets": {"bogus": {"": [], "added": []}}}, "5": {**ok, "buckets": {"other": {"": [], "added": []}}}}}, open(path, "w"))
    expect("bad entries dropped, good kept", list(split.load(path)), ["1"])
    json.dump({"version": 99, "categories": {}}, open(path, "w"))
    expect("unknown version -> {}", split.load(path), {})
    json.dump({"version": 1, "categories": {"7": {"title": "OLD", "template": T, "movies": {"": [movie(1)], "added": [movie(1)]}, "shows": {"": [show(1)], "added": [show(1)]}}}}, open(path, "w"))
    old = split.load(path)
    expect("a version-1 file (movies/shows) still loads as main/other", (list(old), sorted(old["7"].buckets), old["7"].shows_primary), (["7"], ["main", "other"], False))

finish()
