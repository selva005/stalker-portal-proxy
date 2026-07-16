# Stalker-Portal Transparent Proxy Design

## Goal

Replace the M3U-playlist app with a reverse proxy that lets multiple MAG/Stalker-emulator
STB apps on the home LAN share one real Stalker-Portal account, using the app's native UI
(live TV, VOD, series, EPG — whatever the account/app supports) instead of a generated
M3U file. Each TV points its STB app's "Portal URL" setting at the homeserver instead of
the real portal host; the proxy transparently relays everything through one real
authenticated session.

This fully replaces the previous project scope (`/playlist.m3u8`, `/vod.m3u8`, etc. are
removed, not added to).

## Why a proxy instead of continuing the M3U approach

- The account's real VOD/series catalog is large (36,012+ items) and paginated in small
  pages; generating a full M3U from it on every request is slow and repeatedly hits the
  portal's flood protection (confirmed during testing — see prior spec's postmortem).
- The user's actual goal is watching on 4 TVs via the native STB app they already use, not
  via M3U-based players — a proxy gives full native app functionality (VOD browsing, EPG,
  series, search, favorites) for free, since it relays the real protocol instead of
  reimplementing pieces of it.
- Real Stalker-Portal backends generally bind one token per MAC address and can invalidate
  the previous token when a new handshake occurs for that MAC. If every TV's STB app
  independently handshakes with the upstream portal, they would repeatedly invalidate each
  other's sessions. Short-circuiting auth so only the proxy itself ever handshakes upstream
  avoids this.

## Architecture

### `stalker_client.py` (kept, mostly unchanged)
Continues to own the single real authenticated session: `get_session()` /
`invalidate_session()` as today, backed by the existing `_get_token`, `_auth`,
`_handshake`, `_get_account_info` methods and MD5 hardware-version generation.

### `main.py` (replaced)

**Auth routes — answered directly from the cached session, never forwarded upstream:**
- `GET /stalker_portal/server/load.php?type=stb&action=handshake...` → build a handshake
  response using the proxy's cached token (same token for every requesting TV).
- `GET /stalker_portal/server/load.php?type=stb&action=get_profile...` → respond with the
  cached `profile` data.
- `GET /stalker_portal/server/load.php?type=account_info&action=get_main_info...` →
  respond with the cached `account_info` data.

Matching is done by inspecting `type` and `action` query params on any request to
`/stalker_portal/server/load.php`; everything else falls through to the catch-all proxy.

**Catch-all proxy route — forwards everything else verbatim:**
- `@app.api_route("/{path:path}", methods=["GET", "POST", "HEAD"])` — matches any path not
  claimed by the auth routes above (channel/VOD/series listings, EPG, images/logos,
  `create_link`, and anything else the STB app requests, including non-`load.php` paths
  like image assets under `/stalker_portal/misc/...` or `/stalker_portal/c/...`).
- Rewrites two things before forwarding to the real host: the `Cookie` header's `mac=`
  value → the real `STALKER_MAC`, and the `Authorization` header → `Bearer <cached token>`.
  All other headers, query params, and the request body (for POST) pass through unchanged.
- Relays the upstream response back to the client unchanged: same status code, same
  `Content-Type`, same body bytes. This is a byte-passthrough proxy, not a JSON-aware one —
  it must work for HTML, images, and binary stream-related responses, not just the JSON
  API calls we've dealt with so far.

### Error handling

- If the catch-all proxy's upstream call fails outright (connection error, timeout): retry
  once after invalidating and re-fetching the session (in case the failure was an auth
  issue), then relay whatever the retry returns.
- If the upstream responds with a non-2xx status: relay that response as-is (status +
  body) rather than replacing it with a generic error — the STB app needs to see the
  portal's real error responses to behave correctly (e.g. show its own "channel
  unavailable" UI), not a generic proxy error page.
- Only truly unreachable-upstream cases (both the original attempt and the retry fail to
  connect) return a proxy-level 502 with a short message.

### Removed

- `m3u.py`, the `/playlist.m3u8`, `/vod.m3u8`, `/{stream_id}.m3u8` routes, and all VOD/
  series-specific code from the prior spec — none of this is needed once the app is a
  transparent proxy.

### Config

Unchanged — `config.py` keeps `STALKER_HOST`, `STALKER_MAC`, `STALKER_SERIAL`,
`STALKER_DEVICE_ID`(optional), `STALKER_DEVICE_ID2`(optional), `STALKER_STB_TYPE`,
`STALKER_API_SIGNATURE`, `LOG_LEVEL`.

### Deployment

Unchanged Docker/Compose setup. Each TV's STB emulator app is configured with Portal URL
`http://<homeserver-lan-ip>:8000/` in place of the real portal host. No changes needed to
each TV's MAC/device ID settings — those become irrelevant since auth is short-circuited
by the proxy regardless of what any given TV sends.

## Testing / Verification

1. Point one TV's STB app at the homeserver's proxy address and confirm the full native
   app UI works — channel list loads, playback works, VOD/series browsing works if the
   account has those — exactly as it does pointed directly at the real portal.
2. Repeat with a second device connected concurrently, confirming neither TV's session gets
   invalidated by the other's activity (the specific failure mode this design avoids).
3. Confirm non-JSON passthrough works: an image/logo request and an actual video stream
   request both come through correctly (not just the JSON API calls).
