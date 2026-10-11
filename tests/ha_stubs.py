"""The few Home Assistant names the component imports, as small stand-ins, so the tests need no Home Assistant.

Only what the tests exercise is modelled; the real signatures were checked against Home Assistant core
(config_entries.py, components/image, helpers/aiohttp_client.py, exceptions.py). Import this before the component.
"""
import sys
import types
from datetime import datetime, timezone
from typing import Generic, TypeVar

T = TypeVar("T")
SESSION = None  # set by the tests: the aiohttp session async_get_clientsession returns


def module(name, **attributes):
    mod = types.ModuleType(name)
    mod.__dict__.update(attributes)
    sys.modules[name] = mod
    parent, _, child = name.rpartition(".")
    if parent:
        setattr(sys.modules[parent], child, mod)
    return mod


class HomeAssistantError(Exception):
    pass


class ConfigEntryNotReady(HomeAssistantError):
    pass


class ConfigEntryAuthFailed(HomeAssistantError):
    pass


class ConfigEntry(Generic[T]):
    def __init__(self, data=None, options=None):
        self.data = data or {}
        self.options = options or {}
        self.entry_id = "entry"
        self.runtime_data = None


class ImageEntity:
    _attr_image_last_updated = None

    def __init__(self, hass, verify_ssl=False):
        self.hass = hass

    def async_write_ha_state(self):
        pass


def install():
    module("homeassistant")
    module("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
    module("homeassistant.exceptions", HomeAssistantError=HomeAssistantError, ConfigEntryNotReady=ConfigEntryNotReady,
           ConfigEntryAuthFailed=ConfigEntryAuthFailed)
    module("homeassistant.config_entries", ConfigEntry=ConfigEntry)
    module("homeassistant.const", CONF_API_KEY="api_key", CONF_HOST="host", Platform=types.SimpleNamespace(IMAGE="image"))
    module("homeassistant.helpers")
    module("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda hass: SESSION)
    module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    module("homeassistant.components")
    module("homeassistant.components.image", ImageEntity=ImageEntity)
    module("homeassistant.util")
    module("homeassistant.util.dt", utcnow=lambda: datetime.now(timezone.utc))


install()
