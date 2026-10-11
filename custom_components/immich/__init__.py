"""The immich integration."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .hub import ApiError, CannotConnect, ImmichHub
from .options import image_size

PLATFORMS: list[Platform] = [Platform.IMAGE]


@dataclass
class ImmichData:
    """What the platform needs, read once at setup."""

    hub: ImmichHub
    albums: list[dict]


type ImmichConfigEntry = ConfigEntry[ImmichData]


async def async_setup_entry(hass: HomeAssistant, entry: ImmichConfigEntry) -> bool:
    """Set up immich from a config entry."""

    hub = ImmichHub(
        hass,
        host=entry.data[CONF_HOST],
        api_key=entry.data[CONF_API_KEY],
        image_size=image_size(entry.options),
    )

    # Everything that can fail because Immich is not there yet happens here, not in the platform: Home
    # Assistant retries a ConfigEntryNotReady raised in async_setup_entry with a growing delay, but not one
    # raised from a forwarded platform.
    try:
        authenticated = await hub.authenticate()
        albums = await hub.list_all_albums() if authenticated else []
    except (CannotConnect, ApiError) as error:
        raise ConfigEntryNotReady(f"Cannot reach Immich: {error}") from error
    if not authenticated:
        raise ConfigEntryAuthFailed("Immich did not accept the API key")

    entry.runtime_data = ImmichData(hub, albums)
    entry.async_on_unload(entry.add_update_listener(update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def update_listener(hass: HomeAssistant, entry: ImmichConfigEntry) -> None:
    """Handle options updates."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ImmichConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
