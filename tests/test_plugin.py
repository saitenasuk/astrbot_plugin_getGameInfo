import asyncio
import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from PIL import Image

from tests.support import (
    HIGHWAY_ERROR,
    ActionFailed,
    FakeAdapter,
    FakeBot,
    FakeEvent,
    FakeScraper,
    TimingError,
    games,
    has_images,
    main,
)


class PluginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = {"request_timeout": 30, "max_display_items": 21}
        self.other_bot = FakeBot()
        context = SimpleNamespace(
            platform_manager=SimpleNamespace(
                platform_insts=[FakeAdapter(self.other_bot)],
            )
        )
        self.plugin = main.GameDownPlugin(context, self.config)
        self.scraper = FakeScraper()
        self.plugin._scraper = self.scraper
        self.plugin._prepare_image = AsyncMock(return_value="base64://valid-image")
        self.addAsyncCleanup(self.plugin.terminate)
        self.event = FakeEvent()

    async def consume(self, text=None):
        if text is not None:
            self.event.message_str = text
        return [result async for result in self.plugin.on_game(self.event)]

    def sent(self, action="send_group_forward_msg"):
        return [payload for name, payload in self.event.bot.calls if name == action]

    def fail_images(self, action, payload):
        if action.startswith("send_") and has_images(payload):
            raise ActionFailed(HIGHWAY_ERROR)

    async def test_list_uses_receiving_account_and_valid_forward_nodes(self):
        await self.consume()
        self.assertEqual(self.other_bot.calls, [])
        nodes = self.sent()[0]["messages"]
        self.assertEqual(len(nodes), 2)
        self.assertEqual(nodes[0]["data"]["user_id"], "789")
        self.assertEqual(
            [s["type"] for s in nodes[0]["data"]["content"]], ["image", "text"]
        )

    async def test_reported_21_image_failure_retries_once_with_all_titles_and_urls(
        self,
    ):
        self.scraper.results = games(21)
        self.event.bot = FakeBot(self.fail_images)
        with self.assertLogs("getgameinfo-tests", level="WARNING") as logs:
            results = await self.consume()
        calls = self.sent()
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(calls[1]["messages"]), 21)
        self.assertTrue(has_images(calls[0]))
        self.assertFalse(has_images(calls[1]))
        for index, node in enumerate(calls[1]["messages"]):
            self.assertIn(f"Game {index}", node["data"]["content"][0]["data"]["text"])
            self.assertIn(
                f"/games/{index}.html", node["data"]["content"][0]["data"]["text"]
            )
        self.assertIn("纯文字游戏列表", results[-1])
        self.assertIn("reason=HIGHWAY_CONNECT_TIMEOUT", "\n".join(logs.output))
        self.assertIn("retcode=100", "\n".join(logs.output))
        self.assertNotIn("base64://valid-image", "\n".join(logs.output))
        self.assertEqual(self.plugin._prepare_image.await_count, 21)

    async def test_mock_http_posters_to_onebot_text_fallback_pipeline(self):
        buffer = io.BytesIO()
        Image.new("RGB", (250, 165), "blue").save(buffer, format="JPEG")
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, content=buffer.getvalue())

        await self.plugin._image_client.aclose()
        self.plugin._image_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
        self.plugin._prepare_image = main.GameDownPlugin._prepare_image.__get__(
            self.plugin
        )
        self.event.bot = FakeBot(self.fail_images)
        results = await self.consume()
        self.assertEqual([r.method for r in requests], ["GET", "GET"])
        self.assertTrue(
            all(r.headers["Referer"] == "https://online-fix.me/" for r in requests)
        )
        self.assertEqual(len(self.sent()), 2)
        self.assertFalse(has_images(self.sent()[1]))
        self.assertIn("纯文字游戏列表", results[-1])

    async def test_disable_posters_avoids_all_image_downloads_and_uploads(self):
        self.config["send_game_posters"] = False
        await self.consume()
        self.plugin._prepare_image.assert_not_awaited()
        self.assertFalse(has_images(self.sent()[0]))
        self.assertEqual(len(self.sent()), 1)

    async def test_disable_fallback_preserves_specific_error(self):
        self.config["fallback_to_text_on_upload_failure"] = False
        self.event.bot = FakeBot(self.fail_images)
        results = await self.consume()
        self.assertEqual(len(self.sent()), 1)
        self.assertIn("QQ 图片上传连接超时", results[-1])

    async def test_onebot_timeout_does_not_duplicate_forward(self):
        def timeout(action, payload):
            raise TimingError("awaiting result")

        self.event.bot = FakeBot(timeout)
        results = await self.consume()
        self.assertEqual(len(self.sent()), 1)
        self.assertIn("尚不确定", results[-1])

    async def test_failed_text_fallback_does_not_retry_indefinitely(self):
        def fail_all(action, payload):
            raise ActionFailed(HIGHWAY_ERROR)

        self.event.bot = FakeBot(fail_all)
        results = await self.consume()
        self.assertEqual(len(self.sent()), 2)
        self.assertFalse(has_images(self.sent()[1]))
        self.assertIn("上传连接超时", results[-1])

    async def test_text_only_send_failure_is_not_retried(self):
        self.config["send_game_posters"] = False
        self.event.bot = FakeBot(
            lambda action, payload: (_ for _ in ()).throw(ActionFailed(HIGHWAY_ERROR))
        )
        await self.consume()
        self.assertEqual(len(self.sent()), 1)

    async def test_unknown_action_failure_does_not_trigger_media_fallback(self):
        def fail(action, payload):
            raise ActionFailed("invalid group id", 1400)

        self.event.bot = FakeBot(fail)
        results = await self.consume()
        self.assertEqual(len(self.sent()), 1)
        self.assertIn("retcode=1400", results[-1])

    async def test_returned_failed_envelope_is_handled(self):
        def fail_images(action, payload):
            if has_images(payload):
                return ActionFailed(HIGHWAY_ERROR).result

        self.event.bot = FakeBot(fail_images)
        await self.consume()
        self.assertEqual(len(self.sent()), 2)
        self.assertFalse(has_images(self.sent()[1]))

    async def test_private_list_does_not_download_posters(self):
        self.event.group_id = ""
        results = await self.consume()
        self.plugin._prepare_image.assert_not_awaited()
        self.assertEqual(self.event.bot.calls, [])
        self.assertIn("第 1/5 页", results[-1])

    async def test_list_paging_and_item_limit(self):
        self.config["max_display_items"] = 1
        await self.consume("/game list 3")
        self.assertEqual(self.scraper.calls, [("list", 3)])
        self.assertEqual(len(self.sent()[0]["messages"]), 1)

    async def test_empty_list_and_search_have_clear_reply(self):
        self.scraper.results = []
        self.assertIn("暂无游戏", (await self.consume())[-1])
        self.assertIn("未找到", (await self.consume("/game info missing"))[-1])

    async def test_no_poster_url_and_download_failure_are_text_only(self):
        self.scraper.results[0].poster_url = None
        self.plugin._prepare_image.return_value = None
        await self.consume()
        self.assertFalse(has_images(self.sent()[0]))
        self.assertEqual(self.plugin._prepare_image.await_count, 1)

    async def test_multi_result_search_uses_same_safe_fallback(self):
        self.event.bot = FakeBot(self.fail_images)
        results = await self.consume("/game info Game")
        self.assertEqual(len(self.sent()), 2)
        self.assertFalse(has_images(self.sent()[1]))
        self.assertIn("纯文字游戏信息", results[-1])

    async def test_single_detail_preserves_player_counts_and_reply(self):
        self.scraper.results = games(1)
        await self.consume("/game info Game")
        message = self.sent("send_group_msg")[0]["message"]
        self.assertEqual([s["type"] for s in message], ["reply", "image", "text"])
        self.assertIn("合作人数4", message[-1]["data"]["text"])
        self.assertIn("多人人数8", message[-1]["data"]["text"])
        self.assertEqual(
            self.scraper.calls[-1], ("players", "https://online-fix.me/games/0.html")
        )

    async def test_single_detail_upload_failure_uses_text_without_reply(self):
        self.scraper.results = games(1)
        self.event.bot = FakeBot(self.fail_images)
        await self.consume("/game info Game")
        calls = self.sent("send_group_msg")
        self.assertEqual(len(calls), 2)
        self.assertEqual([s["type"] for s in calls[1]["message"]], ["text"])
        self.assertIn("合作人数4", calls[1]["message"][0]["data"]["text"])

    async def test_single_detail_timeout_does_not_duplicate_reply(self):
        self.scraper.results = games(1)
        self.event.bot = FakeBot(
            lambda action, payload: (_ for _ in ()).throw(TimingError("timeout"))
        )
        results = await self.consume("/game info Game")
        self.assertEqual(len(self.sent("send_group_msg")), 1)
        self.assertIn("尚不确定", results[-1])

    async def test_single_detail_posters_can_be_disabled(self):
        self.scraper.results = games(1)
        self.config["send_game_posters"] = False
        await self.consume("/game info Game")
        self.plugin._prepare_image.assert_not_awaited()
        self.assertFalse(has_images(self.sent("send_group_msg")[0]))

    async def test_private_single_detail_does_not_download_images(self):
        self.scraper.results = games(1)
        self.event.group_id = ""
        results = await self.consume("/game info Game")
        self.plugin._prepare_image.assert_not_awaited()
        self.assertIn("合作人数4", results[-1])
        self.assertEqual(self.event.bot.calls, [])

    async def test_private_search_returns_all_result_titles(self):
        self.event.group_id = ""
        results = await self.consume("/game info Game")
        self.assertIn("Game 0", results[-1])
        self.assertIn("Game 1", results[-1])
        self.plugin._prepare_image.assert_not_awaited()

    async def test_player_count_failure_does_not_abort_detail(self):
        self.scraper.results = games(1)
        self.scraper.fetch_player_counts = AsyncMock(
            side_effect=RuntimeError("private diagnostic")
        )
        await self.consume("/game info Game")
        self.assertIn(
            "Game 0", self.sent("send_group_msg")[0]["message"][-1]["data"]["text"]
        )

    async def test_generic_error_is_not_echoed_to_qq(self):
        self.scraper.fetch_list = AsyncMock(
            side_effect=RuntimeError("Cookie=private-cookie")
        )
        results = await self.consume()
        self.assertNotIn("private-cookie", results[-1])
        self.assertIn("获取游戏信息失败", results[-1])

    async def test_subscription_commands_preserve_existing_storage(self):
        self.assertIn("已订阅", (await self.consume("/game subscribe"))[-1])
        self.assertEqual(self.plugin.kv["subscriptions"], ["123"])
        self.assertIn("本群已订阅", (await self.consume("/game subscribe"))[-1])
        self.assertIn("已取消", (await self.consume("/game unsubscribe"))[-1])
        self.assertEqual(self.plugin.kv["subscriptions"], [])

    async def test_push_single_poster_failure_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "plugin_data" / "astrbot_plugin_getGameInfo"
            folder.mkdir(parents=True)
            (folder / "seen_games.json").write_text(json.dumps({"onlinefix": ["old"]}))
            bot = FakeBot(self.fail_images, self_id="789")
            self.plugin._get_bot = lambda *args: bot
            with patch.object(main, "get_astrbot_data_path", return_value=directory):
                await self.plugin._push_new_games(["123"], games(1))
            calls = [p for action, p in bot.calls if action == "send_group_msg"]
            self.assertEqual(len(calls), 2)
            self.assertFalse(has_images(calls[1]))
            self.assertIn("新游戏上架", calls[1]["message"][0]["data"]["text"])

    async def test_push_multi_resolves_identity_and_preserves_other_groups_images(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "plugin_data" / "astrbot_plugin_getGameInfo"
            folder.mkdir(parents=True)
            (folder / "seen_games.json").write_text(json.dumps({"onlinefix": ["old"]}))

            def fail_first_group(action, payload):
                if (
                    action == "send_group_forward_msg"
                    and payload["group_id"] == 123
                    and has_images(payload)
                ):
                    raise ActionFailed(HIGHWAY_ERROR)

            bot = FakeBot(fail_first_group)
            self.plugin._get_bot = lambda *args: bot
            with patch.object(main, "get_astrbot_data_path", return_value=directory):
                await self.plugin._push_new_games(["123", "456"], games())
            self.assertEqual(bot.calls[0][0], "get_login_info")
            calls = [p for action, p in bot.calls if action == "send_group_forward_msg"]
            self.assertEqual(len(calls), 3)
            self.assertFalse(has_images(calls[1]))
            self.assertTrue(has_images(calls[2]))
            self.assertEqual(calls[0]["messages"][0]["data"]["user_id"], "789")

    async def test_push_initializes_seen_without_sending_old_games(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(main, "get_astrbot_data_path", return_value=directory):
                await self.plugin._push_new_games(["123"], games())
            path = (
                Path(directory)
                / "plugin_data"
                / "astrbot_plugin_getGameInfo"
                / "seen_games.json"
            )
            self.assertEqual(json.loads(path.read_text())["onlinefix"], ["0", "1"])
            self.plugin._prepare_image.assert_not_awaited()

    async def test_initialize_and_terminate_without_scraping_live_site(self):
        with patch.dict(main.SCRAPER_REGISTRY, {"onlinefix": FakeScraper}):
            await self.plugin.initialize()
        scraper = self.plugin._scraper
        self.assertIsNotNone(self.plugin._poll_new_task)
        await self.plugin.terminate()
        self.assertTrue(scraper.closed)
        self.assertEqual(scraper.calls, [])

    async def test_private_subscription_is_rejected(self):
        self.event.group_id = ""
        self.assertIn("仅支持在群聊", (await self.consume("/game subscribe"))[-1])
        self.assertNotIn("subscriptions", self.plugin.kv)

    async def test_missing_command_arguments_have_usage(self):
        self.assertIn("用法", (await self.consume("/game"))[-1])
        self.assertIn("用法", (await self.consume("/game info"))[-1])

    async def test_terminate_awaits_polling_task_and_closes_resources(self):
        self.plugin._poll_new_task = asyncio.create_task(asyncio.sleep(100))
        await self.plugin.terminate()
        self.assertTrue(self.plugin._poll_new_task.done())
        self.assertTrue(self.plugin._image_client.is_closed)
        self.assertTrue(self.scraper.closed)


class PosterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.plugin = main.GameDownPlugin(SimpleNamespace(), {"request_timeout": 30})
        self.addAsyncCleanup(self.plugin.terminate)
        buffer = io.BytesIO()
        Image.new("RGB", (250, 165), "blue").save(buffer, format="JPEG")
        self.image = buffer.getvalue()

    async def transport(self, handler):
        await self.plugin._image_client.aclose()
        self.plugin._image_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )

    async def test_single_get_shared_client_headers_and_valid_base64(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(
                200, content=self.image, headers={"Content-Type": "image/jpeg"}
            )

        await self.transport(handler)
        for _ in range(2):
            image = await self.plugin._prepare_image(
                "https://posters.example/poster.jpg", "Game"
            )
            self.assertEqual(
                base64.b64decode(image.removeprefix("base64://")), self.image
            )
        self.assertEqual([r.method for r in requests], ["GET", "GET"])
        self.assertTrue(
            all(r.headers["Referer"] == "https://online-fix.me/" for r in requests)
        )

    async def test_html_response_does_not_enter_qq_image_upload(self):
        await self.transport(
            lambda request: httpx.Response(200, text="<html>denied</html>")
        )
        self.assertIsNone(
            await self.plugin._prepare_image("https://posters.example/x", "Game")
        )

    async def test_http_failure_falls_back_without_echoing_private_error(self):
        await self.transport(
            lambda request: httpx.Response(403, text="Cookie=private-cookie")
        )
        with self.assertLogs("getgameinfo-tests", level="WARNING") as logs:
            result = await self.plugin._prepare_image(
                "https://posters.example/x", "Game"
            )
        self.assertIsNone(result)
        self.assertNotIn("private-cookie", "\n".join(logs.output))

    async def test_poster_byte_limit(self):
        await self.transport(
            lambda request: httpx.Response(200, content=b"x" * (5 * 1024 * 1024 + 1))
        )
        self.assertIsNone(
            await self.plugin._prepare_image("https://posters.example/x", "Game")
        )

    async def test_download_timeout_falls_back(self):
        def handler(request):
            raise httpx.ReadTimeout("private diagnostic", request=request)

        await self.transport(handler)
        self.assertIsNone(
            await self.plugin._prepare_image("https://posters.example/x", "Game")
        )
