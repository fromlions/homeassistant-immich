"""The Immich hub against a local aiohttp server, and the setup and image entity logic."""
import asyncio
import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ha_stubs  # noqa: E402  must come before the component

from custom_components.immich import (  # noqa: E402
    ImmichData,
    async_setup_entry,
    hub as hub_module,
    image,
    options,
)
from custom_components.immich.hub import ApiError, CannotConnect, ImmichHub, normalize_host  # noqa: E402

CONFIG_ENTRY = ha_stubs.ConfigEntry
KEY = "secret"
ASSET = "11111111-1111-4111-8111-111111111111"


class FakeImmich:
    """Records requests and answers like Immich v3, with a few switches for the failure cases."""

    def __init__(self, prefix=""):
        self.prefix = prefix
        self.requests = []
        self.auth_status = True
        self.random_assets = [{"id": "a", "type": "IMAGE"}, {"id": "b", "type": "IMAGE"}]
        self.original = (200, b"jpeg-bytes", "image/jpeg")
        self.delay = 0
        self.validate_status = 200

    def app(self):
        app = web.Application()
        p = self.prefix
        app.router.add_post(p + "/api/auth/validateToken", self.validate)
        app.router.add_post(p + "/api/search/random", self.search)
        app.router.add_get(p + "/api/assets/{id}/thumbnail", self.download)
        app.router.add_get(p + "/api/albums", self.albums)
        return app

    async def validate(self, request):
        self.requests.append(("validate", request.headers.get("x-api-key")))
        await asyncio.sleep(self.delay)
        if self.validate_status != 200:
            return web.Response(status=self.validate_status, text="<html>proxy</html>", content_type="text/html")
        return web.json_response({"authStatus": self.auth_status})

    async def search(self, request):
        body = await request.json()
        self.requests.append(("search", body))
        return web.json_response(self.random_assets)

    async def download(self, request):
        self.requests.append(("thumbnail", request.query.get("size")))
        status, data, content_type = self.original
        return web.Response(status=status, body=data, content_type=content_type)

    async def albums(self, request):
        return web.Response(text="<html>proxy error</html>", content_type="text/html")


class HubTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fake = FakeImmich()
        await self.start(self.fake)

    async def start(self, fake):
        self.server = TestServer(fake.app())
        await self.server.start_server()
        ha_stubs.SESSION = aiohttp.ClientSession()
        self.host = str(self.server.make_url(fake.prefix or ""))
        self.hub = ImmichHub(object(), self.host, KEY)

    async def asyncTearDown(self):
        await ha_stubs.SESSION.close()
        await self.server.close()

    async def test_authenticate_sends_the_key_and_reads_auth_status(self):
        self.assertTrue(await self.hub.authenticate())
        self.assertEqual(("validate", KEY), self.fake.requests[0])
        self.fake.auth_status = False
        self.assertFalse(await self.hub.authenticate())

    async def test_a_wrong_key_is_false_but_a_server_error_is_cannot_connect(self):
        self.fake.validate_status = 401
        self.assertFalse(await self.hub.authenticate())
        self.fake.validate_status = 403
        self.assertFalse(await self.hub.authenticate())
        for status in (500, 502, 503):  # a reverse proxy that is still starting must be retried, not reauthenticated
            self.fake.validate_status = status
            with self.assertRaises(CannotConnect):
                await self.hub.authenticate()

    async def test_a_200_that_is_not_json_is_cannot_connect(self):
        async def html(request):
            return web.Response(text="<html>welcome</html>", content_type="text/html")
        await ha_stubs.SESSION.close()
        await self.server.close()
        app = web.Application()
        app.router.add_post("/api/auth/validateToken", html)
        self.server = TestServer(app)
        await self.server.start_server()
        ha_stubs.SESSION = aiohttp.ClientSession()
        with self.assertRaises(CannotConnect):
            await ImmichHub(object(), str(self.server.make_url("")), KEY).authenticate()

    async def test_random_search_sends_typed_json_and_asks_for_few_images_without_exif(self):
        images = await self.hub.random_images({"isFavorite": True}, 3)
        self.assertEqual(["a", "b"], [i["id"] for i in images])
        self.assertEqual(1, len(self.fake.requests))  # one request: the library is never listed
        body = self.fake.requests[0][1]
        self.assertIs(True, body["isFavorite"])  # a real boolean: Immich v3 refuses the string "true"
        self.assertEqual({"size": 3, "type": "IMAGE", "withExif": False}, {k: body[k] for k in ("size", "type", "withExif")})

    async def test_random_search_sends_the_album_id_as_a_list_and_drops_other_types(self):
        self.fake.random_assets = [{"id": "a", "type": "IMAGE"}, {"id": "v", "type": "VIDEO"}]
        images = await self.hub.random_images({"albumIds": ["album-1"]}, 3)
        self.assertEqual(["album-1"], self.fake.requests[0][1]["albumIds"])
        self.assertEqual(["a"], [i["id"] for i in images])

    async def test_random_search_with_no_match_is_an_empty_list_and_a_non_list_is_an_api_error(self):
        self.fake.random_assets = []
        self.assertEqual([], await self.hub.random_images({}, 3))
        self.fake.random_assets = {"assets": {}}
        with self.assertRaises(ApiError):
            await self.hub.random_images({}, 3)

    async def test_a_path_prefix_of_a_reverse_proxy_is_kept(self):
        await ha_stubs.SESSION.close()
        await self.server.close()
        self.fake = FakeImmich(prefix="/immich")
        await self.start(self.fake)
        self.assertTrue(await self.hub.authenticate())
        self.hub = ImmichHub(object(), self.host + "/", KEY)  # a trailing slash does not double up
        self.assertTrue(await self.hub.authenticate())

    async def test_download_returns_the_preview_and_none_for_refusals_and_unsupported_types(self):
        self.assertEqual((b"jpeg-bytes", "image/jpeg"), await self.hub.download_asset(ASSET))
        self.assertEqual(("thumbnail", "preview"), self.fake.requests[-1])  # not the original, which can be huge
        self.fake.original = (200, b"webp-bytes", "image/webp")  # Immich can be set to WebP previews
        self.assertEqual((b"webp-bytes", "image/webp"), await self.hub.download_asset(ASSET))
        self.fake.original = (404, b"", "application/json")
        self.assertIsNone(await self.hub.download_asset(ASSET))
        self.fake.original = (200, b"heic", "image/heic")
        self.assertIsNone(await self.hub.download_asset(ASSET))

    async def test_download_asks_for_the_configured_size(self):
        full = ImmichHub(object(), self.host, KEY, image_size="fullsize")
        await full.download_asset(ASSET)
        self.assertEqual(("thumbnail", "fullsize"), self.fake.requests[-1])

    async def test_a_non_json_answer_is_an_api_error(self):
        with self.assertRaises(ApiError):
            await self.hub.list_all_albums()

    async def test_a_server_that_is_down_is_cannot_connect(self):
        down = ImmichHub(object(), "http://127.0.0.1:9", KEY)
        with self.assertRaises(CannotConnect):
            await down.authenticate()

    async def test_a_timeout_is_cannot_connect(self):
        self.fake.delay = 1
        with mock.patch.object(hub_module, "_TIMEOUT", aiohttp.ClientTimeout(total=0.1)):
            with self.assertRaises(CannotConnect):
                await self.hub.authenticate()


class HostAndManifestTest(unittest.TestCase):
    def test_a_host_gets_a_scheme_and_loses_the_trailing_slash(self):
        for given, expected in (("immich.local:2283", "http://immich.local:2283"), (" http://192.168.1.5:2283/ ", "http://192.168.1.5:2283"),
                                ("https://photos.example.com/immich/", "https://photos.example.com/immich")):
            self.assertEqual(expected, normalize_host(given), given)

    def test_the_manifest_has_no_requirements_and_points_at_this_repository(self):
        manifest = json.loads((Path(__file__).resolve().parents[1] / "custom_components" / "immich" / "manifest.json").read_text())
        self.assertEqual([], manifest["requirements"])
        self.assertIn("fromlions/homeassistant-immich", manifest["documentation"])
        self.assertEqual("local_polling", manifest["iot_class"])
        self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+$")


