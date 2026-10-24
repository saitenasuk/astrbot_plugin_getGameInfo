"""Offline AstrBot/OneBot doubles; do not contact QQ or the game website."""

from __future__ import annotations

import importlib
import logging
import sys
from copy import deepcopy
from pathlib import Path
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


class ActionFailed(Exception):
    def __init__(self, wording, retcode=100):
        self.result = {
            "status": "failed",
            "retcode": retcode,
            "data": None,
            "wording": wording,
        }
        super().__init__(self.result)


class TimingError(Exception):
    pass


HIGHWAY_ERROR = (
    "highway upload transport failed after 3 attempts "
    "(cmdId=1004 chunk=31175/31175 offset=0): Error: TCP connect timeout"
)


def has_images(payload):
    if "messages" in payload:
        return any(
            has_images({"message": n["data"]["content"]}) for n in payload["messages"]
        )
    return any(s["type"] == "image" for s in payload.get("message", []))


class FakeBot:
    def __init__(self, handler=None, self_id=None):
        self.calls = []
        self.handler = handler
        if self_id is not None:
            self.self_id = self_id

    async def call_action(self, action, **payload):
        self.calls.append((action, deepcopy(payload)))
        if self.handler:
            result = self.handler(action, payload)
            if result is not None:
                return result
        if action == "get_login_info":
            return {"user_id": 789}
        return {"message_id": 10001}


class FakeEvent:
    def __init__(
        self,
        text="/game list",
        *,
        group_id="123",
        bot=None,
        platform_id="test-platform",
        self_id="789",
    ):
        self.message_str = text
        self.group_id = group_id
        self.bot = bot or FakeBot()
        self.platform_id = platform_id
        self.self_id = self_id
        self.unified_msg_origin = (
            f"{platform_id}:GroupMessage:{group_id}" if platform_id else ""
        )
        self.message_obj = SimpleNamespace(message_id="request-message")

    def get_group_id(self):
        return self.group_id

    def get_self_id(self):
        return self.self_id

    def get_platform_id(self):
        return self.platform_id

    def plain_result(self, text):
        return text


class FakeAdapter:
    def __init__(self, bot, platform_id=None, config=None):
        self.bot = bot
        self.config = config or {}
        self.platform_id = platform_id

    def meta(self):
        return SimpleNamespace(name="aiocqhttp", id=self.platform_id)

    def get_client(self):
        return self.bot


class FakeResponse:
    """Stand-in for astrbot.api.web JSON responses in unit tests."""

    def __init__(self, data, status_code=200):
        self.data = data
        self.status_code = status_code


def json_response(data=None, *, status_code=200, headers=None):
    return FakeResponse({} if data is None else data, status_code)


def error_response(message, *, status_code=400, data=None, headers=None):
    return FakeResponse(
        {"status": "error", "message": message, "data": data}, status_code
    )


class FakeStar:
    def __init__(self, context):
        self.context = context
        self.kv = {}

    async def get_kv_data(self, key, default=None):
        return deepcopy(self.kv.get(key, default))

    async def put_kv_data(self, key, value):
        self.kv[key] = deepcopy(value)


def load_plugin():
    package_name = "_test_getgameinfo"
    package = ModuleType(package_name)
    package.__path__ = [str(ROOT)]
    sys.modules[package_name] = package
    stubs = {}
    for name in (
        "astrbot",
        "astrbot.api",
        "astrbot.api.event",
        "astrbot.api.star",
        "astrbot.api.web",
        "astrbot.core",
        "astrbot.core.utils",
        "astrbot.core.utils.astrbot_path",
        "astrbot.core.platform",
        "astrbot.core.platform.sources",
        "astrbot.core.platform.sources.aiocqhttp",
        "astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter",
    ):
        stubs[name] = ModuleType(name)
    stubs["astrbot.api"].AstrBotConfig = dict
    stubs["astrbot.api"].logger = logging.getLogger("getgameinfo-tests")
    stubs["astrbot.api.web"].json_response = json_response
    stubs["astrbot.api.web"].error_response = error_response
    stubs["astrbot.api.web"].request = None
    stubs["astrbot.api.event"].AstrMessageEvent = FakeEvent
    stubs["astrbot.api.event"].filter = SimpleNamespace(
        PermissionType=SimpleNamespace(ADMIN="admin"),
        permission_type=lambda _: lambda fn: fn,
        command=lambda _: lambda fn: fn,
    )
    stubs["astrbot.api.star"].Context = object
    stubs["astrbot.api.star"].Star = FakeStar
    stubs["astrbot.core.utils.astrbot_path"].get_astrbot_data_path = lambda: (
        "/unused-test-data"
    )
    stubs[
        "astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter"
    ].AiocqhttpAdapter = FakeAdapter
    old = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        return importlib.import_module(f"{package_name}.main")
    finally:
        for name, previous in old.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


main = load_plugin()


def games(count=2):
    return [
        main.GameBrief(
            id=str(index),
            title=f"Game {index}",
            url=f"/games/{index}.html",
            poster_url=f"https://posters.example/{index}.jpg",
            platforms=["Steam"],
            modes=["合作"],
        )
        for index in range(count)
    ]


class FakeScraper:
    site_name = "Online-Fix.me"

    def __init__(self, results=None, *, timeout=None):
        self.results = results if results is not None else games()
        self.calls = []
        self.closed = False

    async def fetch_list(self, page=1):
        self.calls.append(("list", page))
        return self.results, 5

    async def search(self, query, page=1):
        self.calls.append(("search", query))
        return self.results, 5

    async def fetch_player_counts(self, url):
        self.calls.append(("players", url))
        return 4, 8

    async def close(self):
        self.closed = True
