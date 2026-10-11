"""The Immich hub against a local aiohttp server, and the setup and image entity logic."""
import asyncio
import json
import sys
import unittest
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
        self.pages = [[{"id": "a", "type": "IMAGE"}, {"id": "v", "type": "VIDEO"}], [{"id": "b", "type": "IMAGE"}]]
        self.original = (200, b"jpeg-bytes", "image/jpeg")
        self.delay = 0
        self.validate_status = 200

    def app(self):
        app = web.Application()
        p = self.prefix
        app.router.add_post(p + "/api/auth/validateToken", self.validate)
        app.router.add_post(p + "/api/search/metadata", self.search)
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
        number = body["page"]
        items = self.pages[number - 1]
        assets = {"items": items, "nextPage": str(number + 1) if number < len(self.pages) else None}
        return web.json_response({"assets": assets})

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

    async def test_favorites_search_sends_json_booleans_and_follows_pages_and_drops_videos(self):
        images = await self.hub.list_favorite_images()
        self.assertEqual(["a", "b"], [i["id"] for i in images])
        bodies = [r[1] for r in self.fake.requests if r[0] == "search"]
        self.assertEqual([1, 2], [b["page"] for b in bodies])
        self.assertIs(True, bodies[0]["isFavorite"])  # a real boolean: Immich v3 refuses the string "true"

    async def test_album_search_sends_the_album_id_as_a_list(self):
        await self.hub.list_album_images("album-1")
        self.assertEqual(["album-1"], self.fake.requests[0][1]["albumIds"])

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
        self.list_calls = 0

    async def list_favorite_images(self):
        self.list_calls += 1
        if self.error:
            raise self.error
        return [{"id": i} for i in self.ids]

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
        return image.ImmichImageFavorite(object(), hub)

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
        hub = FakeHub(downloads=((b"first", "image/jpeg"), None, None, None), info={})
        entity = self.entity(hub)
        await entity.async_update()
        self.assertEqual(b"first", entity._current_image_bytes)
        hub.download_calls = 1
        await entity.async_update()
        self.assertEqual(b"first", entity._current_image_bytes)
        self.assertEqual(1 + image._MAX_ATTEMPTS, hub.download_calls)

    async def test_an_unreachable_immich_keeps_the_previous_picture(self):
        hub = FakeHub(info={})
        entity = self.entity(hub)
        await entity.async_update()
        hub.error = CannotConnect("down")
        entity._available_asset_ids_last_updated = None
        await entity.async_update()  # no exception
        self.assertEqual(b"img", entity._current_image_bytes)

    async def test_an_empty_list_is_asked_for_again_next_time(self):
        hub = FakeHub(ids=())
        entity = self.entity(hub)
        await entity.async_update()
        await entity.async_update()
        self.assertEqual(2, hub.list_calls)
        self.assertIsNone(entity._current_image_bytes)

    async def test_a_filled_list_is_kept_for_the_refresh_interval(self):
        hub = FakeHub(info={})
        entity = self.entity(hub)
        await entity.async_update()
        await entity.async_update()
        self.assertEqual(1, hub.list_calls)


if __name__ == "__main__":
    unittest.main()