class OptionsTest(unittest.TestCase):
    def test_nothing_set_gives_the_defaults(self):
        self.assertEqual("preview", options.image_size({}))
        self.assertEqual(5, options.refresh_minutes({}))
        self.assertEqual(timedelta(minutes=5), options.refresh_interval({"watched_albums": []}))

    def test_set_values_are_used(self):
        given = {"image_size": "fullsize", "refresh_interval": 30}
        self.assertEqual("fullsize", options.image_size(given))
        self.assertEqual(timedelta(minutes=30), options.refresh_interval(given))
        self.assertEqual(30, options.refresh_minutes({"refresh_interval": 30.0}))  # a number selector gives floats

    def test_values_outside_the_allowed_range_or_kind_fall_back_or_clamp(self):
        self.assertEqual("preview", options.image_size({"image_size": "original"}))  # not offered
        self.assertEqual(1, options.refresh_minutes({"refresh_interval": 0}))
        self.assertEqual(1440, options.refresh_minutes({"refresh_interval": 100000}))
        self.assertEqual(5, options.refresh_minutes({"refresh_interval": "soon"}))
        self.assertEqual(5, options.refresh_minutes({"refresh_interval": None}))


class StringsTest(unittest.TestCase):
    root = Path(__file__).resolve().parents[1] / "custom_components" / "immich"

    def test_strings_and_english_translation_agree_on_options_selectors_and_services(self):
        strings = json.loads((self.root / "strings.json").read_text(encoding="utf8"))
        english = json.loads((self.root / "translations" / "en.json").read_text(encoding="utf8"))
        for key in ("watched_albums", "image_size", "refresh_interval"):
            self.assertIn(key, strings["options"]["step"]["init"]["data"])
        self.assertEqual(strings["options"]["step"]["init"], english["options"]["step"]["init"])
        self.assertEqual(strings["selector"], english["selector"])
        self.assertEqual(strings["services"], english["services"])
        self.assertEqual({"preview", "fullsize"}, set(strings["selector"]["image_size"]["options"]))

    def test_the_service_is_described_in_services_yaml(self):
        self.assertRegex((self.root / "services.yaml").read_text(encoding="utf8"), r"(?m)^next_image:")


