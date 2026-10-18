"""End to end: the real background sync against a mock portal, then the real app over HTTP."""
import asyncio
import json
import logging
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
from starlette.testclient import TestClient

from _helpers import expect, finish

import adult
import main
import split

log_lines = []


class _Capture(logging.Handler):
    def emit(self, record):
        log_lines.append(record.getMessage())


logging.getLogger("main").addHandler(_Capture())

PER = 14
GENRES = [("*", "All", "0"), ("1", "NEWS", "0"), ("5", "SPORTS MIX", "0"), ("7", "BLANKET LIVE", "0"), ("643", "ASSAM", "0"), ("9", "LATE NIGHT", "1")]
CATS = [("10", "ACTION", "0"), ("120", "HINDI SERIES (18+)", "1"), ("121", "WEB SERIES (18+)", "1"), ("77", "SPICY", "0"), ("158", "KIDS MIXED", "0"),
        ("31", "PLAIN LONG", "0"), ("13", "KIDS SERIES", "0"), ("88", "ALL ADULT", "0"), ("89", "HUGE ADULT", "0"), ("90", "HUGE SOME", "0"),
        ("91", "BLANKET", "0"), ("92", "STRAY SERIES", "0"), ("93", "NEXT KIDS", "0"), ("94", "BIG SPARSE", "0"), ("95", "BIG PLAIN", "0"), ("*", "All", "0")]
SIZES = {"10": 30, "120": 14, "121": 40, "77": 40, "158": 60, "31": PER * 20, "13": 30, "88": 30, "89": PER * 120, "90": PER * 120,
         "91": 30, "92": PER * 20, "93": 30, "94": PER * 120, "95": PER * 120}


def is_adult(cid, i):
    return {"77": i in (16, 17), "158": i in (55, 56, 57), "31": i == 120, "88": True, "89": True, "90": i == 10, "91": i <= 26}.get(cid, False)


def is_show(cid, i):
    return {"121": i <= 30, "158": i % 3 == 0, "13": True, "91": i % 5 == 0, "92": i != 120, "93": i <= 20, "94": i > 60 and i % 20 == 0}.get(cid, False)


def vod_items(cid):
    return [{"id": f"{cid}-{i}", "name": f"{cid} item {i}", "category_id": cid, "is_series": "1" if is_show(cid, i) else "0",
             "censored": "1" if is_adult(cid, i) else "0"} for i in range(1, SIZES[cid] + 1)]


ASSAM = [{"id": f"a{i}", "name": f"Assam {i}", "tv_genre_id": "643", "censored": "1", "hd": "0"} for i in range(1, 21)]
MIX = [{"id": f"s{n}", "name": f"Sports {n}", "tv_genre_id": "5", "censored": "1" if n % 4 == 0 else "0", "hd": "0"} for n in range(1, 27)]
BL = [{"id": f"b{n}", "name": f"Blanket {n}", "tv_genre_id": "7", "censored": "0" if n <= 2 else "1", "hd": "0"} for n in range(1, 11)]
NEWS = [{"id": f"n{i}", "name": f"News {i}", "tv_genre_id": "1", "censored": "0", "hd": "0"} for i in range(1, 3)]
VISIBLE = NEWS + [c for c in MIX if c["censored"] == "0"] + BL[:2]   # the portal leaves the censored channels out of get_all_channels
seen = []


