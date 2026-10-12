# Adult category prefix

## Problem

The portal marks adult content with a `censored` flag, but not consistently: a category's own
flag can say 0 while every entry inside is flagged (live genre ASSAM, 129 channels, all
`censored=1`, hidden from `get_all_channels` and served only per genre). Apps show no hint, and
hiding adult categories needs each one named in `BLOCKED_CATEGORY_NAMES`.

## Behaviour

- A category is *adult* if the portal flags it, or any entry in it is flagged.
- Its title gets the prefix `Adult | ` (word configurable, empty turns it off) in every category
  listing the proxy serves, filtered and `/unfiltered/` alike, for live genres and VOD
  categories. A title that already starts with the prefix as a word is left alone. The virtual
  `(SERIES)` copy of a split category is prefixed when its real category is adult.
- The prefix is added before the block/allow lists run, so `BLOCKED_CATEGORY_NAMES=ADULT`
  hides every adult category on the filtered endpoint, and the existing item filtering removes
  their channels/entries. The split index applies the same rule to the titles it checks.
- The cache keeps the portal's raw data; the label is applied to each response.

## How it is built

- `adult.py` holds the pure parts: `is_censored`, `censored_ids`, `flagged_categories`,
  `prefixed`, `mark_categories`, save/load.
- The portal's own category flag is read from the listing itself and needs no state. Categories
  found through their entries are kept in `_adult_category_ids` (by type), rebuilt by the
  background sync from data it already fetches, with no extra requests:
  live TV from `get_all_channels` plus the per-genre-only channels the discovery step merges;
  VOD from the pre-warmed pages, the split probe pages and the categories crawled for the split.
- A type whose category list could not be fetched keeps its previous ids. The ids are persisted
  to `<CACHE_FILE_PATH>.adult.json` and loaded at startup, so a restart does not un-label
  entry-derived categories (and, with ADULT blocked, show them) until the next sync.

## Known limits

- VOD detection by entries covers only the pages the sync reads; a category with a single
  censored entry deep inside, on none of them, is not labelled unless the portal flags it.
- With `SPLIT_MIXED_CATEGORIES=false` there are no probe pages, so VOD sees fewer pages.
