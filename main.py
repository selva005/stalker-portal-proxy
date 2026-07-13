"""FastAPI app exposing a Stalker-Portal account as an M3U playlist."""
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse

from config import config
from m3u import build_m3u
from stalker_client import PortalError, StalkerClient

logging.basicConfig(
    level=config.log_level,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("main")

app = FastAPI(title="Stalker-to-M3U")
client = StalkerClient(config)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "Unknown"


async def _with_session(action):
    """Run `action(session)`, retrying once with a forced re-auth on failure."""
    session = await client.get_session()
    try:
        return await action(session)
    except PortalError as e:
        logger.warning("Portal call failed (%s), retrying with fresh session", e)
        client.invalidate_session()
        session = await client.get_session(force_refresh=True)
        return await action(session)


@app.get("/playlist.m3u8")
async def playlist(request: Request):
    try:
        async def action(session):
            channels = await client.get_all_channels(session.token)
            genres = await client.get_genres(session.token)
            return channels, genres

        channels, genres = await _with_session(action)
        session = await client.get_session()
        origin = str(request.base_url).rstrip("/")
        m3u_text = build_m3u(
            config, channels, genres, session.profile, session.account_info,
            origin, _client_ip(request),
        )
        return PlainTextResponse(m3u_text, media_type="application/vnd.apple.mpegurl")
    except PortalError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/{stream_id}.m3u8")
async def stream(stream_id: str):
    if not stream_id:
        raise HTTPException(status_code=400, detail="Missing channel ID")
    try:
        async def action(session):
            return await client.create_link(stream_id, session.token)

        stream_url = await _with_session(action)
        return RedirectResponse(stream_url, status_code=302)
    except PortalError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.on_event("shutdown")
async def shutdown():
    await client.close()