class Portal(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query, keep_blank_values=True).items()}
        seen.append(q)
        t, a = q.get("type"), q.get("action")
        if t == "itv" and a == "get_genres":
            js = [{"id": i, "title": n, "censored": c} for i, n, c in GENRES]
        elif t == "itv" and a == "get_all_channels":
            js = {"total_items": str(len(VISIBLE)), "data": VISIBLE}
        elif t == "itv" and a == "get_ordered_list":
            items = {"1": NEWS, "5": MIX, "7": BL, "643": ASSAM, "9": []}.get(q["genre"])
            p = int(q["p"])
            js = False if items is None else {"total_items": str(len(items)), "max_page_items": PER, "data": items[(p - 1) * PER:p * PER]}
        elif a == "get_categories":
            js = [{"id": i, "title": n, "censored": c} for i, n, c in CATS] if t == "vod" else False
        elif t == "vod" and a == "get_ordered_list" and q.get("category") in SIZES and "movie_id" not in q:
            cid, p = q["category"], int(q["p"])
            items = vod_items(cid)
            if q.get("sortby") == "added":
                items.reverse()
            js = {"total_items": str(len(items)), "max_page_items": PER, "data": items[(p - 1) * PER:p * PER]}
        else:
            js = False
        body = json.dumps({"js": js}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


server = ThreadingHTTPServer(("127.0.0.1", 0), Portal)
threading.Thread(target=server.serve_forever, daemon=True).start()
main.config.scheme, main.config.host = "http", f"127.0.0.1:{server.server_port}"
main.config.prewarm_delay_seconds = 0
main.config.cache_file_path = os.path.join(tempfile.mkdtemp(), "cache.json")


async def fake_session(role="realtime", max_age=None):
    return SimpleNamespace(token="tok", profile={}, account_info={})


main._get_valid_session = fake_session
http = TestClient(main.app)
LOAD = "/stalker_portal/server/load.php"
GEN = {"type": "itv", "action": "get_genres", "JsHttpRequest": "1-xml"}
CAT = {"type": "vod", "action": "get_categories", "JsHttpRequest": "1-xml"}


def sync():
    main.crawl_http = httpx.AsyncClient(timeout=15.0)  # a client per event loop
    asyncio.run(main._prewarm_all())


def titles(prefix, **params):
    return {c["id"]: c["title"] for c in http.get(f"{prefix}{LOAD}", params=params).json()["js"]}


def vpage(prefix, cid, p="1", sortby=""):
    return http.get(f"{prefix}{LOAD}", params={"type": "vod", "action": "get_ordered_list", "category": cid, "genre": "0", "sortby": sortby, "p": p}).json()["js"]


def sizes(index):
    return {c: {r: len(v[""]) for r, v in e.buckets.items()} for c, e in index.items()}


sync()
expect("views per category", sizes(main._split_index), {
    "77": {"main": 38, "adult_main": 2},
    "158": {"main": 38, "other": 19, "adult_main": 2, "adult_other": 1},
    "31": {"main": 279, "adult_main": 1},
    "91": {"main": 24, "other": 6},
    "121": {"main": 30, "other": 10},
    "92": {"main": 279, "other": 1},
    "93": {"main": 10, "other": 20}})
expect("a series-named category keeps its shows (even a mostly-shows flagged one); others keep their movies",
       {c: e.shows_primary for c, e in main._split_index.items() if c in ("121", "92", "93", "158")}, {"121": True, "92": True, "93": False, "158": False})
expect("a flagged category is adult as a whole", {c: e.adult for c, e in main._split_index.items() if e.adult}, {"121": True})
expect("100%-adult categories are renamed: flagged, all entries censored, too big but every sampled entry censored",
       main._adult_category_ids["vod"], {"120", "121", "88", "89"})
expect("too big with only some adult entries: left alone", ("90" in main._split_index, "90" in main._adult_category_ids["vod"]), (False, False))
expect("live: whole-adult genres, and the mixed one split", (main._adult_category_ids["itv"], sizes(main._live_split_index)), ({"643", "9"}, {"5": {"main": 20, "adult_main": 6}}))
expect("files written", (sorted(split.load(main._split_file_path())), sorted(split.load(main._live_split_file_path())), adult.load(main._adult_file_path())["vod"]),
       (sorted(main._split_index), ["5"], {"120", "121", "88", "89"}))

# --- the logs say what happened
expect("blanket flags are logged (vod and live)", [any(m.startswith(p) for m in log_lines) for p in ("Ignoring the adult flags in 'BLANKET'", "Ignoring the adult flags in live genre 'BLANKET LIVE'")], [True, True])
big = [m for m in log_lines if m.startswith("Not splitting 'BIG SPARSE'")]
expect("a big mixed category is reported with its sample", bool(big) and "sampled" in big[0] and "are shows" in big[0], True)
expect("a big category with only some adult entries is reported too", any(m.startswith("Not splitting 'HUGE SOME'") and "1 adult" in m for m in log_lines), True)
probed = sorted(int(q["p"]) for q in seen if q.get("category") == "95" and "genre" in q)
expect("a big show-free category is probed on 8 spread pages and never fetched in full", (len(probed), max(probed) <= 120), (8, True))

WANT_VOD = {"10": "ACTION", "120": "Adult | HINDI SERIES (18+)", "121": "Adult | WEB SERIES (18+)", "1000121": "Adult | WEB MOVIES (18+)",
            "77": "SPICY", "2000077": "Adult | SPICY", "158": "KIDS MIXED", "1000158": "KIDS MIXED (SERIES)", "2000158": "Adult | KIDS MIXED",
            "3000158": "Adult | KIDS MIXED (SERIES)", "31": "PLAIN LONG", "2000031": "Adult | PLAIN LONG", "13": "KIDS SERIES", "88": "Adult | ALL ADULT",
            "89": "Adult | HUGE ADULT", "90": "HUGE SOME", "91": "BLANKET", "1000091": "BLANKET (SERIES)", "92": "STRAY SERIES", "1000092": "STRAY MOVIES",
            "93": "NEXT KIDS", "1000093": "NEXT KIDS (SERIES)", "94": "BIG SPARSE", "95": "BIG PLAIN", "*": "All"}
for label, prefix in (("unfiltered", "/unfiltered"), ("filtered", "")):
    expect(f"{label} categories", titles(prefix, **CAT), WANT_VOD)
    g = titles(prefix, **GEN)
    expect(f"{label} genres: whole-adult renamed, mixed one split, blanket-flagged and clean untouched",
           (g["643"], g["9"], g["1"], g["5"], g["2000005"], g["7"], "2000007" in g), ("Adult | ASSAM", "Adult | LATE NIGHT", "NEWS", "SPORTS MIX", "Adult | SPORTS MIX", "BLANKET LIVE", False))
expect("series module untouched", http.get(LOAD, params={"type": "series", "action": "get_categories", "JsHttpRequest": "1-xml"}).json()["js"], False)

for prefix in ("/unfiltered", ""):
    tag = prefix or "filtered"
    p = vpage(prefix, "77")
    expect(f"{tag}: SPICY is its clean entries", (p["total_items"], len(p["data"]), {i["censored"] for i in p["data"]}), ("38", 14, {"0"}))
    a = vpage(prefix, "2000077")
    expect(f"{tag}: Adult | SPICY is the censored ones", (a["total_items"], [i["id"] for i in a["data"]]), ("2", ["77-16", "77-17"]))
    expect(f"{tag}: adult shows view", [i["id"] for i in vpage(prefix, "3000158")["data"]], ["158-57"])
    w, wm = vpage(prefix, "121"), vpage(prefix, "1000121")
    expect(f"{tag}: the flagged shows-majority category: shows in the original, movies in their own category",
           ((w["total_items"], {i["is_series"] for i in w["data"]}), (wm["total_items"], {i["is_series"] for i in wm["data"]})), (("30", {"1"}), ("10", {"0"})))
    expect(f"{tag}: a stray movie in an all-shows category", ((vpage(prefix, "92")["total_items"], vpage(prefix, "1000092")["total_items"]), [i["id"] for i in vpage(prefix, "1000092")["data"]]), (("279", "1"), ["92-120"]))
    expect(f"{tag}: a shows-majority category without 'series' in its name keeps its movies",
           ((vpage(prefix, "93")["total_items"], {i["is_series"] for i in vpage(prefix, "93")["data"]}), (vpage(prefix, "1000093")["total_items"], {i["is_series"] for i in vpage(prefix, "1000093")["data"]})), (("10", {"0"}), ("20", {"1"})))
expect("added order is served from the split too", [i["id"] for i in vpage("", "2000077", sortby="added")["data"]], ["77-17", "77-16"])

# --- live: channels and per-genre pages
def channels(prefix):
    return http.get(f"{prefix}{LOAD}", params={"type": "itv", "action": "get_all_channels", "JsHttpRequest": "1-xml"}).json()["js"]["data"]


def by_genre(items):
    out = {}
    for c in items:
        out[c["tv_genre_id"]] = out.get(c["tv_genre_id"], 0) + 1
    return out


def lpage(prefix, genre, p="1", **extra):
    return http.get(f"{prefix}{LOAD}", params={"type": "itv", "action": "get_ordered_list", "genre": genre, "p": p, **extra}).json()["js"]


for prefix in ("/unfiltered", ""):
    tag = prefix or "filtered"
    expect(f"{tag}: adult channels of the mixed genre are listed under its adult genre", by_genre(channels(prefix)), {"1": 2, "5": 20, "7": 10, "2000005": 6, "643": 20})
    clean, adult_page = lpage(prefix, "5"), lpage(prefix, "2000005")
    expect(f"{tag}: per-genre pages", (clean["total_items"], len(clean["data"]), {c["censored"] for c in clean["data"]}, adult_page["total_items"], {c["censored"] for c in adult_page["data"]}), ("20", 14, {"0"}, "6", {"1"}))
    expect(f"{tag}: TiviMate-style default extras accepted; a favourites filter goes to the portal", (lpage(prefix, "5", sortby="number", fav="0", hd="0")["total_items"], lpage(prefix, "5", fav="1")["total_items"]), ("20", "26"))

# --- the block list hides only the adult pieces
main._BLOCKED_PATTERNS = main._compile_whole_word_patterns(["adult"])
main._FILTERING_ENABLED = True
expect("blocklist ADULT: the adult pieces and 100%-adult categories are hidden, the clean remainders stay",
       set(titles("", **CAT)), {k for k, v in WANT_VOD.items() if not v.startswith("Adult")})
expect("unfiltered ignores the block list", set(titles("/unfiltered", **CAT)), set(WANT_VOD))
expect("the clean part of a mixed category is still served", len(vpage("", "77")["data"]), 14)
before = len(seen)
expect("a blocked adult view is not served from the index (filtered)", (vpage("", "2000077"), seen[before]["category"]), (False, "2000077"))
expect("unfiltered still serves it, from the index", (len(vpage("/unfiltered", "2000077")["data"]), len(seen) - before), (2, 1))
expect("blocklist ADULT (live): adult genres hidden, the clean part of the mixed genre stays", sorted(titles("", **GEN)), ["*", "1", "5", "7"])
expect("blocklist ADULT (live): get_all_channels keeps the clean channels; unfiltered keeps all",
       (by_genre(channels("")), by_genre(channels("/unfiltered"))), ({"1": 2, "5": 20, "7": 10}, {"1": 2, "5": 20, "7": 10, "2000005": 6, "643": 20}))
expect("blocklist ADULT (live): the clean genre is served, the adult one is not (filtered only)", (lpage("", "5")["total_items"], lpage("", "2000005"), lpage("/unfiltered", "2000005")["total_items"]), ("20", False, "6"))
main._BLOCKED_PATTERNS, main._FILTERING_ENABLED = [], False

# --- setting off: no adult labelling and no adult split, the kind split stays
main.config.adult_category_prefix = ""
main._split_index = {}
main._listing_cache.clear()
sync()
expect("empty prefix: censored entries are ordinary", sizes(main._split_index),
       {"158": {"main": 40, "other": 20}, "91": {"main": 24, "other": 6}, "121": {"main": 30, "other": 10}, "92": {"main": 279, "other": 1}, "93": {"main": 10, "other": 20}})
expect("empty prefix: no adult ids, no live split", (main._adult_category_ids["vod"], main._adult_category_ids["itv"], main._live_split_index), (set(), set(), {}))
expect("empty prefix: titles are the portal's", (titles("/unfiltered", **CAT)["120"], titles("/unfiltered", **CAT)["1000121"], titles("/unfiltered", **GEN)["643"]), ("HINDI SERIES (18+)", "WEB MOVIES (18+)", "ASSAM"))
main.config.adult_category_prefix = "Adult"
main._listing_cache.clear()
sync()

# --- restart: persisted state is loaded before any sync
saved = (sorted(main._split_index), sorted(main._live_split_index), {k: set(v) for k, v in main._adult_category_ids.items()})
main._split_index, main._live_split_index, main._adult_category_ids = {}, {}, {}
main._load_split_from_disk()
main._load_adult_from_disk()
expect("startup load restores all three", (sorted(main._split_index), sorted(main._live_split_index), main._adult_category_ids), saved)

# --- vod unreachable keeps its sets
before_adult = {k: set(v) for k, v in main._adult_category_ids.items()}
original = main._fetch_and_cache


async def no_vod(headers, params, http=None):
    return None if dict(params).get("type") == "vod" else await original(headers, params, http)


main._fetch_and_cache = no_vod
sync()
main._fetch_and_cache = original
expect("vod categories unreachable -> the vod set is kept", main._adult_category_ids["vod"], before_adult["vod"])

finish()
