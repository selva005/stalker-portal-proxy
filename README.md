# Stalker-Portal Proxy

A transparent reverse proxy for a Stalker-Portal account (MAC-based IPTV portal). Point
your MAG/Stalker-emulator STB apps at this proxy instead of the real portal, and they all
share one real authenticated account — full native app support (live TV, VOD, series, EPG,
whatever your account/app supports), no per-device MAC whitelisting needed.

## How it works

Auth requests (`handshake`, `get_profile`, `account_info`) are answered directly from one
cached, real session, so every connected device gets the same identity — this avoids
repeated re-handshakes upstream invalidating each other's tokens. Every other request
(channel/VOD/series listings, EPG, images, stream links) is forwarded to the real portal
verbatim, with the real account's credentials injected.

**Session freshness and recovery:** the cached session isn't trusted forever. Each time a
TV app's handshake/get_profile/account_info request comes in, if the cached session is
older than `SESSION_MAX_AGE_SECONDS` (default 30 minutes), it's proactively refreshed
before answering — this is checked on-demand when an app actually connects, not on a
background timer. This matters if you ever run a second instance of this proxy against the
same account/MAC (e.g. a dev/test environment) — Stalker-Portal backends generally bind one
active token per MAC, so a fresh handshake from one instance can silently invalidate the
other's session; the freshness check catches this the next time an app connects rather than
serving a dead session indefinitely. Separately, if a real proxied request (not one of the
three short-circuited actions above) gets a `401`/`403` from the real portal, the proxy
re-authenticates and **retries that same request once** before returning a response — so a
silently-invalidated session self-heals within the same request rather than requiring the
client to notice the failure and try again on its own.

## Configuration

Copy `.env.example` to `.env` and fill in your portal details:

```bash
cp .env.example .env
```

