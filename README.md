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

## Configuration

Copy `.env.example` to `.env` and fill in your portal details:

```bash
cp .env.example .env
```

Required: `STALKER_HOST`, `STALKER_MAC`, `STALKER_SERIAL`.
Optional: `STALKER_DEVICE_ID`, `STALKER_DEVICE_ID2` (some portals don't require them),
`STALKER_STB_TYPE` (default `MAG250`), `STALKER_API_SIGNATURE` (default `263`),
`LOG_LEVEL` (default `INFO`), `BLOCKED_CATEGORY_NAMES` (comma-separated, case-insensitive
substrings to hide from live TV genre / VOD category / series category listings, e.g.
`ADULT,XXX`), `LISTING_CACHE_TTL_SECONDS` (default `21600` / 6 hours, how long to cache
listing responses and how often the background auto-sync re-runs), `PREWARM_DELAY_SECONDS`
(default `0.3`, delay between requests during auto-sync), `PREWARM_VOD_SERIES_PAGES`
(default `3`, how many pages of each VOD/series category to auto-sync), `CACHE_FILE_PATH`
(default `/data/cache.json`, where the cache is persisted across restarts), `EPG_FILE_PATH`
(default `/data/epg.xml`, where the generated XMLTV guide is persisted), `EPG_HOURS`
(default `24`, how many hours of upcoming programming to include per channel).

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

- `http://<homeserver-lan-ip>:8000/` — category filtering applied (per `BLOCKED_CATEGORY_NAMES`):
  blocked categories are hidden from genre/category listings, AND channels/VOD/series items
  tagged with a blocked category are removed from the channel/item listings too (not just
  hidden from the category name list — the content itself is filtered out).
- `http://<homeserver-lan-ip>:8000/unfiltered/` — same account, same session, but never
  filters anything. Point a specific TV/profile here if you want the full, unfiltered
  catalog on that device.

### XMLTV guide (`/epg.xml`)

Some STB apps (e.g. TiviMate) fetch the whole program guide in one bulk portal call that
some Stalker-Portal accounts simply don't support (a confirmed limitation on the real
portal's side, not this proxy). `http://<homeserver-lan-ip>:8000/epg.xml` works around this
by generating a standard XMLTV document from the portal's working per-channel EPG action
instead — point your app's "EPG URL" / XMLTV source setting at it. It's built in the
background (part of the same auto-sync cycle as live TV/VOD/series) covering the filtered
channel set and `EPG_HOURS` of upcoming programming per channel; `/epg.xml` always serves
whatever's currently built (possibly empty right after a fresh install, until the first
background build finishes).

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
  way as the listing cache.
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
