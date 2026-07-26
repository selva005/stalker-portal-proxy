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
`ADULT,XXX`), `LISTING_CACHE_TTL_SECONDS` (default `600`, how long to cache genre/category/
channel/VOD/series listing responses).

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

- `http://<homeserver-lan-ip>:8000/` — category filtering applied (per `BLOCKED_CATEGORY_NAMES`).
- `http://<homeserver-lan-ip>:8000/unfiltered/` — same account, same session, but never
  filters category listings. Point a specific TV/profile here if you want the full,
  unfiltered catalog on that device.

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
- Logs go to stdout (`docker logs`); control verbosity with `LOG_LEVEL`.
- Intended for LAN use across your own devices on your own account.
