# VOD Movies + TV Series Support Design

## Goal

Extend the existing Stalker-to-M3U app (currently Live TV only, via `/playlist.m3u8`) to
also expose the portal's Video-on-Demand movie catalog and TV series catalog as separate
M3U playlists, with search support and working stream redirects.

## Scope

Two new catalogs, each with its own playlist endpoint:
- **VOD movies**: `/vod.m3u8`
- **TV series**: `/series.m3u8`, flattened to one playlist entry per episode

Live TV (`/playlist.m3u8`) is unchanged. Implementation proceeds in two phases: VOD
movies first (verified against the real portal), then series (reusing VOD's pagination
and category code).

## Portal API (reference, subject to verification against the live portal)

- `GET load.php?type=vod&action=get_categories&JsHttpRequest=1-xml` → `js.data[]`: `id`, `title`
- `GET load.php?type=vod&action=get_ordered_list&category=<id>&p=<page>&sortby=added&JsHttpRequest=1-xml`
  → `js: {data:[...], total_items, max_page_items, cur_page}`. Item fields: `id`, `name`,
  `o_name`, `category_id`, `cmd`, `series` (non-empty array ⇒ this is a series, not a movie).
- `GET load.php?type=vod&action=create_link&cmd=<cmd>&series=&JsHttpRequest=1-xml` → `js.cmd`
  (playable stream URL)
- Series categories/listing reuse the same `type=vod` (or `type=series`, to be confirmed
  live) `get_categories`/`get_ordered_list` actions.
- Seasons: `get_ordered_list` with `movie_id=<series id>` (no `season_id`) → season list.
- Episodes: `get_ordered_list` with `movie_id=<series id>&season_id=<season id>` → episode
  list, items have `name` like `"Episode 3"`.
- Episode stream link: `create_link` with `cmd=<episode cmd>&series=<episode_number>`.

Field names are drawn from a specific stalker_portal fork's source and community client
implementations, not one authoritative spec — **the first implementation step is a live
probe against the user's portal to confirm actual field names before hardcoding them.**

## Components

### `stalker_client.py` additions
- `get_categories(catalog_type: str) -> list` — `catalog_type` is `"vod"` or `"series"`.
- `get_ordered_list_page(catalog_type, category, page, movie_id=None, season_id=None) -> dict`
  — one page of results, returns the raw `{data, total_items, max_page_items, cur_page}`.
- `get_full_ordered_list(catalog_type, category, movie_id=None, season_id=None) -> list`
  — loops pages until exhausted, returns combined `data` list.
- `create_vod_link(cmd: str, episode_number: str | None = None) -> str` — wraps
  `create_link` with the VOD/episode-specific params.

### `vod.py` (new)
- `build_vod_m3u(config, movies, categories, origin, search=None) -> str` — one `#EXTINF`
  per movie (`group-title="VOD | {category}"`), optional in-memory substring filter on
  `name`/`o_name` when `search` is provided. Stream URL: `{origin}/vod/{movie_id}.m3u8`.
- `build_series_m3u(config, episodes, categories, origin, search=None) -> str` — one
  `#EXTINF` per flattened episode, named `"{series_name} S{season:02d}E{episode:02d}"`,
  `group-title="Series | {category}"`. Stream URL: `{origin}/series/{movie_id}/{episode_num}.m3u8`.
  Building this list requires, per series: fetch seasons, then per season fetch episodes —
  more portal calls than VOD movies, so response time will be noticeably higher for
  large series catalogs (acceptable for phase 1; caching is a future improvement, not in
  scope here).

### `main.py` additions
- `GET /vod.m3u8?search=<query>` — fetch VOD categories + full ordered list per category,
  build and return the movie playlist.
- `GET /series.m3u8?search=<query>` — fetch series categories, then per series the season/
  episode tree, build and return the flattened episode playlist.
- `GET /vod/{movie_id}.m3u8` — look up the movie's stored `cmd` (from a request-scoped
  lookup built during the `/vod.m3u8` call is NOT persisted across requests, so this route
  re-fetches the specific movie's `cmd` via a fresh `get_ordered_list` call filtered to
  that id, then calls `create_vod_link` and 302-redirects) — same "no server-side catalog
  cache" approach as the rest of the app.
- `GET /series/{movie_id}/{episode_num}.m3u8` — analogous: re-fetch the specific episode's
  `cmd` (season lookup via `movie_id`, episode lookup via `season_id` + episode number),
  then `create_vod_link(cmd, episode_num)` and 302-redirect.

## Error Handling

Same pattern as existing routes: portal call failures raise `PortalError`, surfaced as
HTTP 502. Missing movie/episode id on stream lookup → HTTP 404.

## Testing / Verification

1. Live probe against the user's real portal: fetch `get_categories` and one page of
   `get_ordered_list` for `type=vod`, confirm actual field names match the reference above
   (adjust code if they differ) before writing the full VOD feature.
2. Manual verification of `/vod.m3u8`: confirm categories, search filtering, and that a
   movie's stream redirect resolves to a real playable URL.
3. Manual verification of `/series.m3u8`: confirm episode flattening, naming format, and
   that an episode's stream redirect resolves to a real playable URL.
