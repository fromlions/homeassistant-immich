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


class ConfigFlow:
    def __init_subclass__(cls, domain=None, **kwargs):
        super().__init_subclass__(**kwargs)


class OptionsFlow:
    """What the options flow uses: hass and config_entry are set by Home Assistant, the rest returns the result."""

    hass = None
    config_entry = None

    def async_show_form(self, *, step_id=None, data_schema=None, errors=None, description_placeholders=None,
                        last_step=None, preview=None):
        return {"type": "form", "step_id": step_id, "data_schema": data_schema, "errors": errors}

    def async_create_entry(self, *, title=None, data):
        return {"type": "create_entry", "title": title, "data": data}

    def async_abort(self, *, reason, description_placeholders=None):
        return {"type": "abort", "reason": reason}


class MultiSelect:
    """config_validation.multi_select: a list of keys of the options."""

    def __init__(self, options):
        self.options = options

    def __call__(self, selected):
        import voluptuous
        if not isinstance(selected, list) or any(item not in self.options for item in selected):
            raise voluptuous.Invalid("not a selection")
        return selected


class NumberSelector:
    """helpers.selector.NumberSelector: a float within min and max."""

    def __init__(self, config):
        self.config = config

    def __call__(self, data):
        import voluptuous
        value = float(data)
        if value < self.config["min"] or value > self.config["max"]:
            raise voluptuous.Invalid(f"{value} is out of range")
        return value


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


SERVICES = []  # (domain, name, entity_domain, schema, func) of every platform entity service registered


def async_register_platform_entity_service(
    hass, service_domain, service_name, *, admin_only=False, description_placeholders=None,
    entity_device_classes=None, entity_domain, func, required_features=None, schema, supports_response=None
):
    """The signature of homeassistant.helpers.service.async_register_platform_entity_service (2026.10)."""
    SERVICES.append((service_domain, service_name, entity_domain, schema, func))


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
    module("homeassistant.config_entries", ConfigEntry=ConfigEntry, ConfigFlow=ConfigFlow, OptionsFlow=OptionsFlow,
           ConfigFlowResult=dict)
    module("homeassistant.const", CONF_API_KEY="api_key", CONF_HOST="host", Platform=types.SimpleNamespace(IMAGE="image"))
    module("homeassistant.helpers")
    module("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda hass: SESSION)
    module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    module("homeassistant.helpers.config_validation", config_entry_only_config_schema=lambda domain: ("schema", domain),
           multi_select=MultiSelect)
    module("homeassistant.helpers.selector", NumberSelector=NumberSelector, NumberSelectorConfig=dict,
           NumberSelectorMode=types.SimpleNamespace(BOX="box"))
    module("homeassistant.helpers.service", async_register_platform_entity_service=async_register_platform_entity_service)
    module("homeassistant.helpers.typing", ConfigType=dict)
    module("homeassistant.helpers.event", async_track_time_interval=async_track_time_interval)
    module("homeassistant.components")
    module("homeassistant.components.image", ImageEntity=ImageEntity)
    module("homeassistant.util")
    module("homeassistant.util.dt", utcnow=lambda: datetime.now(timezone.utc))


install()
