import functools
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.support import (
    HIGHWAY_ERROR,
    ActionFailed,
    FakeAdapter,
    FakeBot,
    FakeScraper,
    games,
    main,
)

webui = main.webui
PLUGIN_PATH = f"/{webui.PLUGIN_NAME}"


class ViewContext:
    """Fake AstrBot context that records registered view APIs."""

    def __init__(self, insts, profiles=None):
        self.platform_manager = SimpleNamespace(platform_insts=insts)
        self.profiles = profiles or {}
        self.routes = {}

    def register_web_api(self, route, handler, methods, desc):
        self.routes[route] = {
            "handler": handler,
            "methods": methods,
            "desc": desc,
        }

    def get_config(self, umo=None):
        return {"plugin_set": self.profiles.get(umo or "", ["*"])}


class FakeRequest:
    def __init__(self, query=None, body=None):
        self.query = query or {}
        self.body = {} if body is None else body

    async def json(self, default=None):
        return self.body


class ViewApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot_a = FakeBot(self_id="111")
        self.bot_b = FakeBot(self_id="222")
        self.context = ViewContext(
            [FakeAdapter(self.bot_b, "bot-b"), FakeAdapter(self.bot_a, "bot-a")],
            {
                "bot-a:GroupMessage:123": ["astrbot_plugin_getGameInfo"],
                "bot-b:GroupMessage:123": ["another_plugin"],
                "bot-b:GroupMessage:456": ["another_plugin"],
            },
        )
        self.plugin = main.GameDownPlugin(
            self.context,
            {
                "request_timeout": 30,
                "max_display_items": 21,
                "game_check_interval": 21600,
                "send_game_posters": True,
                "fallback_to_text_on_upload_failure": True,
            },
        )
        self.plugin.name = "astrbot_plugin_getGameInfo"
        self.plugin._scraper = FakeScraper()
        self.plugin._prepare_image = AsyncMock(return_value="base64://test-image")
        self.addAsyncCleanup(self.plugin.terminate)

    def route(self, path):
        return self.context.routes[f"{PLUGIN_PATH}{path}"]["handler"]

    async def call(self, path, query=None, body=None):
        with patch.object(webui, "request", FakeRequest(query=query, body=body)):
            return await self.route(path)()

    def test_registers_all_routes(self):
        expected_methods = {
            f"{PLUGIN_PATH}/status": ["GET"],
            f"{PLUGIN_PATH}/health": ["GET"],
            f"{PLUGIN_PATH}/history": ["GET"],
            f"{PLUGIN_PATH}/diagnostics": ["GET"],
            f"{PLUGIN_PATH}/groups": ["GET"],
            f"{PLUGIN_PATH}/subscriptions": ["GET"],
            f"{PLUGIN_PATH}/events": ["GET"],
            f"{PLUGIN_PATH}/games": ["GET"],
            f"{PLUGIN_PATH}/games/search": ["GET"],
            f"{PLUGIN_PATH}/games/detail": ["GET"],
            f"{PLUGIN_PATH}/check": ["POST"],
            f"{PLUGIN_PATH}/subscriptions/add": ["POST"],
            f"{PLUGIN_PATH}/subscriptions/remove": ["POST"],
            f"{PLUGIN_PATH}/subscriptions/test": ["POST"],
            f"{PLUGIN_PATH}/maintenance/seen": ["POST"],
        }
        self.assertEqual(set(self.context.routes), set(expected_methods))
        for path, item in self.context.routes.items():
            self.assertTrue(callable(item["handler"]))
            self.assertEqual(item["methods"], expected_methods[path])

    async def test_status_summary_includes_accounts_and_schedule(self):
        self.plugin.kv["subscriptions"] = [
            {"group_id": "123", "platform_id": "bot-a", "self_id": "111"},
            {"group_id": "456", "platform_id": "bot-b", "self_id": "222"},
        ]
        self.plugin.status.next_check_at = (
            datetime.now().astimezone() + timedelta(hours=1)
        ).isoformat(timespec="seconds")
        self.plugin.status.record_delivery("bot-a", "123", ok=True)
        self.plugin.status.last_check = {
            "ok": True,
            "trigger": "schedule",
            "dry_run": False,
            "finished_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
            "duration_ms": 1200,
        }
        response = await self.call("/status")
        data = response.data
        self.assertEqual(data["counts"]["subscriptions"], 2)
        self.assertEqual(data["counts"]["groups"], 2)
        self.assertEqual(data["counts"]["accounts"], 2)
        self.assertEqual(data["schedule"]["interval_seconds"], 21600)
        self.assertGreater(data["schedule"]["seconds_until_next"], 0)
        self.assertEqual(len(data["accounts"]), 2)
        self.assertTrue(data["config"]["send_game_posters"])
        self.assertEqual(data["health"]["reachable"], True)
        self.assertEqual(data["health"]["status_code"], 200)
        self.assertEqual(len(data["history"]), 7)
        self.assertIn("astrbot_version", data["plugin"])
        by_account = {item["platform_id"]: item for item in data["accounts"]}
        self.assertTrue(by_account["bot-a"]["last_delivery"]["ok"])

    async def test_subscriptions_reports_ready_legacy_and_blocked_states(self):
        self.plugin.kv["subscriptions"] = [
            {"group_id": "123", "platform_id": "bot-a", "self_id": "111"},
            {"group_id": "456", "platform_id": "bot-b", "self_id": "222"},
            "789",
        ]
        response = await self.call("/subscriptions")
        data = response.data
        by_group = {item["group_id"]: item for item in data["items"]}
        self.assertEqual(by_group["123"]["state"], "ready")
        self.assertEqual(by_group["123"]["state_label"], "可推送")
        self.assertEqual(by_group["456"]["state"], "plugin_disabled")
        self.assertEqual(by_group["789"]["state"], "legacy")
        self.assertEqual(data["summary"]["total"], 3)
        self.assertEqual(data["summary"]["ready"], 1)
        self.assertEqual(data["summary"]["legacy"], 1)
        self.assertEqual(data["summary"]["blocked"], 1)

    async def test_dry_run_does_not_send_or_change_seen(self):
        self.plugin._scraper.results = games(2)
        with tempfile.TemporaryDirectory() as directory:
            data_dir = (
                Path(directory) / "plugin_data" / "astrbot_plugin_getGameInfo"
            )
            data_dir.mkdir(parents=True)
            seen_path = data_dir / "seen_games.json"
            seen_path.write_text(json.dumps({"onlinefix": ["old"]}))
            with patch.object(main, "get_astrbot_data_path", return_value=directory):
                response = await self.call("/check", body={"mode": "dry_run"})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.data["ok"])
            self.assertEqual(len(response.data["new_games"]), 2)
            self.assertEqual(
                json.loads(seen_path.read_text()), {"onlinefix": ["old"]}
            )
        self.assertEqual(self.bot_a.calls, [])
        self.assertEqual(self.bot_b.calls, [])
        self.assertTrue(self.plugin.status.last_check["dry_run"])

    async def test_dry_run_reports_missing_baseline(self):
        self.plugin._scraper.results = games(2)
        with tempfile.TemporaryDirectory() as directory, patch.object(
            main, "get_astrbot_data_path", return_value=directory
        ):
            response = await self.call("/check", body={"mode": "dry_run"})
        self.assertTrue(response.data["baseline_missing"])
        self.assertEqual(response.data["new_games"], [])

    async def test_push_check_sends_to_subscribed_account(self):
        self.plugin.kv["subscriptions"] = [
            {"group_id": "123", "platform_id": "bot-a", "self_id": "111"},
            {"group_id": "456", "platform_id": "bot-b", "self_id": "222"},
        ]
        self.plugin._scraper.results = games(1)
        with tempfile.TemporaryDirectory() as directory, patch.object(
            main, "get_astrbot_data_path", return_value=directory
        ):
            data_dir = Path(directory) / "plugin_data" / "astrbot_plugin_getGameInfo"
            data_dir.mkdir(parents=True)
            seen_path = data_dir / "seen_games.json"
            seen_path.write_text(json.dumps({"onlinefix": ["old"]}))
            response = await self.call("/check", body={"mode": "push"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["pushes"], 1)
            self.assertEqual(response.data["failures"], 0)
            self.assertEqual(response.data["skipped"], 1)
            self.assertEqual(
                json.loads(seen_path.read_text())["onlinefix"], ["0"]
            )
        actions_a = [action for action, _ in self.bot_a.calls]
        self.assertIn("send_group_msg", actions_a)
        self.assertEqual(self.bot_b.calls, [])
        self.assertFalse(self.plugin.status.last_check["dry_run"])
        self.assertTrue(
            any(event["kind"] == "push_ok" for event in self.plugin.status.events())
        )

    async def test_push_check_requires_subscriptions(self):
        response = await self.call("/check", body={"mode": "push"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("订阅", response.data["message"])

    async def test_remove_subscription_removes_group(self):
        self.plugin.kv["subscriptions"] = [
            {"group_id": "123", "platform_id": "bot-a", "self_id": "111"},
            {"group_id": "456", "platform_id": "bot-b", "self_id": "222"},
        ]
        response = await self.call(
            "/subscriptions/remove", body={"group_id": "123"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["removed"], 1)
        self.assertEqual(
            [item["group_id"] for item in self.plugin.kv["subscriptions"]], ["456"]
        )
        missing = await self.call(
            "/subscriptions/remove", body={"group_id": "123"}
        )
        self.assertEqual(missing.status_code, 404)
        self.assertTrue(
            any(event["kind"] == "unsubscribe" for event in self.plugin.status.events())
        )

    async def test_remove_subscription_validates_group_id(self):
        response = await self.call(
            "/subscriptions/remove", body={"group_id": "abc"}
        )
        self.assertEqual(response.status_code, 400)

    async def test_test_push_uses_subscribed_account(self):
        self.plugin.kv["subscriptions"] = [
            {"group_id": "123", "platform_id": "bot-a", "self_id": "111"}
        ]
        response = await self.call(
            "/subscriptions/test", body={"group_id": "123"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["ok"])
        calls = [payload for action, payload in self.bot_a.calls if action == "send_group_msg"]
        self.assertEqual(calls[0]["group_id"], 123)
        self.assertIn("测试消息", calls[0]["message"][0]["data"]["text"])
        self.assertTrue(
            any(event["kind"] == "push_test" for event in self.plugin.status.events())
        )

    async def test_test_push_rejects_blocked_or_unknown_subscription(self):
        self.plugin.kv["subscriptions"] = [
            {"group_id": "456", "platform_id": "bot-b", "self_id": "222"}
        ]
        blocked = await self.call(
            "/subscriptions/test", body={"group_id": "456"}
        )
        self.assertEqual(blocked.status_code, 409)
        unknown = await self.call(
            "/subscriptions/test", body={"group_id": "999"}
        )
        self.assertEqual(unknown.status_code, 404)

    async def test_check_returns_conflict_when_lock_is_held(self):
        await self.plugin._check_lock.acquire()
        try:
            response = await self.call("/check", body={"mode": "dry_run"})
        finally:
            self.plugin._check_lock.release()
        self.assertEqual(response.status_code, 409)

    async def test_manual_check_cooldown(self):
        self.plugin._scraper.results = games(1)
        with tempfile.TemporaryDirectory() as directory, patch.object(
            main, "get_astrbot_data_path", return_value=directory
        ):
            first = await self.call("/check", body={"mode": "dry_run"})
            second = await self.call("/check", body={"mode": "dry_run"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)

    async def test_games_marks_new_and_limits_items(self):
        self.plugin._scraper.results = games(3)
        with tempfile.TemporaryDirectory() as directory:
            data_dir = (
                Path(directory) / "plugin_data" / "astrbot_plugin_getGameInfo"
            )
            data_dir.mkdir(parents=True)
            (data_dir / "seen_games.json").write_text(
                json.dumps({"onlinefix": ["0"]})
            )
            with patch.object(main, "get_astrbot_data_path", return_value=directory):
                response = await self.call("/games", query={"limit": "2"})
        data = response.data
        self.assertEqual(len(data["items"]), 2)
        self.assertFalse(data["items"][0]["is_new"])
        self.assertTrue(data["items"][1]["is_new"])
        self.assertEqual(data["page"], 1)
        self.assertEqual(data["total_pages"], 5)

    async def test_game_search_validates_query_and_returns_results(self):
        self.plugin._scraper.results = games(1)
        empty = await self.call("/games/search", query={"q": ""})
        self.assertEqual(empty.status_code, 400)
        response = await self.call("/games/search", query={"q": "Game"})
        self.assertEqual(response.data["query"], "Game")
        self.assertEqual(len(response.data["items"]), 1)

    async def test_game_detail_rejects_external_urls_and_accepts_site_url(self):
        bad = await self.call(
            "/games/detail", query={"url": "https://evil.example/games/1.html"}
        )
        self.assertEqual(bad.status_code, 400)
        response = await self.call(
            "/games/detail",
            query={"url": "https://online-fix.me/games/1-game.html?x=1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["coop_players"], 4)
        self.assertEqual(response.data["multi_players"], 8)
        self.assertEqual(response.data["url"], "https://online-fix.me/games/1-game.html")

    async def test_events_filter_by_category(self):
        self.plugin.status.add_event("info", "check_ok", "检查完成")
        self.plugin.status.add_event(
            "warning", "push_fail", "群 123 推送失败", group_id="123"
        )
        push_events = await self.call("/events", query={"category": "push"})
        check_events = await self.call("/events", query={"category": "check"})
        error_events = await self.call("/events", query={"category": "error"})
        self.assertEqual(len(push_events.data["items"]), 1)
        self.assertEqual(len(check_events.data["items"]), 1)
        self.assertEqual(len(error_events.data["items"]), 0)

    async def test_check_returns_503_before_initialize(self):
        plugin = main.GameDownPlugin(self.context, {})
        plugin._scraper = None
        self.addAsyncCleanup(plugin.terminate)
        with patch.object(webui, "request", FakeRequest(body={"mode": "dry_run"})):
            response = await self.context.routes[f"{PLUGIN_PATH}/check"]["handler"]()
        self.assertEqual(response.status_code, 503)

    async def test_health_and_history_endpoints(self):
        health = await self.call("/health")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.data["reachable"])
        self.assertEqual(health.data["status_code"], 200)
        history = await self.call("/history", query={"days": "3"})
        self.assertEqual(len(history.data["days"]), 3)
        self.assertIn("avg_duration_ms", history.data["days"][0])

    async def test_diagnostics_contains_status_and_seen(self):
        self.plugin.kv["subscriptions"] = [
            {"group_id": "123", "platform_id": "bot-a", "self_id": "111"}
        ]
        with tempfile.TemporaryDirectory() as directory, patch.object(
            main, "get_astrbot_data_path", return_value=directory
        ):
            data_dir = Path(directory) / "plugin_data" / "astrbot_plugin_getGameInfo"
            data_dir.mkdir(parents=True)
            (data_dir / "seen_games.json").write_text(
                json.dumps({"onlinefix": ["1", "2"]})
            )
            response = await self.call("/diagnostics")
        data = response.data
        self.assertEqual(data["seen_games"], ["1", "2"])
        self.assertIn("subscriptions", data)
        self.assertIn("events", data)
        self.assertIn("history", data)
        self.assertNotIn("base64", json.dumps(data))

    async def test_maintenance_reset_and_clear_seen(self):
        self.plugin._scraper.results = games(2)
        with tempfile.TemporaryDirectory() as directory, patch.object(
            main, "get_astrbot_data_path", return_value=directory
        ):
            seen_path = (
                Path(directory)
                / "plugin_data"
                / "astrbot_plugin_getGameInfo"
                / "seen_games.json"
            )
            bad = await self.call(
                "/maintenance/seen", body={"action": "reset", "confirm": "CLEAR"}
            )
            self.assertEqual(bad.status_code, 400)
            reset = await self.call(
                "/maintenance/seen", body={"action": "reset", "confirm": "RESET"}
            )
            self.assertEqual(reset.status_code, 200)
            self.assertEqual(reset.data["tracked_games"], 2)
            self.assertEqual(
                json.loads(seen_path.read_text()), {"onlinefix": ["0", "1"]}
            )
            cleared = await self.call(
                "/maintenance/seen", body={"action": "clear", "confirm": "CLEAR"}
            )
            self.assertEqual(cleared.status_code, 200)
            self.assertTrue(json.loads(seen_path.read_text())["force_push_all"])

    async def test_clear_seen_makes_next_push_send_all_games(self):
        self.plugin.kv["subscriptions"] = [
            {"group_id": "123", "platform_id": "bot-a", "self_id": "111"}
        ]
        self.plugin._scraper.results = games(2)
        with tempfile.TemporaryDirectory() as directory, patch.object(
            main, "get_astrbot_data_path", return_value=directory
        ):
            await self.call(
                "/maintenance/seen", body={"action": "clear", "confirm": "CLEAR"}
            )
            summary = await self.plugin._push_new_games(
                self.plugin.kv["subscriptions"], games(2)
            )
            self.assertEqual(summary["pushes"], 1)
            seen = json.loads(
                (
                    Path(directory)
                    / "plugin_data"
                    / "astrbot_plugin_getGameInfo"
                    / "seen_games.json"
                ).read_text()
            )
            self.assertEqual(seen["onlinefix"], ["0", "1"])
            self.assertNotIn("force_push_all", seen)

    async def test_groups_and_add_subscription(self):
        def handler(action, payload):
            if action == "get_group_list":
                return {"data": [{"group_id": 123, "group_name": "Group A"}]}

        self.bot_a.handler = handler
        # aiocqhttp exposes bot.self_id as an API partial; it must not leak into
        # the account card or the stored subscription.
        self.bot_a.self_id = functools.partial(lambda: None)
        groups = await self.call("/groups", query={"platform_id": "bot-a"})
        self.assertEqual(groups.status_code, 200)
        self.assertEqual(groups.data["items"], [{"group_id": "123", "group_name": "Group A"}])
        added = await self.call(
            "/subscriptions/add",
            body={"group_id": "123", "platform_id": "bot-a"},
        )
        self.assertEqual(added.status_code, 200)
        self.assertTrue(added.data["changed"])
        self.assertEqual(self.plugin.kv["subscriptions"][0]["platform_id"], "bot-a")
        self.assertEqual(self.plugin.kv["subscriptions"][0]["self_id"], "789")
        missing = await self.call(
            "/subscriptions/add",
            body={"group_id": "456", "platform_id": "bot-a"},
        )
        self.assertEqual(missing.status_code, 400)

    async def test_status_hides_cqhttp_callable_self_id(self):
        self.bot_a.self_id = functools.partial(lambda: None)
        response = await self.call("/status")
        account = next(
            item
            for item in response.data["accounts"]
            if item["platform_id"] == "bot-a"
        )
        self.assertIsNone(account["self_id"])
        self.assertNotIn("functools.partial", json.dumps(response.data))

    async def test_test_push_failure_records_detail_for_retry(self):
        self.plugin.kv["subscriptions"] = [
            {"group_id": "123", "platform_id": "bot-a", "self_id": "111"}
        ]

        def fail(action, payload):
            if action == "send_group_msg":
                raise ActionFailed(HIGHWAY_ERROR)

        self.bot_a.handler = fail
        response = await self.call(
            "/subscriptions/test", body={"group_id": "123"}
        )
        self.assertEqual(response.status_code, 502)
        event = next(
            item
            for item in self.plugin.status.events()
            if item["kind"] == "push_test"
        )
        self.assertEqual(event["reason"], "HIGHWAY_CONNECT_TIMEOUT")
        self.assertIn("TCP connect timeout", event["detail"])

    async def test_unexpected_values_still_produce_safe_status(self):
        response = await self.call("/status")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["last_check"])
        self.assertEqual(response.data["counts"]["subscriptions"], 0)