Required: `STALKER_HOST` (plain host, or prefixed with `http://`/`https://` if your
portal requires https — plain host defaults to `http://`), `STALKER_MAC`, `STALKER_SERIAL`.
Optional: `STALKER_DEVICE_ID`, `STALKER_DEVICE_ID2` (some portals don't require them),
`STALKER_STB_TYPE` (default `MAG250`), `STALKER_API_SIGNATURE` (default `263`),
`LOG_LEVEL` (default `INFO`), `BLOCKED_CATEGORY_NAMES` (comma-separated, case-insensitive
whole-word names to hide from live TV genre / VOD category / series category listings, e.g.
`ADULT,XXX`), `ALLOWED_CATEGORY_NAMES` (allowlist, same matching — when set, only matching
categories are shown; see [Category filtering](#category-filtering)),
`LISTING_CACHE_TTL_SECONDS` (default `21600` / 6 hours, how long to cache
listing responses and how often the background auto-sync re-runs), `PREWARM_DELAY_SECONDS`
(default `0.3`, delay between requests during auto-sync), `PREWARM_VOD_SERIES_PAGES`
(default `3`, how many pages of each VOD/series category to auto-sync), `CACHE_FILE_PATH`
(default `/data/cache.json`, where the cache is persisted across restarts), `EPG_FILE_PATH`
(default `/data/epg.xml`, where the generated XMLTV guide is persisted), `EPG_HOURS`
(default `24`, how many hours of upcoming programming to include per channel),
`EPG_PUBLISH_EVERY_N_CHANNELS` (default `200`, publish partial EPG results every N
channels during the crawl instead of waiting for the whole thing to finish),
`PREWARM_RATE_LIMIT_BACKOFF_SECONDS` (default `30`, how long to back off after a 429
during background auto-sync), `PREWARM_MAX_CONSECUTIVE_RATE_LIMITS` (default `5`, abort
the current crawl cycle after this many consecutive 429s), `EPG_FRESHNESS_TTL_SECONDS`
(default same as `LISTING_CACHE_TTL_SECONDS`, skip the startup EPG crawl if persisted
data is still within this age), `SESSION_MAX_AGE_SECONDS` (default `1800` / 30 minutes,
how long a cached session is trusted before the next handshake/get_profile proactively
re-authenticates instead of trusting the cache indefinitely).

## Run with Docker Compose

```bash
docker compose up --build
```

On each TV's STB emulator app, set the Portal URL to:

```
http://<homeserver-lan-ip>:8000/
```

MAC/device ID settings on each TV no longer matter — auth is handled entirely by the
proxy using the real account's credentials.

### Filtered vs. unfiltered endpoint

- `http://<homeserver-lan-ip>:8000/` — category filtering applied (per `BLOCKED_CATEGORY_NAMES`
  and `ALLOWED_CATEGORY_NAMES`): hidden categories are removed from genre/category
  listings, AND channels/VOD/series items tagged with a hidden category are removed from
  the channel/item listings too (not just hidden from the category name list — the content
  itself is filtered out).
- `http://<homeserver-lan-ip>:8000/unfiltered/` — same account, same session, but never
  filters anything. Point a specific TV/profile here if you want the full, unfiltered
  catalog on that device.

### Category filtering

Both lists are comma-separated, case-insensitive, and match **whole words**: a name matches
when it appears in the category title with no letter or digit directly on either side. So
`IN` matches `IN TAMIL`, `TAMIL|IN` and `TAMIL-IN`, but not `INDIA` or `BEGIN`. A name that
ends in punctuation, like `US|`, only enforces the boundary on its letter/digit edges, so it
matches `US|NEWS` (and not `RUS|NEWS`).

- `BLOCKED_CATEGORY_NAMES` — hide categories matching any name.
- `ALLOWED_CATEGORY_NAMES` — when set, hide every category that does **not** match any name.
  This is the easier option when the list you'd have to block is long, but it also hides
  categories the provider adds later until you add them here.
- If both are set, a category must match the allowlist and not match the blocklist.

### EPG

Some STB apps (e.g. TiviMate) fetch the whole program guide via a bulk portal call
(`get_epg_info`, no channel filter) that some Stalker-Portal accounts simply don't support
(a confirmed limitation on the real portal's side, not this proxy — it returns nothing at
all). This proxy works around it two ways, using the same underlying data (crawled via the
portal's working per-channel EPG action, `get_short_epg`):

- **Native**: `get_epg_info` requests (both the bulk form and the per-channel form) are
  answered directly by the proxy instead of being forwarded to the real portal — apps using
  the native Stalker Portal EPG integration (like TiviMate) should just get real guide data
  automatically, no extra configuration needed.
- **XMLTV fallback**: `http://<homeserver-lan-ip>:8000/epg.xml` serves the same data as a
  standard XMLTV document, for apps that expect a separate XMLTV guide URL instead.

Both are built in the background (part of the same auto-sync cycle as live TV/VOD/series)
covering the filtered channel set and `EPG_HOURS` of upcoming programming per channel, and
persisted to disk so a restart doesn't serve empty data until the next crawl finishes. On
large channel lists a full crawl can take 30-60+ minutes; results are published
incrementally every `EPG_PUBLISH_EVERY_N_CHANNELS` channels rather than waiting for the
whole crawl, so channels already fetched show real guide data well before the crawl ends.
If persisted EPG data is still fresh (within `EPG_FRESHNESS_TTL_SECONDS`) when the process
starts, the startup crawl is skipped entirely and the persisted data is served as-is until
the next scheduled background refresh — a restart doesn't force a full re-crawl.

The crawl also detects rate-limiting (429) responses from the portal, backs off
(`PREWARM_RATE_LIMIT_BACKOFF_SECONDS`) instead of continuing at full pace, and aborts the
current cycle (publishing whatever was gathered so far) after
`PREWARM_MAX_CONSECUTIVE_RATE_LIMITS` consecutive 429s — sustained rate-limiting from a
long-running crawl was found to also disrupt real-time STB app authentication (the crawl
and real-time traffic share one portal session, with a per-role failure cooldown so a
crawl-triggered auth failure doesn't block real devices' own re-authentication attempts).

## Run locally without Docker

```bash
pip install -r requirements.txt
export $(cat .env | xargs)
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Notes

- The portal session (token/profile/account info) is cached in memory and only
  re-authenticated when a portal call fails, avoiding repeated handshakes upstream.
- Genre, category, channel, VOD, and series listing responses are cached in memory for
  `LISTING_CACHE_TTL_SECONDS` to cut down on repeated portal hits from multiple TVs or
  app refreshes. Stream links (`create_link`) and EPG are never cached. The filtered and
  `/unfiltered/` endpoints share the same underlying cache entry — filtering is applied
  fresh on each response, not baked into the cached data.
- **Background auto-sync**, on startup and every `LISTING_CACHE_TTL_SECONDS` after that,
  regardless of whether any TV is in use:
  - Live TV: genres and the full channel list are fully synced (small enough to do so).
  - VOD/series: categories, plus the first `PREWARM_VOD_SERIES_PAGES` pages of every
    category. Full catalogs are too large to sync entirely (one category alone had
    36,000+ items at ~14/page) — full crawling would take a long time and risks
    re-triggering the portal's rate limiting. Anything beyond the pre-synced pages stays
    reactively cached as your TV app actually requests it.
- **Cache persistence**: the cache is saved to `CACHE_FILE_PATH` after each auto-sync and
  on shutdown, and reloaded on startup — a container restart doesn't start cold. Backed by
  the `cache_data` Docker volume in `docker-compose.yml`, so it survives
  `docker compose down`/`up` (not just a process restart).
- **EPG generation** runs after the VOD/category auto-sync (so the two background crawls
  don't compete for the same pacing budget at once), persisted to `EPG_FILE_PATH` the same
  way as the listing cache. The background crawl uses its own HTTP connection pool,
  separate from real-time proxied traffic, so a slow/hung crawl request can't compete with
  real devices for a connection.
- HTTP redirects from the portal (e.g. a domain move, or an `http`→`https` upgrade) are
  followed automatically rather than treated as a failure, preserving the account's
  identifying headers across the hop (httpx's own redirect handling would otherwise
  silently drop them on any cross-domain redirect, breaking auth without an obvious
  error). The resolved host is also remembered for later requests, so only the first
  request after a portal-side domain move pays the extra hop.
- Live TV channel listings (`get_all_channels`, live-TV `get_ordered_list`) get their `hd`
  flag set to `1` when the channel name contains `HD`, `FHD`, `UHD` or `4K` as a whole
  word. Some portals report `hd=0` for every channel even when the name says HD, which
  leaves apps with no HD badge. The flag is only ever turned on; a name without a tag
  doesn't mean the channel is SD, so those keep whatever the portal sent. Applies to both
  the filtered and `/unfiltered/` endpoints; the cache itself keeps the portal's raw data.
- Some portals leave certain live channels (observed: censored/adult ones) out of
  `get_all_channels` but still list them per genre (`get_ordered_list&genre=ID`). Apps that
  ask per genre (TiviMate) see them; apps that load the whole list once and group it by
  genre themselves (StbEmu) would show those genres empty. Each background sync therefore
  compares every genre's per-genre total with `get_all_channels` (one request per genre,
  paced by `PREWARM_DELAY_SECONDS`, plus extra pages for genres that fall short) and merges
  the missing channels into the `get_all_channels` responses. Your allow/block lists still
  apply to them. The result isn't persisted, so after a restart those genres stay empty in
  such apps until the first sync's live-TV step finishes (about a minute); if a sync is
  interrupted or rate-limited, the previous result is kept.
- Logs go to stdout (`docker logs`); control verbosity with `LOG_LEVEL`.
- Intended for LAN use across your own devices on your own account.

## Intended use

This is meant for personal use: sharing one Stalker-Portal account/subscription you
already own across your own devices on your own network, without each device needing
its own MAC whitelisting. It is not intended for redistributing portal access to other
people, and doing so may violate your provider's terms of service. You're responsible
for complying with the terms of whatever Stalker-Portal account you point this at.

## License

[MIT](LICENSE)
