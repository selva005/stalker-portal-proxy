# Split mixed movie/TV-show VOD categories

## Problem

Some portals keep TV shows inside ordinary VOD categories, flagged `is_series=1`, next to
movies. TiviMate files each VOD category under *Movies* or *TV Shows* and cannot draw the
other kind of entry, so a mixed category shows blank placeholder tiles for them (observed:
"KIDS DISNEY+", 365 items, about a third of them shows). The portal data is fine: pages are
complete, sorting is stable, posters exist, and TiviMate loads a show's seasons and episodes
natively through `type=vod&action=get_ordered_list&movie_id=...` once the show is in a
category it files under TV Shows.

Ruled out along the way: unstable paging, rate limiting, bad cache entries, relative poster
URLs, and the `is_movie`/`is_series` double label (a pure-series category carries the same
labels and works).

## Behaviour

On **both endpoints**. The split only restructures and hides nothing, so `/unfiltered/` gets it
too (an app there that handled shows inline now finds them in the `(SERIES)` category):

- A VOD category whose sample holds both shows and movies, in any proportion, is *mixed*; so is
  one whose sample holds adult entries (`censored`), which are split out the same way.
- The original keeps the *primary kind*: movies, unless the title says "series" (whole word), then
  shows. The other kind moves to a virtual copy of the category (every other field copied):
  `<title> (SERIES)` (id + 1,000,000), or for a series-named category the title with SERIES swapped
  for MOVIES (a movies category must not say "series": TiviMate may file by name). Adult entries go
  to `Adult | <title>` (id + 2,000,000) and their other kind to id + 3,000,000. Observed: "HINDI
  INDIAN SERIES (18+)" is 361 shows and 87 movies.
- Each view holds a single kind and is re-sliced to the portal's page size with an exact
  `total_items`, so there are no gaps. A group (clean or adult) is split whenever it holds both kinds.
- Only requests TiviMate actually sends are rewritten: `type=vod&action=get_ordered_list`
  with `category`, `genre` 0 or absent, `sortby` empty or `added`, and `p`. Anything else
  (`movie_id`, `season_id`, search, other sorts or filters) passes through untouched.
- On the filtered endpoint, allow/block lists apply to both titles, and a blocked category is
  not rewritten. `/unfiltered/` never applies them.

## How it is built

- **Detection** starts from the pages the background sync already pre-warms (first
  `PREWARM_VOD_SERIES_PAGES`, default 3, of every category). A category of at most
  `SPLIT_MAX_PAGES` pages whose sample is a single kind is probed: `SPLIT_PROBE_PAGES` (default 8) more
  pages spread evenly through it (all of them if fewer), because shows are often sparse
  (observed: 29 of 506 entries, none before page 4). A few hundred extra requests per sync.
- **Crawl** of each mixed category of at most `SPLIT_MAX_PAGES` (default 100) pages happens in
  the same sync, `SPLIT_CRAWL_CONCURRENCY` (default 3) pages at a time with the usual pacing,
  once per order (default and `added`). The result is accepted only if every page arrived and
  the distinct entries equal the portal's `total_items`; otherwise the previous result is kept
  (or the category is served raw, as before).
- **Serving** slices the stored lists: no parsing or upstream request per call.
- **Persistence** next to the listing cache (`<CACHE_FILE_PATH>.split.json`), loaded at startup
  so a restart does not bring the blanks back until the next sync. Each sync replaces the index
  in one step.
- TiviMate re-reads the category list and page 1 of every category each time it starts, so
  it picks the split up on its next start or playlist refresh; nothing needs live updating.

## Configuration

`SPLIT_MIXED_CATEGORIES` (default `true`, the kill switch), `SPLIT_MAX_PAGES` (100),
`SPLIT_CRAWL_CONCURRENCY` (3), `SPLIT_PROBE_PAGES` (8). No category list to maintain.

## Known limits

- A category whose other kind is so rare that the probe pages miss it is not detected.
- Categories over the page cap, such as one with 8,806 items, are not split.
- That TiviMate files the virtual category under TV Shows is expected (all shows, named
  "SERIES", so either a name-based or content-based rule fits) but unverified.
