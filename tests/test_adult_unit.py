"""adult.py: flags, the blanket-flag rule, titles and persistence (no network)."""
import json
import os
import tempfile

from _helpers import expect, finish

import adult

expect("is_censored forms", [adult.is_censored({"censored": v}) for v in ("1", 1, True)], [True] * 3)
expect("not censored", [adult.is_censored({"censored": v}) for v in ("0", 0, False, None, "")] + [adult.is_censored({}), adult.is_censored("x")], [False] * 7)

cats = json.dumps({"js": [{"id": "1", "title": "A", "censored": "0"}, {"id": "120", "title": "B", "censored": "1"}, {"id": 7, "title": "C", "censored": 1}]}).encode()
expect("flagged_categories", adult.flagged_categories(cats), {"120", "7"})
expect("flagged_categories on junk", (adult.flagged_categories(b"<html>"), adult.flagged_categories(b'{"js": false}')), (set(), set()))


def flagged_of(n, total):
    return [{"censored": "1" if i < n else "0"} for i in range(total)]


expect("blanket flag: most but not all flagged", [adult.flags_unreliable(flagged_of(n, 10)) for n in (6, 9, 10)], [True, True, False])
expect("credible: half or fewer, none, or empty", [adult.flags_unreliable(flagged_of(n, 10)) for n in (0, 1, 5)] + [adult.flags_unreliable([])], [False] * 4)
expect("non-dict entries count as unflagged", adult.flags_unreliable([{"censored": "1"}, "junk", {"censored": "1"}, {"censored": "1"}]), True)

expect("prefix added", adult.prefixed("ASSAM", "Adult"), "Adult | ASSAM")
expect("already prefixed / starts with the word", [adult.prefixed(t, "Adult") for t in ("Adult | ASSAM", "ADULT MOVIES", "adult|x")], ["Adult | ASSAM", "ADULT MOVIES", "adult|x"])
expect("a word merely starting with it is prefixed", adult.prefixed("Adulthood", "Adult"), "Adult | Adulthood")
expect("empty prefix is off; custom prefix", (adult.prefixed("ASSAM", ""), adult.prefixed("ASSAM", "18+")), ("ASSAM", "18+ | ASSAM"))

listing = json.dumps({"js": [
    {"id": "1", "title": "NEWS", "censored": "0"}, {"id": "643", "title": "ASSAM", "censored": "0", "alias": "assam"},
    {"id": "9", "title": "LATE NIGHT", "censored": "1"}, {"id": 120, "title": "HINDI (18+)", "censored": 1},
    {"id": "1000158", "title": "KIDS MIXED (SERIES)", "censored": "0"}, {"title": "no id", "censored": "1"}, {"id": "5", "censored": "1"}, "junk"]}).encode()
titles = lambda c: [x.get("title") if isinstance(x, dict) else x for x in json.loads(c)["js"]]
out = adult.mark_categories(listing, {"643"}, "Adult")
expect("flag, id set, untouched", titles(out), ["NEWS", "Adult | ASSAM", "Adult | LATE NIGHT", "Adult | HINDI (18+)", "KIDS MIXED (SERIES)", "Adult | no id", None, "junk"])
expect("other fields kept", json.loads(out)["js"][1], {"id": "643", "title": "Adult | ASSAM", "censored": "0", "alias": "assam"})
expect("a resolver maps virtual ids to the real one", titles(adult.mark_categories(listing, {"158"}, "Adult", resolve_id=lambda c: "158" if c == "1000158" else c))[4], "Adult | KIDS MIXED (SERIES)")
nothing = json.dumps({"js": [{"id": "1", "title": "NEWS", "censored": "0"}]}).encode()
expect("nothing to mark / empty prefix / junk -> same bytes",
       (adult.mark_categories(nothing, {"643"}, "Adult") is nothing, adult.mark_categories(listing, {"643"}, "") is listing, adult.mark_categories(b"<html>", {"1"}, "Adult")), (True, True, b"<html>"))

with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "cache.json.adult.json")
    adult.save({"itv": {"643", "9"}, "vod": {"120"}, "series": set()}, path)
    expect("round trip", adult.load(path), {"itv": {"643", "9"}, "vod": {"120"}, "series": set()})
    expect("no temp file left", os.listdir(d), ["cache.json.adult.json"])
    open(path, "w").write("{bad")
    expect("missing / corrupt / unknown version", (adult.load(os.path.join(d, "nope")), adult.load(path), (json.dump({"version": 99, "categories": {"itv": ["1"]}}, open(path, "w")), adult.load(path))[1]), ({}, {}, {}))

finish()
