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
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from config import config
from epg import EMPTY_XMLTV, build_xmltv
from stalker_client import PortalError, StalkerClient

logging.basicConfig(
    level=config.log_level,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("main")

app = FastAPI(title="Stalker-Portal Proxy")
client = StalkerClient(config)

UPSTREAM_BASE = f"http://{config.host}"

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
    "get_all_channels": "tv_genre_id",
    "get_ordered_list": "category_id",
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


def _is_blocked_category(title: str) -> bool:
    title_lower = title.lower()
    return any(blocked in title_lower for blocked in config.blocked_category_names)


def _update_blocked_category_ids(req_type: str, content: bytes) -> None:
    """Record which category ids are blocked, so item listings can filter by id too.

    Called for every category/genre listing seen (cache hit or fresh fetch), regardless
    of whether the current request is filtered or not, so the mapping is always kept
    current from whichever source populated it first.
    """
    if not config.blocked_category_names:
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


async def _get_valid_session():
    try:
        return await client.get_session()
    except PortalError:
        client.invalidate_session()
        return await client.get_session(force_refresh=True)


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
            session = await _get_valid_session()
            return JSONResponse({"js": {"token": session.token, "random": ""}})

        if req_type == "stb" and action == "get_profile":
            session = await _get_valid_session()
            return JSONResponse({"js": session.profile})

        if req_type == "account_info" and action == "get_main_info":
            session = await _get_valid_session()
            return JSONResponse({"js": session.account_info})

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
        if filtered and config.blocked_category_names:
            return _filter_categories(content)
        return content
    if filtered and action in _ITEM_LISTING_FIELDS:
        return _filter_items(content, req_type, _ITEM_LISTING_FIELDS[action])
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


async def _fetch_and_cache(headers: dict, params: list[tuple[str, str]]) -> bytes | None:
    url = f"{UPSTREAM_BASE}/stalker_portal/server/load.php"
    try:
        response = await client.http.get(url, params=params, headers=headers)
    except httpx.HTTPError as e:
        logger.warning("Pre-warm request failed for %s: %s", params, e)
        return None
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
        session = await _get_valid_session()
    except PortalError as e:
        logger.warning("Skipping pre-warm: %s", e)
        return

    headers = {
        "Cookie": f"mac={config.mac_address}; stb_lang=en; timezone=GMT",
        "Authorization": f"Bearer {session.token}",
    }

    for req_type, action in _LIVE_TV_PREWARM_ENDPOINTS:
        content = await _fetch_and_cache(
            headers, [("type", req_type), ("action", action), ("JsHttpRequest", "1-xml")]
        )
        if content and action == "get_genres":
            _update_blocked_category_ids(req_type, content)
        logger.info("Pre-warmed live TV: type=%s action=%s", req_type, action)
        await asyncio.sleep(config.prewarm_delay_seconds)

    for req_type, action in _CATALOG_PREWARM_ENDPOINTS:
        content = await _fetch_and_cache(
            headers, [("type", req_type), ("action", action), ("JsHttpRequest", "1-xml")]
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
                )
                await asyncio.sleep(config.prewarm_delay_seconds)
        logger.info(
            "Pre-warmed first %d page(s) of %d %s categories",
            config.prewarm_vod_series_pages, len(category_ids), req_type,
        )

    _save_cache_to_disk()


_epg_document: bytes = EMPTY_XMLTV


def _save_epg_to_disk() -> None:
    try:
        epg_dir = os.path.dirname(config.epg_file_path)
        if epg_dir:
            os.makedirs(epg_dir, exist_ok=True)
        tmp_path = config.epg_file_path + ".tmp"
        with open(tmp_path, "wb") as f:
            f.write(_epg_document)
        os.replace(tmp_path, config.epg_file_path)
        logger.info("Persisted EPG document to %s", config.epg_file_path)
    except OSError as e:
        logger.warning("Failed to persist EPG to disk: %s", e)


def _load_epg_from_disk() -> None:
    global _epg_document
    try:
        with open(config.epg_file_path, "rb") as f:
            _epg_document = f.read()
        logger.info("Loaded EPG document from %s", config.epg_file_path)
    except OSError:
        pass


async def _build_epg() -> None:
    """Crawl get_short_epg for every filtered channel and rebuild the XMLTV document.

    Runs after the VOD/category pre-warm so the two background crawls don't compete for
    the same pacing budget at the same moment.
    """
    global _epg_document

    try:
        session = await _get_valid_session()
    except PortalError as e:
        logger.warning("Skipping EPG build: %s", e)
        return

    headers = {
        "Cookie": f"mac={config.mac_address}; stb_lang=en; timezone=GMT",
        "Authorization": f"Bearer {session.token}",
    }

    content = await _fetch_and_cache(
        headers, [("type", "itv"), ("action", "get_all_channels"), ("JsHttpRequest", "1-xml")]
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
    for channel in channels:
        xmltv_id = channel.get("xmltv_id")
        channel_id = channel.get("id")
        if not xmltv_id or not channel_id:
            continue
        url = f"{UPSTREAM_BASE}/stalker_portal/server/load.php"
        params = [
            ("type", "itv"),
            ("action", "get_short_epg"),
            ("ch_id", str(channel_id)),
            ("size", str(config.epg_hours)),
            ("JsHttpRequest", "1-xml"),
        ]
        try:
            response = await client.http.get(url, params=params, headers=headers)
            if response.status_code == 200:
                programs_by_channel[str(channel_id)] = response.json().get("js", [])
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("EPG fetch failed for channel %s: %s", channel_id, e)
        await asyncio.sleep(config.prewarm_delay_seconds)

    _epg_document = build_xmltv(channels, programs_by_channel)
    _save_epg_to_disk()
    logger.info("Built EPG document for %d channels", len(channels))


@app.on_event("startup")
async def startup() -> None:
    _load_cache_from_disk()
    _load_epg_from_disk()

    async def _run_background_sync():
        await _prewarm_all()
        await _build_epg()

    asyncio.create_task(_run_background_sync())

    async def _background_sync_loop():
        while True:
            await asyncio.sleep(config.listing_cache_ttl_seconds)
            await _run_background_sync()

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
    url = f"{UPSTREAM_BASE}{upstream_path}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _EXCLUDED_REQUEST_HEADERS}
    headers["Cookie"] = f"mac={config.mac_address}; stb_lang=en; timezone=GMT"
    headers["Authorization"] = f"Bearer {session.token}"
    body = await request.body()

    async def do_request() -> httpx.Response:
        return await client.http.request(
            request.method,
            url,
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
    _save_cache_to_disk()
    await client.close()
