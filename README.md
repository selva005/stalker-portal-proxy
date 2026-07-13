# Stalker-to-M3U

Converts a Stalker-Portal account (MAC-based IPTV portal) into an M3U playlist usable in
TiviMate, OTT Navigator, VLC, Perfect Player, etc. FastAPI app deployable via Docker.

## Features

- Authenticates to any Stalker-Portal using MAC address, serial number, and device IDs
- Generates a dynamic M3U playlist directly from the portal (channels, genres, account info)
- Redirects to direct stream URLs for any channel
- Auto-detects and displays the requesting client's IP in the playlist info rows

## Configuration

Copy `.env.example` to `.env` and fill in your portal details:

```bash
cp .env.example .env
```

Required: `STALKER_HOST`, `STALKER_MAC`, `STALKER_SERIAL`.
Optional: `STALKER_DEVICE_ID`, `STALKER_DEVICE_ID2` (some portals don't require them),
`STALKER_STB_TYPE` (default `MAG250`), `STALKER_API_SIGNATURE` (default `263`),
`LOG_LEVEL` (default `INFO`).

## Run with Docker Compose

```bash
docker compose up --build
```

Then load the playlist in your IPTV player at:

```
http://<host>:8000/playlist.m3u8
```

## Run locally without Docker

```bash
pip install -r requirements.txt
export $(cat .env | xargs)
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Endpoints

- `GET /playlist.m3u8` — full M3U playlist (channels + account info rows)
- `GET /{channel_id}.m3u8` — 302-redirects to the live stream URL for that channel

## Notes

- The portal session (token/profile/account info) is cached in memory and only
  re-authenticated when a portal call fails, avoiding a full re-auth on every request.
- Logs go to stdout (`docker logs`); control verbosity with `LOG_LEVEL`.
