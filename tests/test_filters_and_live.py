"""Regression tests for older behaviour: whole-word block/allow lists, item filtering, the HD flag, and
the merge of live channels the portal serves only per genre."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from _helpers import expect, finish

import main

# --- whole-word matching
def matches(names, title):
    return any(p.search(title) for p in main._compile_whole_word_patterns(names))


expect("IN matches whole words only", [matches(["in"], t) for t in ("IN TAMIL", "TAMIL|IN", "TAMIL-IN", "INDIA", "BEGIN")], [True, True, True, False, False])
expect("a name ending in punctuation checks only its letter edges", [matches(["us|"], t) for t in ("US|NEWS", "RUS|NEWS")], [True, False])
expect("case-insensitive; names with digits", [matches(["18+"], "HINDI (18+)"), matches(["adult"], "ADULT | x"), matches(["4k"], "SKY 4K")], [True, True, True])

# --- block / allow lists
saved = (main._BLOCKED_PATTERNS, main._ALLOWED_PATTERNS)


def lists(blocked, allowed):
    main._BLOCKED_PATTERNS = main._compile_whole_word_patterns(blocked)
    main._ALLOWED_PATTERNS = main._compile_whole_word_patterns(allowed)


lists(["adult", "18+"], [])
expect("blocklist only", [main._is_blocked_category(t) for t in ("Adult | X", "HINDI (18+)", "NEWS")], [True, True, False])
lists([], ["english", "kids"])
expect("allowlist only: anything else is hidden", [main._is_blocked_category(t) for t in ("ENGLISH LATEST", "KIDS DISNEY+", "HINDI LATEST")], [False, False, True])
lists(["adult"], ["english", "kids"])
expect("both: must match the allowlist and not the blocklist", [main._is_blocked_category(t) for t in ("KIDS NETFLIX", "Adult | KIDS NETFLIX", "HINDI")], [False, True, True])
main._BLOCKED_PATTERNS, main._ALLOWED_PATTERNS = saved

# --- item filtering by blocked category ids
main._blocked_category_ids = {"itv": {"2"}, "vod": {"99"}}
saved_flag = main._FILTERING_ENABLED
main._FILTERING_ENABLED = True
ch = json.dumps({"js": {"data": [{"id": "a", "tv_genre_id": "1", "name": "A"}, {"id": "b", "tv_genre_id": "2", "name": "B"}]}}).encode()
ids = lambda content: [i["id"] for i in json.loads(content)["js"]["data"]]
expect("live listings are filtered by tv_genre_id (filtered endpoint only)",
       (ids(main._apply_filter_if_needed(ch, "itv", "get_all_channels", True)), ids(main._apply_filter_if_needed(ch, "itv", "get_all_channels", False)),
        ids(main._apply_filter_if_needed(ch, "itv", "get_ordered_list", True))), (["a"], ["a", "b"], ["a"]))
vod = json.dumps({"js": {"data": [{"id": "x", "category_id": "5"}, {"id": "y", "category_id": "99"}]}}).encode()
expect("vod listings are filtered by category_id", ids(main._apply_filter_if_needed(vod, "vod", "get_ordered_list", True)), ["x"])
nothing = json.dumps({"js": {"data": [{"id": "x", "category_id": "5"}]}}).encode()
expect("nothing to filter -> the original bytes", main._apply_filter_if_needed(nothing, "vod", "get_ordered_list", True) is nothing, True)
main._FILTERING_ENABLED = saved_flag
main._blocked_category_ids = {}

# --- HD flag from the name
channels = {"js": {"data": [
    {"id": "1", "name": "CNN HD", "hd": "0", "tv_genre_id": "1"}, {"id": "2", "name": "BBC FHD", "hd": "0", "tv_genre_id": "1"},
    {"id": "3", "name": "SKY 4K", "hd": "0", "tv_genre_id": "1"}, {"id": "4", "name": "MTV UHD", "hd": "0", "tv_genre_id": "1"},
    {"id": "5", "name": "hd movies", "hd": "0", "tv_genre_id": "1"}, {"id": "6", "name": "CSPAN", "hd": "0", "tv_genre_id": "1"},
    {"id": "7", "name": "NICK SD", "hd": "0", "tv_genre_id": "1"}, {"id": "8", "name": "HDTV NEWS", "hd": "0", "tv_genre_id": "1"},
    {"id": "9", "name": "ABCHD", "hd": "0", "tv_genre_id": "1"}, {"id": "10", "name": "ESPN HD+", "hd": "0", "tv_genre_id": "1"},
    {"id": "11", "name": "ALREADY HD", "hd": "1", "tv_genre_id": "1"}, {"id": "12", "name": "NO FLAG HD CHANNEL", "tv_genre_id": "1"},
    {"id": "13", "name": None, "hd": "0", "tv_genre_id": "1"}, {"id": "14", "name": "IN|SPORTS HD", "hd": "0", "tv_genre_id": "2"}]}}
raw = json.dumps(channels).encode()
hd = lambda content: {i["id"]: str(i.get("hd")) for i in json.loads(content)["js"]["data"]}
out = hd(main._apply_filter_if_needed(raw, "itv", "get_all_channels", False))
expect("tagged HD/FHD/4K/UHD turn on", [out[k] for k in "1 2 3 4 5 10 14".split()], ["1"] * 7)
expect("untagged / SD / lookalikes stay off", [out[k] for k in "6 7 8 9 13".split()], ["0"] * 5)
expect("already-HD untouched; a missing hd field is set when tagged", (out["11"], out["12"]), ("1", "1"))
expect("per-genre live listings get it too", hd(main._apply_filter_if_needed(raw, "itv", "get_ordered_list", True))["1"], "1")
movie = json.dumps({"js": {"data": [{"id": "1", "name": "Some Movie HD", "hd": "0"}]}}).encode()
expect("vod untouched; non-json and list-shaped js pass through",
       (main._apply_filter_if_needed(movie, "vod", "get_ordered_list", True) is movie, main._apply_filter_if_needed(b"<html>", "itv", "get_all_channels", False), main._apply_filter_if_needed(b'{"js": []}', "itv", "get_all_channels", False)),
       (True, b"<html>", b'{"js": []}'))

# --- live channels the portal serves only per genre
PAGE = 14


def ch_(i, name, genre, censored="0"):
    return {"id": str(i), "name": name, "tv_genre_id": str(genre), "censored": censored, "hd": "0"}


visible = [ch_(1, "TAMIL ONE", 1), ch_(2, "TAMIL TWO HD", 1), ch_(3, "N1", 2), ch_(6, "MIX A", 700)]
by_genre = {"1": [c for c in visible if c["tv_genre_id"] == "1"], "2": [c for c in visible if c["tv_genre_id"] == "2"],
            "643": [ch_(1000 + i, f"WEB {i}", 643, "1") for i in range(1, 32)], "700": [c for c in visible if c["tv_genre_id"] == "700"] + [ch_(2000, "MIX HIDDEN HD", 700, "1")]}
genres = {"js": [{"id": "*", "title": "All"}, {"id": "1", "title": "IN TAMIL"}, {"id": "2", "title": "NEWS"}, {"id": "643", "title": "WEB"}, {"id": "700", "title": "MIXED"}]}
mode, requests_seen = {"fail": None}, []


class Portal(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        requests_seen.append(q)
        if mode["fail"] == q.get("genre"):
            self.send_response(500)
            self.end_headers()
            return
        items, p = by_genre.get(q["genre"]), int(q["p"])
        body = json.dumps({"js": {"total_items": str(len(items)), "max_page_items": PAGE, "data": items[(p - 1) * PAGE:p * PAGE]}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


server = HTTPServer(("127.0.0.1", 0), Portal)
threading.Thread(target=server.serve_forever, daemon=True).start()
main.config.scheme, main.config.host = "http", f"127.0.0.1:{server.server_port}"
genres_bytes, channels_bytes = json.dumps(genres).encode(), json.dumps({"js": {"total_items": str(len(visible)), "data": visible}}).encode()


async def run():
    await main._discover_per_genre_only_channels({}, genres_bytes, channels_bytes)
    found = sorted(c["id"] for c in main._extra_live_channels)
    expect("discovery finds hidden channels in fully- and partly-hidden genres", (len(found), "2000" in found, "1001" in found, "1031" in found), (32, True, True, True))
    expect("pages through a genre only as far as it needs", [(q["genre"], q["p"]) for q in requests_seen if q["genre"] == "643"], [("643", "1"), ("643", "2"), ("643", "3")])
    expect("never asks for the non-numeric 'All' genre; complete genres stay at page 1", (any(q["genre"] == "*" for q in requests_seen), [q["p"] for q in requests_seen if q["genre"] in ("1", "2")]), (False, ["1", "1"]))
    before = list(main._extra_live_channels)
    mode["fail"] = "643"
    await main._discover_per_genre_only_channels({}, genres_bytes, channels_bytes)
    expect("a failed discovery keeps the previous result", main._extra_live_channels, before)
    mode["fail"] = None
    out = main._apply_filter_if_needed(channels_bytes, "itv", "get_all_channels", filtered=False)
    expect("get_all_channels gets the hidden channels, total_items adjusted with its type, hd derived",
           (len(ids(out)), json.loads(out)["js"]["total_items"], {i["id"]: i["hd"] for i in json.loads(out)["js"]["data"]}["2000"]), (len(visible) + 32, str(len(visible) + 32), "1"))
    expect("merging twice adds no duplicates", len(ids(main._apply_filter_if_needed(out, "itv", "get_all_channels", False))), len(visible) + 32)
    main._blocked_category_ids = {"itv": {"643"}}
    main._FILTERING_ENABLED = True
    got = set(ids(main._apply_filter_if_needed(channels_bytes, "itv", "get_all_channels", True)))
    expect("filtered: a blocked genre's hidden channels are removed, other genres' kept", ("1001" in got, "2000" in got), (False, True))
    main._blocked_category_ids, main._FILTERING_ENABLED = {}, False
    main._extra_live_channels = []


asyncio.run(run())
finish()
