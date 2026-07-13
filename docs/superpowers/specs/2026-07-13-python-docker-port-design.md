# Stalker-to-M3U: Python/Docker Port Design

## Goal

Port `worker.js` (a Cloudflare Worker) into a Python service that does the same job — authenticate to a Stalker-Portal via MAC address, generate an M3U playlist, and redirect individual channel requests to live stream URLs — deployable as a Docker container.

## Scope

Single Stalker-Portal account per container, configured entirely via environment variables. No multi-account support, no stream proxying (redirects only), matching the original script's behavior and scope.

## Architecture

FastAPI app (Uvicorn ASGI server) in a single Docker container.

```
stalker2m3u/
├── main.py            # FastAPI app, routes
├── config.py          # env var loading
├── stalker_client.py  # portal auth + API calls, token cache
├── m3u.py             # M3U playlist text builder
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

## Components

### `config.py`
Reads configuration from environment variables:
- `STALKER_HOST` (required)
- `STALKER_MAC` (required)
- `STALKER_SERIAL` (required)
- `STALKER_DEVICE_ID` (required)
- `STALKER_DEVICE_ID2` (required)
- `STALKER_STB_TYPE` (default `MAG250`)
- `STALKER_API_SIGNATURE` (default `263`)
- `LOG_LEVEL` (default `INFO`)

Fails fast at startup with a clear error if required vars are missing.

### `stalker_client.py`
Async httpx-based client, ported 1:1 from worker.js logic:
- `hw_version` / `hw_version_2` generation via MD5 (matches `hash()` in worker.js, using Python's `hashlib.md5`).
- `get_token()`, `auth(token)`, `handshake(token)`, `get_account_info(token)`, `get_genres(token)`, `get_all_channels(token)`, `create_link(channel_id, token)` — same headers (`Cookie`, `Referer`, `User-Agent`, `X-User-Agent`, `Authorization`) and query params as the original.
- Maintains an in-memory cached session: `{token, profile, account_info, obtained_at}`.
- `get_session()`: returns cached session if present and last-known-good; if a portal call using it fails (non-2xx or empty/invalid JSON), the caller invalidates the cache and `get_session()` performs a fresh handshake → auth → handshake → account_info sequence (mirrors `genToken()`), then retries once.
- No TTL-based proactive expiry — reactive re-auth on failure only, since the portal doesn't publish a token lifetime.

### `m3u.py`
`build_m3u(channels, genres, profile, account_info, origin, client_ip) -> str`, producing the same structure as `convertJsonToM3U`: header info rows (IP, user IP, portal, created, expiry, tariff plan, max connections) followed by one `#EXTINF` + URL pair per channel, grouped by genre title.

### `main.py`
FastAPI app with:
- `GET /playlist.m3u8` — obtains session (cached or fresh), fetches channels + genres, builds and returns M3U text (`media_type="application/vnd.apple.mpegurl"`).
- `GET /{stream_id}.m3u8` — obtains session, calls `create_link`, returns `RedirectResponse(url, status_code=302)`.
- Any portal/auth failure surfaces as HTTP 502 with a short error message.
- Client IP for the "User IP" info row is read from `X-Forwarded-For` (falls back to direct connection IP), since Docker deployments commonly sit behind a reverse proxy.

### Logging
Standard `logging` module writing to stdout, level controlled by `LOG_LEVEL`. Replaces `logDebug`; captured via `docker logs`.

## Error Handling

- Missing required env vars → process exits immediately with a clear message (fail fast, not a runtime 500 on first request).
- Portal unreachable / non-2xx / unparseable JSON at any step → the specific route returns HTTP 502 with a short message; cached session is invalidated so the next request re-authenticates from scratch.
- Missing/invalid stream ID in `/{stream_id}.m3u8` → HTTP 400.

## Docker

- `Dockerfile`: `python:3.12-slim` base, non-root user, installs `requirements.txt` (`fastapi`, `uvicorn[standard]`, `httpx`), `CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]`.
- `docker-compose.yml`: example service with `env_file: .env` and port mapping `8000:8000`.
- `.env.example`: lists all config vars from `config.py` with placeholder values and comments.

## Testing / Verification

Manual: build image, run with real or test-account credentials, `curl localhost:8000/playlist.m3u8` and confirm well-formed M3U output; load the playlist URL in VLC/TiviMate and confirm channel redirect works end-to-end.
