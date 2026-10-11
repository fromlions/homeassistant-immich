"""The immich integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .hub import CannotConnect, ImmichHub

PLATFORMS: list[Platform] = [Platform.IMAGE]

type ImmichConfigEntry = ConfigEntry[ImmichHub]


async def async_setup_entry(hass: HomeAssistant, entry: ImmichConfigEntry) -> bool:
    """Set up immich from a config entry."""

    hub = ImmichHub(hass, host=entry.data[CONF_HOST], api_key=entry.data[CONF_API_KEY])

    try:
        authenticated = await hub.authenticate()
    except CannotConnect as error:
        # Immich is not reachable yet (it starts after Home Assistant sometimes): Home Assistant
        # tries again with a growing delay instead of leaving the integration failed.
        raise ConfigEntryNotReady(f"Cannot connect to Immich: {error}") from error
    if not authenticated:
        raise ConfigEntryAuthFailed("Immich did not accept the API key")

    entry.runtime_data = hub

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ImmichConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