class SetupTest(unittest.IsolatedAsyncioTestCase):
    def entry(self):
        return CONFIG_ENTRY(data={"host": "http://immich", "api_key": KEY})

    def hass(self):
        hass = mock.Mock()
        hass.config_entries.async_forward_entry_setups = mock.AsyncMock()
        return hass

    async def test_immich_down_at_startup_is_retried_not_failed(self):
        with mock.patch("custom_components.immich.ImmichHub.authenticate", side_effect=CannotConnect("down")):
            with self.assertRaises(ha_stubs.ConfigEntryNotReady):
                await async_setup_entry(self.hass(), self.entry())

    async def test_an_album_list_that_fails_is_retried_by_setup_not_by_the_platform(self):
        # Home Assistant retries a ConfigEntryNotReady from async_setup_entry; from a forwarded platform it does not.
        for error in (CannotConnect("down"), ApiError()):
            with mock.patch("custom_components.immich.ImmichHub.authenticate", return_value=True),                     mock.patch("custom_components.immich.ImmichHub.list_all_albums", side_effect=error):
                with self.assertRaises(ha_stubs.ConfigEntryNotReady):
                    await async_setup_entry(self.hass(), self.entry())

    async def test_a_refused_key_starts_reauthentication(self):
        with mock.patch("custom_components.immich.ImmichHub.authenticate", return_value=False):
            with self.assertRaises(ha_stubs.ConfigEntryAuthFailed):
                await async_setup_entry(self.hass(), self.entry())

    async def test_a_good_key_stores_the_hub_and_albums_and_sets_up_the_platform(self):
        hass, entry = self.hass(), self.entry()
        albums = [{"id": "x", "albumName": "Holidays"}]
        with mock.patch("custom_components.immich.ImmichHub.authenticate", return_value=True),                 mock.patch("custom_components.immich.ImmichHub.list_all_albums", return_value=albums):
            self.assertTrue(await async_setup_entry(hass, entry))
        self.assertIsInstance(entry.runtime_data, ImmichData)
        self.assertIsInstance(entry.runtime_data.hub, ImmichHub)
        self.assertEqual(albums, entry.runtime_data.albums)
        self.assertEqual(1, len(entry.listeners))  # options changes reload the entry
        hass.config_entries.async_forward_entry_setups.assert_awaited_once()

    async def test_the_hub_gets_the_picture_size_from_the_options(self):
        for given, expected in (({}, "preview"), ({"image_size": "fullsize"}, "fullsize")):
            entry = CONFIG_ENTRY(data={"host": "http://immich", "api_key": KEY}, options=given)
            with mock.patch("custom_components.immich.ImmichHub.authenticate", return_value=True),                     mock.patch("custom_components.immich.ImmichHub.list_all_albums", return_value=[]):
                await async_setup_entry(self.hass(), entry)
            self.assertEqual(expected, entry.runtime_data.hub.image_size)

    async def test_the_platform_hands_the_chosen_interval_to_every_entity_and_registers_the_service_once_per_setup(self):
        albums = [{"id": "x", "albumName": "Holidays"}]
        entry = CONFIG_ENTRY(options={"watched_albums": ["x"], "refresh_interval": 15})
        entry.runtime_data = ImmichData(FakeHub(), albums)
        added = []
        ha_stubs.PLATFORM.services.clear()
        await image.async_setup_entry(object(), entry, added.extend)
        self.assertEqual([timedelta(minutes=15)] * 2, [e._interval for e in added])
        self.assertEqual([("next_image", None, "async_next_image")], ha_stubs.PLATFORM.services)
        self.assertTrue(callable(image.BaseImmichImage.async_next_image))

    async def test_names_and_unique_ids_stay_what_dashboards_are_bound_to(self):
        favorite = image.ImmichImageFavorite(object(), FakeHub(), timedelta(minutes=5))
        album = image.ImmichImageAlbum(object(), FakeHub(), timedelta(minutes=5), "album-id", "Screen saver")
        self.assertEqual(("favorite_image", "Immich: Random favorite image"), (favorite._attr_unique_id, favorite._attr_name))
        self.assertEqual(("album-id", "Immich: Screen saver"), (album._attr_unique_id, album._attr_name))
        self.assertFalse(hasattr(favorite, "_attr_device_info"))

    async def test_the_image_platform_creates_the_favorite_and_the_watched_albums(self):
        albums = [{"id": "x", "albumName": "Holidays"}, {"id": "y", "albumName": "Work"}]
        entry = CONFIG_ENTRY(options={"watched_albums": ["y"]})
        entry.runtime_data = ImmichData(FakeHub(), albums)
        added = []
        await image.async_setup_entry(object(), entry, added.extend)
        self.assertEqual(["Immich: Random favorite image", "Immich: Work"], [e._attr_name for e in added])


class FakeHub:
    def __init__(self, ids=("a",), downloads=((b"img", "image/jpeg"),), info=None, error=None):
        self.ids, self.downloads, self.info, self.error = list(ids), list(downloads), info, error
        self.download_calls = 0
        self.random_calls = []

    async def random_images(self, criteria, count):
        self.random_calls.append((criteria, count))
        if self.error:
            raise self.error
        return [{"id": i} for i in self.ids][:count]

    async def download_asset(self, asset_id):
        if self.error:
            raise self.error
        self.download_calls += 1
        return self.downloads[min(self.download_calls, len(self.downloads)) - 1]

    async def get_asset_info(self, asset_id):
        if self.info is None:
            raise ApiError()
        return self.info


