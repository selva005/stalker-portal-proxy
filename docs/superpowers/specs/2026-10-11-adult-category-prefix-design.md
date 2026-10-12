# Adult content: prefix whole categories, split out the rest

## Problem

The portal marks adult content with a `censored` flag, inconsistently: a category's own flag can
say 0 while every entry inside is flagged (live genre ASSAM, 129 channels, all `censored=1`,
omitted from `get_all_channels` and served only per genre), and many ordinary categories hold a few
adult entries among clean ones. Apps show no hint, and hiding adult content needs each category
named in `BLOCKED_CATEGORY_NAMES`. Renaming a whole category for one flagged entry would hide
everything clean in it as soon as `ADULT` is blocked (an earlier version did exactly that).

## Behaviour

- An entry is adult if it is flagged `censored`, or its category is flagged by the portal.
- A category (VOD or live genre) that is adult as a whole (portal flag, or every entry flagged)
  is renamed in place with the prefix `Adult | ` (configurable; empty turns the feature off).
- A category with some adult entries keeps its clean entries; the adult ones move to a virtual
  `Adult | <title>` category (id + 2,000,000), and adult shows to `Adult | <title> (SERIES)`
  (id + 3,000,000). See the split design for the views and paging.
- Entry flags are ignored in a category the portal doesn't flag when most but not all of its entries
  are flagged (`adult.flags_unreliable`): observed on a "(PG)" anime category with 878 of 1,003
  entries flagged (Digimon, My Hero Academia). Such a flag is a blanket artifact, not an exception
  among clean entries. The category is then treated as clean (shows are still split) and the sync
  logs it. A category with every entry flagged is still adult (live genre ASSAM).
- Both endpoints. The prefix is added before the block/allow lists run, so
  `BLOCKED_CATEGORY_NAMES=ADULT` hides every adult piece on the filtered endpoint (and, through
  the existing item filtering, its channels and entries) and leaves the clean remainders.
- Live: in `get_all_channels` the adult channels of a split genre are listed under the adult
  genre's id (apps like StbEmu group that list by `tv_genre_id` themselves); per-genre listings
  are re-sliced. The portal's own per-genre-only channels come from the existing discovery step.
- The cache keeps the portal's raw data; the labelling is applied to each response.

## How it is built

- `adult.py`: `is_censored`, `flagged_categories`, `prefixed`, `mark_categories`, save/load (pure).
- `split.py` (see the split design): `build_category` partitions a fully crawled category into
  clean/adult groups and, within each, movies/shows; whole-adult categories are reported to the
  caller, which keeps their ids in `_adult_category_ids` (by type) for the in-place rename.
- Detection costs no extra requests beyond the split's own: VOD from the pre-warmed pages and the
  probe pages (shows or adult entries), then a full crawl of each candidate of at most
  `SPLIT_MAX_PAGES` pages. Over the cap, a category is renamed only if the portal flags it or
  every sampled entry is censored. Live: genres with a censored channel among those already known
  are crawled; where no crawl settles it, a genre counts as adult only if every known channel is.
- A failed crawl keeps the previous result and adult status. Everything is persisted
  (`.split.json`, `.livesplit.json`, `.adult.json`) and loaded at startup, so a restart does not
  show adult content unlabelled (and, with ADULT blocked, visible) until the next sync.

## Known limits

- A genuinely adult category that the portal doesn't flag and where most but not all entries are
  flagged is treated as clean (the flagged majority is not credible by itself).
- Entries are found through sampled pages (probe), so a category with very few adult entries on
  none of them is not split unless the portal flags it.
- Splitting mixed live genres relies on recognising the app's per-genre request; one in a shape
  the proxy doesn't recognise is passed through as the portal sends it.
