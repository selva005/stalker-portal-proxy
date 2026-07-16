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
`LOG_LEVEL` (default `INFO`).

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

## Run locally without Docker

```bash
pip install -r requirements.txt
export $(cat .env | xargs)
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Notes

- The portal session (token/profile/account info) is cached in memory and only
  re-authenticated when a portal call fails, avoiding repeated handshakes upstream.
- Logs go to stdout (`docker logs`); control verbosity with `LOG_LEVEL`.
- Intended for LAN use across your own devices on your own account.