class ImageEntityTest(unittest.IsolatedAsyncioTestCase):
    def entity(self, hub):
        return image.ImmichImageFavorite(object(), hub, timedelta(minutes=5))

    async def test_a_picture_is_shown_with_its_metadata(self):
        entity = self.entity(FakeHub(info={"originalFileName": "cat.jpg", "localDateTime": "2026-01-01"},
                                     downloads=((b"img", "image/webp"),)))
        self.assertEqual(b"img", await entity.async_image())
        self.assertEqual("image/webp", entity._attr_content_type)  # served as what Immich sent
        self.assertEqual("cat.jpg", entity._attr_extra_state_attributes["media_filename"])
        self.assertIsNotNone(entity._attr_image_last_updated.tzinfo)

    async def test_missing_metadata_does_not_lose_the_picture(self):
        entity = self.entity(FakeHub(info=None))
        self.assertEqual(b"img", await entity.async_image())
        self.assertEqual("", entity._attr_extra_state_attributes["media_filename"])

    async def test_unusable_pictures_stop_after_the_attempt_cap_and_keep_the_previous_one(self):
        hub = FakeHub(ids=("a", "b", "c"), downloads=((b"first", "image/jpeg"), None, None, None), info={})
        entity = self.entity(hub)
        await entity._async_refresh(None)
        self.assertEqual(b"first", entity._current_image_bytes)
        hub.download_calls = 1
        await entity._async_refresh(None)
        self.assertEqual(b"first", entity._current_image_bytes)
        self.assertEqual(1 + image._MAX_ATTEMPTS, hub.download_calls)

    async def test_an_unreachable_immich_keeps_the_previous_picture(self):
        hub = FakeHub(info={})
        entity = self.entity(hub)
        await entity._async_refresh(None)
        hub.error = CannotConnect("down")
        await entity._async_refresh(None)  # no exception
        self.assertEqual(b"img", entity._current_image_bytes)

    async def test_nothing_matching_keeps_the_entity_empty_and_asks_again_next_time(self):
        hub = FakeHub(ids=())
        entity = self.entity(hub)
        await entity._async_refresh(None)
        await entity._async_refresh(None)
        self.assertEqual(2, len(hub.random_calls))
        self.assertIsNone(entity._current_image_bytes)
        self.assertEqual(0, hub.download_calls)

    async def test_every_refresh_asks_immich_for_new_random_pictures(self):
        hub = FakeHub(ids=("a", "b", "c", "d"), info={})
        entity = self.entity(hub)
        await entity._async_refresh(None)
        await entity._async_refresh(None)
        self.assertEqual([({"isFavorite": True}, image._MAX_ATTEMPTS)] * 2, hub.random_calls)

    async def test_the_timer_runs_at_the_interval_loads_a_picture_and_stops_with_the_entity(self):
        hub = FakeHub(info={})
        entity = image.ImmichImageFavorite(object(), hub, timedelta(minutes=7))
        ha_stubs.TIMERS.clear()
        await entity.async_added_to_hass()
        (timer,) = ha_stubs.TIMERS
        self.assertEqual(timedelta(minutes=7), timer["interval"])
        self.assertFalse(entity._attr_should_poll)  # Home Assistant's own polling would add a second schedule
        await timer["action"](None)
        self.assertEqual(b"img", entity._current_image_bytes)
        for remove in entity.on_remove:
            remove()
        self.assertTrue(timer["cancelled"])

    async def test_next_image_loads_a_new_picture_now_and_a_failure_keeps_the_old_one(self):
        hub = FakeHub(downloads=((b"one", "image/jpeg"), (b"two", "image/jpeg")), info={})
        entity = self.entity(hub)
        await entity.async_next_image()
        self.assertEqual(b"one", entity._current_image_bytes)
        await entity.async_next_image()
        self.assertEqual(b"two", entity._current_image_bytes)
        hub.error = CannotConnect("down")
        await entity.async_next_image()  # no exception
        self.assertEqual(b"two", entity._current_image_bytes)

    async def test_an_album_entity_filters_on_its_album(self):
        hub = FakeHub(info={})
        await image.ImmichImageAlbum(object(), hub, timedelta(minutes=5), "album-1", "Holidays")._async_refresh(None)
        self.assertEqual([({"albumIds": ["album-1"]}, image._MAX_ATTEMPTS)], hub.random_calls)


if __name__ == "__main__":
    unittest.main()
