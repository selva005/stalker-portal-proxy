"""Transparent reverse proxy for a Stalker-Portal account.

Auth-related actions are answered directly from one cached, real session so every
downstream STB app (regardless of its own MAC/device settings) shares the same
upstream identity. Everything else is forwarded to the real portal verbatim, with
the real account's credentials injected.
"""
import logging

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


async def _get_valid_session():
    try:
        return await client.get_session()
    except PortalError:
        client.invalidate_session()
        return await client.get_session(force_refresh=True)


@app.get("/stalker_portal/server/load.php")
async def load_php(request: Request):
    params = request.query_params
    req_type = params.get("type", "")
    action = params.get("action", "")

    if req_type == "stb" and action == "handshake":
        session = await _get_valid_session()
        return JSONResponse({"js": {"token": session.token, "random": ""}})

    if req_type == "stb" and action == "get_profile":
        session = await _get_valid_session()
        return JSONResponse({"js": session.profile})

    if req_type == "account_info" and action == "get_main_info":
        session = await _get_valid_session()
        return JSONResponse({"js": session.account_info})

    return await _proxy(request)


@app.api_route("/{path:path}", methods=["GET", "POST", "HEAD"])
async def catch_all(request: Request, path: str):
    return await _proxy(request)


async def _proxy(request: Request) -> Response:
    session = await _get_valid_session()
    url = f"{UPSTREAM_BASE}{request.url.path}"
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

    response_headers = {
        k: v for k, v in response.headers.items() if k.lower() not in _EXCLUDED_RESPONSE_HEADERS
    }
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=response_headers,
        media_type=response.headers.get("content-type"),
    )


@app.on_event("shutdown")
async def shutdown():
    await client.close()
