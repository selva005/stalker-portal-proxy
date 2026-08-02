# XMLTV EPG Endpoint Design

## Goal

TiviMate (configured with the "Stalker Portal" playlist type) attempts to fetch the whole
program guide via a bulk `get_epg_info` call (no channel filter, just a time period). This
account's real portal returns a completely empty response for that action — confirmed via
direct testing, not a proxy issue — so TiviMate shows no EPG and falls back to prompting
for a separate XMLTV guide URL. The per-channel action (`get_short_epg`) works correctly
with real program data. This design adds a proxy-generated XMLTV endpoint built from that
working per-channel action, so TiviMate's guide has real, portal-accurate data instead of
relying on a third-party public guide that may not match this account's exact channel list.

## Scope

- New endpoint: `GET /epg.xml`, serving a standards-compliant XMLTV document.
- Covers the **filtered** channel set only (~6,539 channels at time of writing) — matches
  what's actually watched through the main endpoint; the `/unfiltered/` variant does not
  get its own EPG endpoint in this pass (YAGNI — can be added later if needed).
- Up to **24 hours** of upcoming programming per channel.
- Generated **in the background**, not on-demand per request — a live per-request build
  would mean thousands of portal calls on every guide load in TiviMate.

## Architecture

### `epg.py` (new module)

Owns XMLTV generation, kept separate from `main.py`'s proxy/routing logic:

- `build_xmltv(channels: list[dict], programs_by_channel: dict[str, list[dict]]) -> bytes`
  — assembles a standard XMLTV `<tv>` document: one `<channel>` element per channel (using
  the channel's `xmltv_id` field as the XMLTV `id` attribute — the same identifier the
  portal itself already exposes, so TiviMate's own channel-to-guide matching works
  correctly), followed by one `<programme>` element per program entry, with `start`/`stop`
  attributes formatted per the XMLTV datetime spec (`YYYYMMDDHHMMSS +0000`, derived from
  the `get_short_epg` response's `start_timestamp`/`stop_timestamp` fields), `<title>`, and
  `<desc>` (from `name`/`descr`).
- Channels with no `xmltv_id` are skipped (nothing to associate guide data with).
- Channels whose `get_short_epg` call fails or returns no programs are included as a
  `<channel>` element with zero `<programme>` entries — not fabricated placeholder data.

### `main.py` additions

- `GET /epg.xml` — returns the cached, pre-built XMLTV document (`media_type="application/xml"`).
  If none has been generated yet (fresh install, first startup), returns a minimal empty
  `<tv></tv>` document with a 200 status rather than an error, so TiviMate doesn't treat a
  slow first crawl as a broken source.
- New background task `_build_epg()`: fetches the (already-cached or freshly-fetched)
  filtered channel list, then for each channel with an `xmltv_id`, calls `get_short_epg`
  (paced by the existing `PREWARM_DELAY_SECONDS`, same as the VOD/category crawl), collects
  results, and calls `build_xmltv(...)` to produce the final document. The result is stored
  in memory (module-level bytes) and persisted to disk (new file, e.g. `epg.xml`, alongside
  the existing `cache.json`, under `CACHE_FILE_PATH`'s directory) so a container restart
  serves the last-built guide immediately instead of an empty one.
- Runs once on startup (after the existing VOD/category pre-warm, so as not to compete for
  the same pacing budget at the same moment) and every `LISTING_CACHE_TTL_SECONDS` (6 hours)
  after that — same background-task pattern and cadence as the existing auto-sync.
- Channel list used for the crawl is filtered exactly like the main `/` endpoint (blocked
  categories excluded), reusing the existing `_blocked_category_ids`/`_filter_items` logic
  so the EPG channel set always matches what's actually visible through the proxy's
  filtered endpoint.

### Config

New `.env` var: none required — reuses existing `PREWARM_DELAY_SECONDS` (pacing) and
`LISTING_CACHE_TTL_SECONDS` (refresh interval), consistent with "one pacing knob, one
freshness knob" for all background crawls rather than adding per-feature duplicates.

## Error Handling

- If the channel-list fetch itself fails (portal down, auth failure): skip this cycle,
  log a warning, keep serving whatever XMLTV document is already cached (stale but present)
  rather than replacing it with nothing.
- Per-channel `get_short_epg` failures don't abort the whole crawl — that channel is simply
  included with no programmes, and the crawl continues to the next channel.

## Testing / Verification

1. Unit-style verification of `build_xmltv`: given a small fixed set of channels/programs,
   confirm the produced XML is well-formed, uses the correct `xmltv_id` as channel `id`,
   and formats start/stop timestamps per the XMLTV datetime spec.
2. Against the real portal: run the background crawl (potentially on a reduced channel
   subset for a faster first test), confirm `/epg.xml` returns valid XML, and confirm at
   least one real channel (e.g. the CNN channel already verified with `get_short_epg`) shows
   up with correct programme data.
3. Point TiviMate's EPG URL at `/epg.xml` and confirm the guide populates with real program
   titles/times for channels that have them.
