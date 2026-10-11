"""Hub for Immich integration."""
from __future__ import annotations

import asyncio
import json
import logging

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_HEADER_API_KEY = "x-api-key"
_LOGGER = logging.getLogger(__name__)

# Immich serves previews as JPEG, or WebP when its image settings say so.
_ALLOWED_MIME_TYPES = ["image/png", "image/jpeg", "image/webp"]
# Immich's "preview" is about 1440 px, enough for a dashboard frame. "fullsize" is not a smaller original: for a JPEG,
# PNG or WebP original Immich redirects to the original file (often 10 to 20 MB), and it falls back to the
# preview when it generates no fullsize pictures. "original" is refused for the thumbnail endpoint.
_PICTURE_SIZE = "preview"
# Seconds one request may take in total; Immich answers in well under this unless it is stuck.
_TIMEOUT = aiohttp.ClientTimeout(total=30)
# Bytes of an error body worth logging.
_LOGGED_BODY = 500


def normalize_host(host: str) -> str:
    """The address as the integration stores it: a scheme (http when none is given), no trailing slash."""
    host = host.strip()
    if "://" not in host:
        host = f"http://{host}"
    return host.rstrip("/")


class ImmichHub:
    """Immich API hub."""

    def __init__(self, hass: HomeAssistant, host: str, api_key: str) -> None:
        """Initialize."""
        # Home Assistant's shared session: it is closed by Home Assistant, and reuses connections.
        self._session = async_get_clientsession(hass)
        # No trailing slash: a path prefix of a reverse proxy (https://host/immich) is kept.
        self.host = normalize_host(host)
        self.api_key = api_key

    async def _call(
        self, method: str, path: str, body: dict | None = None, accept: str = "application/json"
    ) -> tuple[int, bytes, str]:
        """One request: status, body and content type. A connection problem or a timeout is CannotConnect."""
        headers = {"Accept": accept, _HEADER_API_KEY: self.api_key}
        try:
            async with self._session.request(
                method, f"{self.host}{path}", headers=headers, json=body, timeout=_TIMEOUT
            ) as response:
                return response.status, await response.read(), response.content_type
        except (aiohttp.ClientError, asyncio.TimeoutError) as exception:
            _LOGGER.error("Error connecting to the API: %r", exception)
            raise CannotConnect from exception

    async def _json(self, method: str, path: str, body: dict | None = None) -> dict | list:
        """The JSON answer of a request that must succeed; anything else is ApiError."""
        status, data, _content_type = await self._call(method, path, body)
        if status != 200:
            _LOGGER.error("Error from API: status=%d body=%s", status, data[:_LOGGED_BODY])
            raise ApiError()
        try:
            return json.loads(data)
        except ValueError as exception:
            # A reverse proxy's error page answers 200 with HTML sometimes.
            _LOGGER.error("Error from API: not JSON: %s", data[:_LOGGED_BODY])
            raise ApiError() from exception

    async def authenticate(self) -> bool:
        """Test if we can authenticate with the host: False only when Immich says the key is wrong. A server
        error or an answer that is not Immich's (a reverse proxy still starting) is CannotConnect, so it is
        retried and does not start reauthentication."""
        status, data, _content_type = await self._call("POST", "/api/auth/validateToken")
        if status in (401, 403):
            return False
        if status != 200:
            _LOGGER.error("Error from API: status=%d body=%s", status, data[:_LOGGED_BODY])
            raise CannotConnect(f"Immich answered {status}")
        try:
            authenticated = bool(json.loads(data).get("authStatus"))
        except (ValueError, AttributeError) as exception:
            _LOGGER.error("Error from API: not JSON: %s", data[:_LOGGED_BODY])
            raise CannotConnect("Immich did not answer with JSON") from exception
        if not authenticated:
            _LOGGER.error("Error from API: body=%s", data[:_LOGGED_BODY])
        return authenticated

    async def get_my_user_info(self) -> dict:
        """Get user info."""
        return await self._json("GET", "/api/users/me")

    async def get_asset_info(self, asset_id: str) -> dict | None:
        """Get asset info."""
        return await self._json("GET", f"/api/assets/{asset_id}")

    async def download_asset(self, asset_id: str) -> tuple[bytes, str] | None:
        """The asset's preview picture and its content type; None when Immich refuses it or it is not a picture
        Home Assistant can show."""
        status, data, content_type = await self._call(
            "GET", f"/api/assets/{asset_id}/thumbnail?size={_PICTURE_SIZE}", accept="*/*"
        )
        if status != 200:
            _LOGGER.error("Error from API: status=%d", status)
            return None
        if content_type not in _ALLOWED_MIME_TYPES:
            _LOGGER.error("MIME type is not supported: %s", content_type)
            return None
        return data, content_type

    async def list_all_albums(self) -> list[dict]:
        """List all albums."""
        return await self._json("GET", "/api/albums")

    async def random_images(self, criteria: dict, count: int) -> list[dict]:
        """Up to `count` random images matching the search criteria, picked by Immich itself, so the whole
        library is never listed. The body goes as JSON: Immich v3 validates the types, so a boolean sent as
        the string "true" is refused. An empty list means nothing matches (yet)."""
        body = dict(criteria, size=count, type="IMAGE", withExif=False)
        result = await self._json("POST", "/api/search/random", body)
        if not isinstance(result, list):
            _LOGGER.error("Error from API: random search did not answer with a list")
            raise ApiError()
        return [asset for asset in result if asset.get("type") == "IMAGE"]


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class ApiError(HomeAssistantError):
    """Error to indicate that the API returned an error."""
