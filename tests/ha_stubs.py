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
        self.listeners = []

    def add_update_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def async_on_unload(self, callback):
        return callback


TASKS = []  # (target, name, eager_start) of every config entry background task


class StubConfigEntry:
    def async_create_background_task(self, hass, target, name, eager_start=True):
        TASKS.append((target, name, eager_start))


class StubEntityPlatform:
    config_entry = StubConfigEntry()


class ImageEntity:
    _attr_image_last_updated = None
    _attr_unique_id = None
    platform = StubEntityPlatform()

    @property
    def unique_id(self):
        return self._attr_unique_id

    def __init__(self, hass, verify_ssl=False):
        self.hass = hass
        self.on_remove = []

    async def async_added_to_hass(self):
        pass

    def async_on_remove(self, func):
        self.on_remove.append(func)

    def async_write_ha_state(self):
        pass


class Platform:
    """EntityPlatform.async_register_entity_service(name, schema, func) records its calls."""

    def __init__(self):
        self.services = []

    def async_register_entity_service(self, name, schema, func, *args, **kwargs):
        self.services.append((name, schema, func))


PLATFORM = Platform()  # what async_get_current_platform returns
TIMERS = []  # (hass, action, interval, cancelled) of every async_track_time_interval call


async def fire(timer):
    """One tick of a timer: the real tracker calls the action with the current time."""
    await timer["action"](datetime.now(timezone.utc))


def async_track_time_interval(hass, action, interval, *, name=None, cancel_on_shutdown=None):
    timer = {"hass": hass, "action": action, "interval": interval, "cancelled": False}
    TIMERS.append(timer)
    return lambda: timer.update(cancelled=True)


def install():
    module("homeassistant")
    module("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
    module("homeassistant.exceptions", HomeAssistantError=HomeAssistantError, ConfigEntryNotReady=ConfigEntryNotReady,
           ConfigEntryAuthFailed=ConfigEntryAuthFailed)
    module("homeassistant.config_entries", ConfigEntry=ConfigEntry)
    module("homeassistant.const", CONF_API_KEY="api_key", CONF_HOST="host", Platform=types.SimpleNamespace(IMAGE="image"))
    module("homeassistant.helpers")
    module("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda hass: SESSION)
    module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object,
           async_get_current_platform=lambda: PLATFORM)
    module("homeassistant.helpers.event", async_track_time_interval=async_track_time_interval)
    module("homeassistant.components")
    module("homeassistant.components.image", ImageEntity=ImageEntity)
    module("homeassistant.util")
    module("homeassistant.util.dt", utcnow=lambda: datetime.now(timezone.utc))


install()
