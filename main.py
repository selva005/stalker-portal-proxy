"""Transparent reverse proxy for a Stalker-Portal account.

Auth-related actions are answered directly from one cached, real session so every
downstream STB app (regardless of its own MAC/device settings) shares the same
upstream identity. Everything else is forwarded to the real portal verbatim, with
the real account's credentials injected.
"""
import json
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from config import config
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


def _cache_key(upstream_path: str, query_params) -> str:
    return f"{upstream_path}?{urlencode(sorted(query_params.multi_items()))}"


def _is_blocked_category(title: str) -> bool:
    title_lower = title.lower()
    return any(blocked in title_lower for blocked in config.blocked_category_names)


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


def _apply_filter_if_needed(content: bytes, action: str, filtered: bool) -> bytes:
    if filtered and config.blocked_category_names and action in _CATEGORY_LISTING_ACTIONS:
        return _filter_categories(content)
    return content


async def _proxy(request: Request, effective_path: str, filtered: bool) -> Response:
    upstream_path = _upstream_path(effective_path)
    action = request.query_params.get("action", "")
    cacheable = request.method == "GET" and action in _CACHEABLE_ACTIONS
    cache_key = _cache_key(upstream_path, request.query_params) if cacheable else None

    if cache_key is not None:
        cached = _listing_cache.get(cache_key)
        if cached is not None and cached.expires_at > time.time():
            content = _apply_filter_if_needed(cached.content, action, filtered)
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

    content = _apply_filter_if_needed(response.content, action, filtered)

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
    await client.close()
