"""Transparent reverse proxy for a Stalker-Portal account.

Auth-related actions are answered directly from one cached, real session so every
downstream STB app (regardless of its own MAC/device settings) shares the same
upstream identity. Everything else is forwarded to the real portal verbatim, with
the real account's credentials injected.
"""
import asyncio
import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from config import config
from epg import EMPTY_XMLTV, build_xmltv
from stalker_client import PortalError, StalkerClient, request_with_redirects

logging.basicConfig(
    level=config.log_level,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("main")

app = FastAPI(title="Stalker-Portal Proxy")
client = StalkerClient(config)
# Separate HTTP transport for the background crawl (VOD/category pre-warm + EPG), so a
# hung/slow crawl request can never compete with real-time traffic for a connection out of
# the same pool. Auth/session state is still fully shared via `client` -- see
# `_get_valid_session`'s `role` param for why the crawl doesn't get its own login.
crawl_http = httpx.AsyncClient(timeout=15.0)


def _upstream_base() -> str:
    # config.scheme/config.host can change at runtime -- see request_with_redirects()
    # in stalker_client.py -- so this must be recomputed on every call, not cached.
    return f"{config.scheme}://{config.host}"

# Headers that must not be blindly relayed from the upstream response, since the
# ASGI server recalculates transport-level framing itself.
_EXCLUDED_RESPONSE_HEADERS = {"content-encoding", "content-length", "transfer-encoding", "connection"}
_EXCLUDED_REQUEST_HEADERS = {
    "host", "content-length", "transfer-encoding", "connection", "authorization", "cookie",
}

# Actions that return a category/genre listing (live TV genres, VOD categories, series
# categories all share this response shape: {"js": [{"id", "title", ...}, ...]}).
_CATEGORY_LISTING_ACTIONS = {"get_genres", "get_categories"}

# Actions that return actual channel/item entries, each tagged with the category/genre
# they belong to. Blocking a category from the listing above does NOT stop these from
# still containing items in that category -- both need filtering for a category to
# actually disappear from what the app can see, not just lose its display name.
_ITEM_LISTING_FIELDS = {
    ("itv", "get_all_channels"): "tv_genre_id",
    # Live channels listed per genre carry tv_genre_id, unlike VOD/series items' category_id.
    ("itv", "get_ordered_list"): "tv_genre_id",
    ("vod", "get_ordered_list"): "category_id",
    ("series", "get_ordered_list"): "category_id",
}

# Populated from whatever category/genre listing content has been seen (fresh or cached),
# keyed by portal type ("itv", "vod", "series") -> set of blocked category ids.
_blocked_category_ids: dict[str, set] = {}

# Read-heavy, slow-changing listing actions worth caching to cut down on repeated
# portal hits from multiple TVs (or repeated app refreshes) requesting the same data.
# Stream links (create_link) and EPG are intentionally excluded, since they're either
# time-sensitive or already lightweight per-item lookups.
_CACHEABLE_ACTIONS = {"get_genres", "get_categories", "get_all_channels", "get_ordered_list"}


@dataclass
class _CacheEntry:
    status_code: int
    content_type: str
    content: bytes
    expires_at: float


_listing_cache: dict[str, _CacheEntry] = {}


def _cache_key(upstream_path: str, items) -> str:
    return f"{upstream_path}?{urlencode(sorted(items))}"


def _save_cache_to_disk() -> None:
    """Persist the in-memory cache so a container restart doesn't start cold."""
    try:
        cache_dir = os.path.dirname(config.cache_file_path)
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
        serializable = {
            key: {
                "status_code": entry.status_code,
                "content_type": entry.content_type,
                "content_b64": base64.b64encode(entry.content).decode("ascii"),
                "expires_at": entry.expires_at,
            }
            for key, entry in _listing_cache.items()
        }
        tmp_path = config.cache_file_path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(serializable, f)
        os.replace(tmp_path, config.cache_file_path)
        logger.info("Persisted %d cache entries to %s", len(serializable), config.cache_file_path)
    except OSError as e:
        logger.warning("Failed to persist cache to disk: %s", e)


def _load_cache_from_disk() -> None:
    try:
        with open(config.cache_file_path) as f:
            serializable = json.load(f)
    except (OSError, ValueError):
        return
    loaded = 0
    for key, entry in serializable.items():
        try:
            content = base64.b64decode(entry["content_b64"])
        except (KeyError, ValueError):
            continue
        _listing_cache[key] = _CacheEntry(
            status_code=entry["status_code"],
            content_type=entry["content_type"],
            content=content,
            expires_at=entry["expires_at"],
        )
        loaded += 1
    logger.info("Loaded %d cache entries from %s", loaded, config.cache_file_path)


def _compile_whole_word_patterns(names: list[str]) -> list[re.Pattern]:
    """Compile each name to a case-insensitive whole-word pattern.

    Letters/digits on either side of the name block a match ("IN" matches "IN TAMIL" and
    "TAMIL|IN" but not "INDIA"). The boundary is only enforced on an edge that is itself a
    letter/digit, so a name ending in punctuation like "US|" still matches "US|NEWS".
    """
    patterns = []
    for name in names:
        left = r"(?<![^\W_])" if name[0].isalnum() else ""
        right = r"(?![^\W_])" if name[-1].isalnum() else ""
        patterns.append(re.compile(f"{left}{re.escape(name)}{right}", re.IGNORECASE))
    return patterns


_BLOCKED_PATTERNS = _compile_whole_word_patterns(config.blocked_category_names)
_ALLOWED_PATTERNS = _compile_whole_word_patterns(config.allowed_category_names)
_FILTERING_ENABLED = bool(_BLOCKED_PATTERNS or _ALLOWED_PATTERNS)
_HD_NAME_PATTERNS = _compile_whole_word_patterns(["hd", "fhd", "uhd", "4k"])


def _is_blocked_category(title: str) -> bool:
    """True if the category should be hidden: it matches the blocklist, or an allowlist
    is configured and it doesn't match it (blocklist wins if a title matches both)."""
    if any(p.search(title) for p in _BLOCKED_PATTERNS):
        return True
    return bool(_ALLOWED_PATTERNS) and not any(p.search(title) for p in _ALLOWED_PATTERNS)


def _update_blocked_category_ids(req_type: str, content: bytes) -> None:
    """Record which category ids are blocked, so item listings can filter by id too.

    Called for every category/genre listing seen (cache hit or fresh fetch), regardless
    of whether the current request is filtered or not, so the mapping is always kept
    current from whichever source populated it first.
    """
    if not _FILTERING_ENABLED:
        return
    try:
        data = json.loads(content)
    except ValueError:
        return
    categories = data.get("js")
    if not isinstance(categories, list):
        return
    _blocked_category_ids[req_type] = {
        str(c["id"]) for c in categories
        if c.get("id") is not None and _is_blocked_category(c.get("title", ""))
    }


def _filter_categories(body: bytes) -> bytes:
    try:
        data = json.loads(body)
    except ValueError:
        return body
    categories = data.get("js")
    if not isinstance(categories, list):
        return body
    data["js"] = [c for c in categories if not _is_blocked_category(c.get("title", ""))]
    return json.dumps(data).encode()


def _filter_items(content: bytes, req_type: str, id_field: str) -> bytes:
    blocked_ids = _blocked_category_ids.get(req_type)
    if not blocked_ids:
        return content
    try:
        data = json.loads(content)
    except ValueError:
        return content
    js = data.get("js")
    items = js.get("data") if isinstance(js, dict) else None
    if not isinstance(items, list):
        return content
    js["data"] = [item for item in items if str(item.get(id_field)) not in blocked_ids]
    return json.dumps(data).encode()


def _mark_hd_channels(content: bytes) -> bytes:
    """Set `hd` on channels whose name carries an HD/FHD/UHD/4K tag.

    This portal reports hd=0 for every channel, even ones named "... HD", so apps never
    show an HD badge. Only ever turns the flag on: an untagged name proves nothing.
    """
    try:
        data = json.loads(content)
    except ValueError:
        return content
    js = data.get("js")
    items = js.get("data") if isinstance(js, dict) else None
    if not isinstance(items, list):
        return content
    changed = False
    for item in items:
        if str(item.get("hd")) != "1" and any(p.search(item.get("name") or "") for p in _HD_NAME_PATTERNS):
            item["hd"] = "1"
            changed = True
    return json.dumps(data).encode() if changed else content


async def _get_valid_session(role: str = "realtime", max_age: float = None):
    try:
        session = await client.get_session(role=role)
    except PortalError:
        client.invalidate_session()
        return await client.get_session(force_refresh=True, role=role)

    if max_age is not None and (time.time() - session.obtained_at) > max_age:
        # The cached session is old enough that we no longer trust it's still accepted
        # upstream without checking (e.g. it may have been silently invalidated by another
        # device authenticating with the same MAC). Refresh proactively rather than only
        # reacting after a real request gets a 401/403.
        try:
            return await client.get_session(force_refresh=True, role=role)
        except PortalError as e:
            logger.warning("Proactive session refresh failed (%s), using stale session", e)
            return session

    return session


# Requests under this prefix skip category filtering entirely, e.g. an STB app
# pointed at http://host:8000/unfiltered/ gets the raw, unfiltered catalog.
UNFILTERED_PREFIX = "/unfiltered"


@app.api_route("/{path:path}", methods=["GET", "POST", "HEAD"])
async def catch_all(request: Request, path: str):
    """Single entry point for every request the STB app might send.

    Different STB emulator apps disagree on whether the configured Portal URL
    already includes the `/stalker_portal` prefix, so routing here is done by
    query params (`type`/`action`), not by matching a literal path.
    """
    if request.url.path in ("/epg.xml", "/epg.xml/"):
        return Response(content=_epg_document, media_type="application/xml")

    params = request.query_params
    req_type = params.get("type", "")
    action = params.get("action", "")

    try:
        if req_type == "stb" and action == "handshake":
            # This is the entry point of a TV app's session lifecycle -- check staleness
            # here (and on get_profile/account_info below) rather than on a background
            # timer, so a silently-invalidated session (e.g. another device authenticating
            # with the same MAC) gets caught right when an app actually starts using it.
            session = await _get_valid_session(max_age=config.session_max_age_seconds)
            return JSONResponse({"js": {"token": session.token, "random": ""}})

        if req_type == "stb" and action == "get_profile":
            session = await _get_valid_session(max_age=config.session_max_age_seconds)
            return JSONResponse({"js": session.profile})

        if req_type == "account_info" and action == "get_main_info":
            session = await _get_valid_session(max_age=config.session_max_age_seconds)
            return JSONResponse({"js": session.account_info})

        if req_type == "itv" and action == "get_epg_info":
            # This account's real portal returns nothing at all for this bulk EPG action
            # (confirmed by direct testing), so answer it ourselves from the same
            # per-channel program data already crawled for /epg.xml.
            ch_id = params.get("ch_id")
            if ch_id:
                return JSONResponse({"js": {ch_id: _epg_programs_by_channel.get(ch_id, [])}})
            return JSONResponse({"js": _epg_programs_by_channel})

        filtered = True
        effective_path = request.url.path
        if effective_path == UNFILTERED_PREFIX or effective_path.startswith(UNFILTERED_PREFIX + "/"):
            filtered = False
            effective_path = effective_path[len(UNFILTERED_PREFIX):] or "/"

        return await _proxy(request, effective_path, filtered)
    except PortalError as e:
        logger.warning("Portal unavailable: %s", e)
        return Response(content=f"Portal unavailable: {e}", status_code=502)


def _upstream_path(request_path: str) -> str:
    """Normalize the request path to the real portal's actual base path.

    Some STB apps are configured with a bare Portal URL and don't prepend
    `/stalker_portal` themselves when building requests.
    """
    if request_path.startswith("/stalker_portal"):
        return request_path
    return f"/stalker_portal{request_path}"


def _apply_filter_if_needed(content: bytes, req_type: str, action: str, filtered: bool) -> bytes:
    if action in _CATEGORY_LISTING_ACTIONS:
        _update_blocked_category_ids(req_type, content)
        if filtered and _FILTERING_ENABLED:
            return _filter_categories(content)
        return content
    id_field = _ITEM_LISTING_FIELDS.get((req_type, action))
    if id_field:
        if filtered:
            content = _filter_items(content, req_type, id_field)
        if req_type == "itv":
            content = _mark_hd_channels(content)
    return content


# Live TV is small enough (genres + one get_all_channels call) to fully auto-sync in the
# background. VOD/series catalogs are large (one category alone had 36,000+ items at
# ~14/page), so only their categories plus the first PREWARM_VOD_SERIES_PAGES pages of
# each category are pre-warmed -- full per-category crawling would take a long time and
# risks re-triggering the portal's rate limiting. Everything beyond that stays reactively
# cached (populated as the TV app actually requests it).
_LIVE_TV_PREWARM_ENDPOINTS = [
    ("itv", "get_genres"),
    ("itv", "get_all_channels"),
]
_CATALOG_PREWARM_ENDPOINTS = [
    ("vod", "get_categories"),
    ("series", "get_categories"),
]


def _parse_category_ids(content: bytes) -> list[str]:
    try:
        data = json.loads(content)
    except ValueError:
        return []
    categories = data.get("js")
    if not isinstance(categories, list):
        return []
    return [str(c["id"]) for c in categories if c.get("id") is not None]


async def _handle_rate_limit(response: httpx.Response, consecutive: int) -> tuple[bool, int]:
    """If `response` is a 429, back off before the caller's next request.

    Returns (should_abort_this_crawl_cycle, updated_consecutive_429_count). Backing off here
    (rather than silently skipping, as before) directly reduces pressure on the shared
    upstream account during a long-running crawl -- sustained rate-limiting during a crawl
    was the leading theory for why real-time STB auth requests started failing once the EPG
    crawl was introduced (the crawl's own auth attempts share a cooldown with real traffic;
    see `role` on `_get_valid_session`/`StalkerClient.get_session`).
    """
    if response.status_code != 429:
        return False, 0
    backoff = config.prewarm_rate_limit_backoff_seconds
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            backoff = max(backoff, float(retry_after))
        except ValueError:
            pass
    consecutive += 1
    logger.warning(
        "Rate limited (429), backing off %.1fs (consecutive=%d/%d)",
        backoff, consecutive, config.prewarm_max_consecutive_rate_limits,
    )
    await asyncio.sleep(backoff)
    return consecutive >= config.prewarm_max_consecutive_rate_limits, consecutive


async def _fetch_and_cache(
    headers: dict, params: list[tuple[str, str]], http: httpx.AsyncClient = None
) -> bytes | None:
    http = http or client.http
    url = f"{_upstream_base()}/stalker_portal/server/load.php"
    try:
        response = await request_with_redirects(http, "GET", url, config, params=params, headers=headers)
    except httpx.HTTPError as e:
        logger.warning("Pre-warm request failed for %s: %s", params, e)
        return None
    await _handle_rate_limit(response, 0)
    if response.status_code != 200:
        logger.warning("Pre-warm got status %s for %s", response.status_code, params)
        return None
    key = _cache_key("/stalker_portal/server/load.php", params)
    _listing_cache[key] = _CacheEntry(
        status_code=response.status_code,
        content_type=response.headers.get("content-type"),
        content=response.content,
        expires_at=time.time() + config.listing_cache_ttl_seconds,
    )
    return response.content


async def _prewarm_all() -> None:
    try:
        session = await _get_valid_session(role="crawl")
    except PortalError as e:
        logger.warning("Skipping pre-warm: %s", e)
        return

    headers = {
        "Cookie": f"mac={config.mac_address}; stb_lang=en; timezone=GMT",
        "Authorization": f"Bearer {session.token}",
    }

    for req_type, action in _LIVE_TV_PREWARM_ENDPOINTS:
        content = await _fetch_and_cache(
            headers, [("type", req_type), ("action", action), ("JsHttpRequest", "1-xml")], http=crawl_http
        )
        if content and action == "get_genres":
            _update_blocked_category_ids(req_type, content)
        logger.info("Pre-warmed live TV: type=%s action=%s", req_type, action)
        await asyncio.sleep(config.prewarm_delay_seconds)

    for req_type, action in _CATALOG_PREWARM_ENDPOINTS:
        content = await _fetch_and_cache(
            headers, [("type", req_type), ("action", action), ("JsHttpRequest", "1-xml")], http=crawl_http
        )
        logger.info("Pre-warmed categories: type=%s action=%s", req_type, action)
        await asyncio.sleep(config.prewarm_delay_seconds)

        if not content:
            continue
        _update_blocked_category_ids(req_type, content)
        category_ids = _parse_category_ids(content)
        for category_id in category_ids:
            for page in range(1, config.prewarm_vod_series_pages + 1):
                await _fetch_and_cache(
                    headers,
                    [
                        ("type", req_type),
                        ("action", "get_ordered_list"),
                        ("category", category_id),
                        ("p", str(page)),
                        ("JsHttpRequest", "1-xml"),
                    ],
                    http=crawl_http,
                )
                await asyncio.sleep(config.prewarm_delay_seconds)
        logger.info(
            "Pre-warmed first %d page(s) of %d %s categories",
            config.prewarm_vod_series_pages, len(category_ids), req_type,
        )

    await asyncio.to_thread(_save_cache_to_disk)


_epg_document: bytes = EMPTY_XMLTV

# Per-channel program lists from the last EPG crawl (channel id -> get_short_epg entries),
# kept around so live get_epg_info requests can be answered directly instead of relying on
# the real portal's bulk EPG action, which returns nothing for this account.
_epg_programs_by_channel: dict[str, list[dict]] = {}

_EPG_PROGRAMS_FILE_SUFFIX = ".programs.json"


def _epg_programs_file_path() -> str:
    return config.epg_file_path + _EPG_PROGRAMS_FILE_SUFFIX


def _save_epg_to_disk() -> None:
    try:
        epg_dir = os.path.dirname(config.epg_file_path)
        if epg_dir:
            os.makedirs(epg_dir, exist_ok=True)
        tmp_path = config.epg_file_path + ".tmp"
        with open(tmp_path, "wb") as f:
            f.write(_epg_document)
        os.replace(tmp_path, config.epg_file_path)

        programs_tmp_path = _epg_programs_file_path() + ".tmp"
        with open(programs_tmp_path, "w") as f:
            json.dump(_epg_programs_by_channel, f)
        os.replace(programs_tmp_path, _epg_programs_file_path())

        logger.info("Persisted EPG document and program data to %s", config.epg_file_path)
    except OSError as e:
        logger.warning("Failed to persist EPG to disk: %s", e)


def _load_epg_from_disk() -> None:
    global _epg_document, _epg_programs_by_channel
    try:
        with open(config.epg_file_path, "rb") as f:
            _epg_document = f.read()
        logger.info("Loaded EPG document from %s", config.epg_file_path)
    except OSError:
        pass
    try:
        with open(_epg_programs_file_path()) as f:
            _epg_programs_by_channel = json.load(f)
        logger.info("Loaded EPG program data for %d channels from disk", len(_epg_programs_by_channel))
    except (OSError, ValueError):
        pass


_EPG_META_FILE_SUFFIX = ".meta.json"


def _epg_meta_file_path() -> str:
    return config.epg_file_path + _EPG_META_FILE_SUFFIX


def _save_epg_meta_to_disk(completed_at: float, channel_count: int) -> None:
    """Record when the last FULLY-COMPLETED crawl finished (not partial-publish points).

    A partial publish (see `_publish_epg`) replaces `_epg_programs_by_channel` wholesale
    rather than merging with a prior complete crawl's data for channels not yet reached in
    the current pass. If a crawl is interrupted shortly after a partial publish, using that
    write's timestamp for freshness would make an incomplete guide look "fresh" and skip a
    real re-crawl -- so this is only written at true completion.
    """
    try:
        meta_dir = os.path.dirname(_epg_meta_file_path())
        if meta_dir:
            os.makedirs(meta_dir, exist_ok=True)
        tmp_path = _epg_meta_file_path() + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump({"completed_at": completed_at, "channel_count": channel_count}, f)
        os.replace(tmp_path, _epg_meta_file_path())
    except OSError as e:
        logger.warning("Failed to persist EPG metadata to disk: %s", e)


def _load_epg_meta_from_disk() -> dict | None:
    try:
        with open(_epg_meta_file_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _epg_is_fresh() -> bool:
    if not _epg_programs_by_channel:
        return False
    meta = _load_epg_meta_from_disk()
    if not meta or not isinstance(meta.get("completed_at"), (int, float)):
        return False
    age = time.time() - meta["completed_at"]
    return age < config.epg_freshness_ttl_seconds


async def _publish_epg(channels: list[dict], programs_by_channel: dict[str, list[dict]]) -> None:
    """Rebuild the XMLTV doc from `programs_by_channel` and persist everything to disk."""
    global _epg_document, _epg_programs_by_channel
    _epg_programs_by_channel = dict(programs_by_channel)
    _epg_document = await asyncio.to_thread(build_xmltv, channels, programs_by_channel)
    await asyncio.to_thread(_save_epg_to_disk)


async def _build_epg() -> None:
    """Crawl get_short_epg for every filtered channel and rebuild the XMLTV document.

    Runs after the VOD/category pre-warm so the two background crawls don't compete for
    the same pacing budget at the same moment.
    """
    try:
        session = await _get_valid_session(role="crawl")
    except PortalError as e:
        logger.warning("Skipping EPG build: %s", e)
        return

    headers = {
        "Cookie": f"mac={config.mac_address}; stb_lang=en; timezone=GMT",
        "Authorization": f"Bearer {session.token}",
    }

    content = await _fetch_and_cache(
        headers, [("type", "itv"), ("action", "get_all_channels"), ("JsHttpRequest", "1-xml")], http=crawl_http
    )
    if not content:
        logger.warning("Skipping EPG build: could not fetch channel list")
        return

    filtered_content = _filter_items(content, "itv", "tv_genre_id")
    try:
        channels = json.loads(filtered_content).get("js", {}).get("data", [])
    except ValueError:
        logger.warning("Skipping EPG build: channel list was not valid JSON")
        return

    programs_by_channel: dict[str, list[dict]] = {}
    fetched_count = 0
    consecutive_rate_limits = 0
    for channel in channels:
        channel_id = channel.get("id")
        if not channel_id:
            continue
        # Note: xmltv_id is NOT required here -- get_epg_info answers are keyed by the
        # channel's numeric id, not xmltv_id. Only the /epg.xml XMLTV document needs
        # xmltv_id, and build_xmltv() already skips channels without one on its own.
        url = f"{_upstream_base()}/stalker_portal/server/load.php"
        params = [
            ("type", "itv"),
            ("action", "get_short_epg"),
            ("ch_id", str(channel_id)),
            ("size", str(config.epg_hours)),
            ("JsHttpRequest", "1-xml"),
        ]
        try:
            response = await request_with_redirects(
                crawl_http, "GET", url, config, params=params, headers=headers
            )
            should_abort, consecutive_rate_limits = await _handle_rate_limit(
                response, consecutive_rate_limits
            )
            if should_abort:
                logger.warning(
                    "Aborting EPG crawl after %d consecutive rate-limit responses "
                    "(%d/%d channels fetched)",
                    consecutive_rate_limits, fetched_count, len(channels),
                )
                await _publish_epg(channels, programs_by_channel)
                return
            if response.status_code == 200:
                programs_by_channel[str(channel_id)] = response.json().get("js", [])
            elif response.status_code != 429:
                logger.warning(
                    "EPG fetch got status %s for channel %s", response.status_code, channel_id
                )
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("EPG fetch failed for channel %s: %s", channel_id, e)

        fetched_count += 1
        if fetched_count % config.epg_publish_every_n_channels == 0:
            await _publish_epg(channels, programs_by_channel)
            logger.info(
                "EPG progress: published data for %d/%d channels so far",
                fetched_count, len(channels),
            )

        await asyncio.sleep(config.prewarm_delay_seconds)

    await _publish_epg(channels, programs_by_channel)
    await asyncio.to_thread(_save_epg_meta_to_disk, time.time(), len(channels))
    logger.info("Built EPG document for %d channels", len(channels))


@app.on_event("startup")
async def startup() -> None:
    _load_cache_from_disk()
    _load_epg_from_disk()  # must run before _epg_is_fresh() is checked below

    async def _run_background_sync(skip_epg_if_fresh: bool = False):
        await _prewarm_all()
        if skip_epg_if_fresh and _epg_is_fresh():
            logger.info("Skipping startup EPG crawl: loaded data is within the freshness threshold")
            return
        await _build_epg()

    asyncio.create_task(_run_background_sync(skip_epg_if_fresh=True))

    async def _background_sync_loop():
        while True:
            await asyncio.sleep(config.listing_cache_ttl_seconds)
            await _run_background_sync()  # periodic loop always rebuilds, freshness-skip is startup-only

    asyncio.create_task(_background_sync_loop())


async def _proxy(request: Request, effective_path: str, filtered: bool) -> Response:
    upstream_path = _upstream_path(effective_path)
    req_type = request.query_params.get("type", "")
    action = request.query_params.get("action", "")
    cacheable = request.method == "GET" and action in _CACHEABLE_ACTIONS
    cache_key = _cache_key(upstream_path, request.query_params.multi_items()) if cacheable else None

    if cache_key is not None:
        cached = _listing_cache.get(cache_key)
        if cached is not None and cached.expires_at > time.time():
            content = _apply_filter_if_needed(cached.content, req_type, action, filtered)
            return Response(content=content, status_code=cached.status_code, media_type=cached.content_type)

    session = await _get_valid_session()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _EXCLUDED_REQUEST_HEADERS}
    headers["Cookie"] = f"mac={config.mac_address}; stb_lang=en; timezone=GMT"
    headers["Authorization"] = f"Bearer {session.token}"
    body = await request.body()

    async def do_request() -> httpx.Response:
        url = f"{_upstream_base()}{upstream_path}"
        return await request_with_redirects(
            client.http,
            request.method,
            url,
            config,
            params=request.query_params,
            headers=headers,
            content=body or None,
        )

    try:
        response = await do_request()
    except httpx.HTTPError as e:
        logger.warning("Upstream request failed (%s), retrying with a fresh session", e)
        client.invalidate_session()
        session = await _get_valid_session()
        headers["Authorization"] = f"Bearer {session.token}"
        try:
            response = await do_request()
        except httpx.HTTPError as e2:
            return Response(content=f"Upstream unreachable: {e2}", status_code=502)

    if response.status_code in (401, 403):
        # Retry this same request once with a fresh session, rather than only invalidating
        # for next time -- self-heals within this request instead of forcing the client to
        # see the failure and issue a second request on its own to recover.
        logger.warning(
            "Upstream returned %s, retrying once with a fresh session", response.status_code
        )
        client.invalidate_session()
        session = await _get_valid_session()
        headers["Authorization"] = f"Bearer {session.token}"
        try:
            response = await do_request()
        except httpx.HTTPError as e:
            return Response(content=f"Upstream unreachable: {e}", status_code=502)

    if response.status_code >= 500:
        client.invalidate_session()

    if cache_key is not None and response.status_code == 200:
        _listing_cache[cache_key] = _CacheEntry(
            status_code=response.status_code,
            content_type=response.headers.get("content-type"),
            content=response.content,
            expires_at=time.time() + config.listing_cache_ttl_seconds,
        )

    content = _apply_filter_if_needed(response.content, req_type, action, filtered)

    response_headers = {
        k: v for k, v in response.headers.items() if k.lower() not in _EXCLUDED_RESPONSE_HEADERS
    }
    return Response(
        content=content,
        status_code=response.status_code,
        headers=response_headers,
        media_type=response.headers.get("content-type"),
    )


@app.on_event("shutdown")
async def shutdown():
    await asyncio.to_thread(_save_cache_to_disk)
    await client.close()
    await crawl_http.aclose()
