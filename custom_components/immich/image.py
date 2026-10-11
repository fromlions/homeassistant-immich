"""Image device for Immich integration."""
from __future__ import annotations

from datetime import datetime, timedelta
import logging

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from . import ImmichConfigEntry
from .const import CONF_WATCHED_ALBUMS, SERVICE_NEXT_IMAGE
from .hub import ImmichHub
from .options import refresh_interval

# Random pictures asked for, and so tried, in one refresh before it gives up and the entity keeps the previous one.
_MAX_ATTEMPTS = 3

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ImmichConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Immich image platform."""

    hub = config_entry.runtime_data.hub
    albums = config_entry.runtime_data.albums
    interval = refresh_interval(config_entry.options)

    # An entity service: it runs on the image entities the call targets. Home Assistant registers it once for
    # the domain, whatever the number of entries, and ignores the repeat on a reload.
    entity_platform.async_get_current_platform().async_register_entity_service(
        SERVICE_NEXT_IMAGE, None, "async_next_image"
    )

    # Create entity for random favorite image
    async_add_entities([ImmichImageFavorite(hass, hub, interval)])

    # Create entities for random image from each watched album
    watched_albums = config_entry.options.get(CONF_WATCHED_ALBUMS, [])
    async_add_entities(
        [
            ImmichImageAlbum(
                hass, hub, interval, album_id=album["id"], album_name=album["albumName"]
            )
            for album in albums
            if album["id"] in watched_albums
        ]
    )


class BaseImmichImage(ImageEntity):
    """Base image entity for Immich. Subclasses will define where the random image comes from (e.g. favorite images, by album ID,..)."""

    _attr_has_entity_name = True

    # Home Assistant's polling interval is one per platform, set by the code and not by an entry's options, so
    # the entity runs its own timer for the interval the user chose.
    _attr_should_poll = False

    _current_image_bytes: bytes | None = None

    def __init__(self, hass: HomeAssistant, hub: ImmichHub, interval: timedelta) -> None:
        """Initialize the Immich image entity."""
        super().__init__(hass=hass, verify_ssl=True)
        self.hub = hub
        self._interval = interval
        self.hass = hass

        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self) -> None:
        """Start the refresh timer; it stops with the entity, which a changed option reloads."""
        await super().async_added_to_hass()
        self.async_on_remove(async_track_time_interval(self.hass, self._async_refresh, self._interval))

    async def async_next_image(self) -> None:
        """The next_image service: load the next picture now instead of waiting for the timer."""
        await self._load_and_cache_next_image()

    async def _async_refresh(self, _now: datetime) -> None:
        """The timer's action."""
        await self._load_and_cache_next_image()

    async def async_image(self) -> bytes | None:
        """Return the current image. If no image is available, load and cache the image."""
        if not self._current_image_bytes:
            await self._load_and_cache_next_image()

        return self._current_image_bytes

    def _search_criteria(self) -> dict:
        """What Immich's random search filters on."""
        raise NotImplementedError

    async def _random_asset_ids(self) -> list[str]:
        """A few random asset ids from Immich: one request per refresh, the picture list is never fetched."""
        images = await self.hub.random_images(self._search_criteria(), _MAX_ATTEMPTS)
        return [image["id"] for image in images]

    async def _load_and_cache_next_image(self) -> None:
        """Download and cache the next image; on any failure the previous image stays."""
        try:
            asset_ids = await self._random_asset_ids()
            if not asset_ids:
                _LOGGER.debug("No assets are available")
                return

            for asset_id in asset_ids:
                downloaded = await self.hub.download_asset(asset_id)

                if not downloaded:
                    # Not a picture Home Assistant can show: try another one.
                    continue

                asset_bytes, content_type = downloaded
                await self._set_asset_attributes(asset_id)

                self._current_image_bytes = asset_bytes
                self._attr_content_type = content_type
                self._attr_image_last_updated = dt_util.utcnow()
                self.async_write_ha_state()
                return

            _LOGGER.warning("No usable picture among %d random ones; keeping the current one", len(asset_ids))
        except HomeAssistantError as error:
            # Immich unreachable or refusing: keep showing the last picture instead of failing the update.
            _LOGGER.warning("Could not refresh the Immich picture: %s", error)

    async def _set_asset_attributes(self, asset_id: str) -> None:
        """The picture's file name, date and metadata as attributes; the picture shows without them."""
        try:
            asset_info = await self.hub.get_asset_info(asset_id) or {}
        except HomeAssistantError as error:
            _LOGGER.debug("No metadata for %s: %s", asset_id, error)
            asset_info = {}

        self._attr_extra_state_attributes["media_filename"] = (
            asset_info.get("originalFileName") or ""
        )
        self._attr_extra_state_attributes["media_exif"] = (
            asset_info.get("exifInfo") or ""
        )
        self._attr_extra_state_attributes["media_localdatetime"] = (
            asset_info.get("localDateTime") or ""
        )


class ImmichImageFavorite(BaseImmichImage):
    """Image entity for Immich that displays a random image from the user's favorites."""

    _attr_unique_id = "favorite_image"
    _attr_name = "Immich: Random favorite image"

    def _search_criteria(self) -> dict:
        return {"isFavorite": True}


class ImmichImageAlbum(BaseImmichImage):
    """Image entity for Immich that displays a random image from a specific album."""

    def __init__(
        self, hass: HomeAssistant, hub: ImmichHub, interval: timedelta, album_id: str, album_name: str
    ) -> None:
        """Initialize the Immich image entity."""
        super().__init__(hass, hub, interval)
        self._album_id = album_id
        self._attr_unique_id = album_id
        self._attr_name = f"Immich: {album_name}"

    def _search_criteria(self) -> dict:
        return {"albumIds": [self._album_id]}
