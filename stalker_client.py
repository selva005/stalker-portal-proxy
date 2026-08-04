"""Async client for authenticating and querying a Stalker-Portal, ported from worker.js."""
import asyncio
import hashlib
import json
import logging
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from config import Config

logger = logging.getLogger("stalker_client")


class PortalError(RuntimeError):
    """Raised when a Stalker-Portal call fails or returns unusable data."""


@dataclass
class Session:
    token: str
    profile: dict
    account_info: dict
    obtained_at: float = field(default_factory=time.time)


def _md5(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


AUTH_FAILURE_COOLDOWN_SECONDS = 10.0


class StalkerClient:
    def __init__(self, config: Config):
        self.config = config
        self.hw_version = "1.7-BD-" + _md5(config.mac_address)[:2].upper()
        self.hw_version_2 = _md5(config.serial_number.lower() + config.mac_address.lower())
        self._session: Optional[Session] = None
        self._auth_failed_until_by_role: dict[str, float] = {}
        self._auth_lock = asyncio.Lock()
        self.http = httpx.AsyncClient(timeout=15.0)

    def _headers(self, token: str = "") -> dict:
        headers = {
            "Cookie": f"mac={self.config.mac_address}; stb_lang=en; timezone=GMT",
            "Referer": f"http://{self.config.host}/stalker_portal/c/",
            "User-Agent": (
                "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 "
                "(KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3"
            ),
            "X-User-Agent": f"Model: {self.config.stb_type}; Link: WiFi",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _get_json(self, url: str, token: str = "") -> Any:
        try:
            response = await self.http.get(url, headers=self._headers(token))
        except httpx.HTTPError as e:
            raise PortalError(f"Request failed: {e}") from e
        if response.status_code != 200:
            raise PortalError(f"Portal returned status {response.status_code} for {url}")
        try:
            return response.json()
        except ValueError as e:
            raise PortalError(f"Portal returned invalid JSON for {url}") from e

    async def _get_token(self) -> str:
        url = (
            f"http://{self.config.host}/stalker_portal/server/load.php"
            "?type=stb&action=handshake&token=&JsHttpRequest=1-xml"
        )
        data = await self._get_json(url)
        token = (data.get("js") or {}).get("token", "")
        if not token:
            raise PortalError("Empty token from handshake")
        return token

    async def _auth(self, token: str) -> dict:
        metrics = {
            "mac": self.config.mac_address,
            "model": "",
            "type": "STB",
            "uid": "",
            "device": "",
            "random": "",
        }
        metrics_encoded = urllib.parse.quote(json.dumps(metrics))
        url = (
            f"http://{self.config.host}/stalker_portal/server/load.php?type=stb&action=get_profile"
            "&hd=1&ver=ImageDescription:%200.2.18-r14-pub-250;"
            "%20PORTAL%20version:%205.5.0;%20API%20Version:%20328;"
            f"&num_banks=2&sn={self.config.serial_number}"
            f"&stb_type={self.config.stb_type}&client_type=STB&image_version=218&video_out=hdmi"
            f"&device_id={self.config.device_id}&device_id2={self.config.device_id_2}"
            f"&signature=&auth_second_step=1&hw_version={self.hw_version}"
            f"&not_valid_token=0&metrics={metrics_encoded}"
            f"&hw_version_2={self.hw_version_2}&api_signature={self.config.api_signature}"
            "&prehash=&JsHttpRequest=1-xml"
        )
        data = await self._get_json(url, token)
        return data.get("js") or {}

    async def _handshake(self, token: str) -> str:
        url = (
            f"http://{self.config.host}/stalker_portal/server/load.php"
            f"?type=stb&action=handshake&token={token}&JsHttpRequest=1-xml"
        )
        data = await self._get_json(url)
        new_token = (data.get("js") or {}).get("token", "")
        if not new_token:
            raise PortalError("Empty token from second handshake")
        return new_token

    async def _get_account_info(self, token: str) -> dict:
        url = (
            f"http://{self.config.host}/stalker_portal/server/load.php"
            "?type=account_info&action=get_main_info&JsHttpRequest=1-xml"
        )
        data = await self._get_json(url, token)
        return data.get("js") or {}

    async def _authenticate(self) -> Session:
        logger.info("Authenticating with portal %s", self.config.host)
        token = await self._get_token()
        profile = await self._auth(token)
        new_token = await self._handshake(token)
        account_info = await self._get_account_info(new_token)
        logger.info("Authentication successful")
        return Session(token=new_token, profile=profile, account_info=account_info)

    async def get_session(self, force_refresh: bool = False, role: str = "default") -> Session:
        """Return the shared session, authenticating if needed.

        `role` scopes the auth-failure cooldown only -- there is still exactly one shared
        session/token for the whole process (never two independent logins for the same
        MAC, which risks each invalidating the other's token upstream). Scoping the
        cooldown by role means a failure triggered by one role (e.g. a long-running
        background crawl) doesn't block another role's (e.g. real-time STB traffic)
        ability to attempt its own re-authentication.
        """
        if force_refresh or self._session is None:
            async with self._auth_lock:
                if force_refresh or self._session is None:  # re-check: another caller may have won the race
                    failed_until = self._auth_failed_until_by_role.get(role, 0.0)
                    if time.time() < failed_until:
                        raise PortalError(
                            f"Skipping re-authentication (role={role}): recent auth failure, still in cooldown"
                        )
                    try:
                        self._session = await self._authenticate()
                    except PortalError:
                        self._auth_failed_until_by_role[role] = time.time() + AUTH_FAILURE_COOLDOWN_SECONDS
                        raise
        return self._session

    def invalidate_session(self) -> None:
        self._session = None

    async def close(self) -> None:
        await self.http.aclose()
