"""WebUI view APIs for astrbot_plugin_getGameInfo.

The view lives in ``pages/status/`` and talks to these handlers through
``window.AstrBotPluginView``. AstrBot's Dashboard supplies authentication;
handlers only validate and shape data.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlparse, urlunparse

try:  # AstrBot >= 4.26
    from astrbot.api.web import error_response, json_response, request
except (ImportError, AttributeError):  # pragma: no cover - AstrBot 4.25 fallback
    from quart import jsonify, request  # type: ignore[no-redef]

    def json_response(  # type: ignore[misc]
        data: Any = None,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> Any:
        response = jsonify({} if data is None else data)
        response.status_code = status_code
        if headers:
            response.headers.update(headers)
        return response

    def error_response(  # type: ignore[misc]
        message: str,
        *,
        status_code: int = 400,
        data: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return json_response(
            {"status": "error", "message": message, "data": data},
            status_code=status_code,
            headers=headers,
        )

from .onebot_sender import OneBotActionError, call_action
from .status import CheckBusyError

try:
    from astrbot.api import logger
except ImportError:  # pragma: no cover - tests import through the plugin loader
    logger = logging.getLogger(__name__)

PLUGIN_NAME = "astrbot_plugin_getGameInfo"
SITE_URL = "https://online-fix.me"
GAME_CACHE_TTL = 60.0
MANUAL_CHECK_COOLDOWN = 10.0
TEST_PUSH_COOLDOWN = 10.0
MAX_LIST_LIMIT = 50
MAX_EVENT_LIMIT = 200

STATE_LABELS = {
    "ready": "可推送",
    "legacy": "旧订阅，将自动迁移",
    "plugin_disabled": "插件未启用（配置文件或会话规则）",
    "platform_missing": "平台未加载",
    "bot_unavailable": "机器人实例不可用",
    "no_eligible_account": "没有可用账号",
}


# ---- request helpers (AstrBot 4.26 proxy and Quart fallback) ----


def _query_params() -> Any:
    query = getattr(request, "query", None)
    if query is not None:
        return query
    return getattr(request, "args", None)


def _query_get(key: str, default: Any = None, cast: Any = None) -> Any:
    query = _query_params()
    if query is None:
        return default
    try:
        if cast is not None:
            return query.get(key, default, type=cast)
        return query.get(key, default)
    except TypeError:
        value = query.get(key, default)
        if cast is not None and value is not None:
            try:
                return cast(value)
            except (TypeError, ValueError):
                return default
        return value


async def _json_body(default: Any = None) -> Any:
    json_method = getattr(request, "json", None)
    if callable(json_method):
        try:
            return await json_method(default=default)
        except TypeError:
            try:
                return await json_method()
            except Exception:
                return default
        except Exception:
            return default
    get_json = getattr(request, "get_json", None)
    if callable(get_json):
        try:
            return await get_json(silent=True) or default
        except Exception:
            return default
    return default


# ---- small data helpers ----


def _plugin_version(plugin) -> str:
    try:
        from astrbot.core.star.star import star_map

        metadata = star_map.get(type(plugin).__module__)
        if metadata is not None and metadata.version:
            return str(metadata.version)
    except Exception:
        metadata = None
    return str(getattr(plugin, "version", None) or "unknown")


def _astrbot_version() -> str | None:
    try:
        import astrbot

        version = getattr(astrbot, "__version__", None)
        return str(version) if version else None
    except Exception:
        return None


def _valid_group_id(raw: Any) -> str | None:
    text = str(raw or "").strip()
    if not text.isdigit() or not 1 <= len(text) <= 20:
        return None
    return text


def _absolute_url(raw: str | None) -> str | None:
    if not raw:
        return None
    if raw.startswith(("http://", "https://")):
        return raw
    if raw.startswith("//"):
        return f"https:{raw}"
    if raw.startswith("/"):
        return f"{SITE_URL}{raw}"
    return raw


def normalize_game_url(raw: str | None) -> str | None:
    """Accept only https online-fix.me URLs under /games/."""
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw or len(raw) > 500:
        return None
    if raw.startswith("/"):
        raw = f"{SITE_URL}{raw}"
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        host == "online-fix.me" or host.endswith(".online-fix.me")
    ):
        return None
    if parsed.username or parsed.password:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port not in (None, 443):
        return None
    if not parsed.path.startswith("/games/"):
        return None
    return urlunparse(("https", host, parsed.path, "", "", ""))


def _seen_ids(plugin) -> set[str]:
    try:
        seen = plugin._load_seen()
    except Exception:
        return set()
    ids = seen.get("onlinefix", []) if isinstance(seen, dict) else []
    return {str(item) for item in ids} if isinstance(ids, list) else set()


def _game_to_dict(game, seen_ids: set[str]) -> dict[str, Any]:
    return {
        "id": str(game.id),
        "title": game.title,
        "url": _absolute_url(game.url),
        "poster_url": _absolute_url(game.poster_url),
        "post_date": game.post_date,
        "release_date": game.release_date,
        "platforms": list(game.platforms or []),
        "modes": list(game.modes or []),
        "update_info": game.update_info,
        "is_new": str(game.id) not in seen_ids,
    }


async def _load_subscriptions(plugin) -> list[dict]:
    try:
        raw = await plugin.get_kv_data("subscriptions", [])
    except Exception:
        raw = []
    return plugin._normalize_subscriptions(raw)


# ---- status ----


async def _build_status(plugin) -> dict[str, Any]:
    subscriptions = await _load_subscriptions(plugin)
    seen_ids = _seen_ids(plugin)
    status = plugin.status
    snapshot = status.snapshot()
    last_check = snapshot.get("last_check")
    now = datetime.now().astimezone()

    deliveries: dict[str, dict] = {}
    for item in subscriptions:
        platform_id = item.get("platform_id")
        delivery = status.get_delivery(platform_id, item["group_id"])
        if delivery is None:
            continue
        key = platform_id or ""
        current = deliveries.get(key)
        if current is None or str(delivery.get("at", "")) > str(
            current.get("at", "")
        ):
            deliveries[key] = delivery

    accounts = []
    for inst in plugin._aiocqhttp_insts():
        platform_id = plugin._inst_platform_id(inst)
        bot = plugin._inst_bot(inst)
        state = getattr(inst, "status", None)
        accounts.append(
            {
                "platform_id": platform_id,
                "self_id": str(getattr(bot, "self_id", "") or "") or None,
                "status": getattr(state, "value", None) or "unknown",
                "subscriptions": sum(
                    1
                    for item in subscriptions
                    if item.get("platform_id") == platform_id
                ),
                "last_delivery": deliveries.get(platform_id or ""),
            }
        )

    next_check_at = snapshot.get("next_check_at")
    seconds_until_next = None
    if next_check_at:
        try:
            seconds_until_next = max(
                0,
                int(
                    (
                        datetime.fromisoformat(next_check_at) - now
                    ).total_seconds()
                ),
            )
        except (TypeError, ValueError):
            seconds_until_next = None

    config_keys = (
        "request_timeout",
        "game_check_interval",
        "max_display_items",
        "send_game_posters",
        "fallback_to_text_on_upload_failure",
    )
    config = {key: plugin.config.get(key) for key in config_keys}

    return {
        "server_time": now.isoformat(timespec="seconds"),
        "plugin": {
            "name": plugin._plugin_name(),
            "display_name": "getGameInfo",
            "version": _plugin_version(plugin),
            "astrbot_version": _astrbot_version(),
        },
        "runtime": {
            "started_at": snapshot.get("started_at"),
            "uptime_seconds": snapshot.get("uptime_seconds", 0),
            "scraper_ready": getattr(plugin, "_scraper", None) is not None,
            "poll_task_running": bool(
                getattr(plugin, "_poll_new_task", None)
                and not plugin._poll_new_task.done()
            ),
            "checking": bool(
                getattr(plugin, "_check_lock", None)
                and plugin._check_lock.locked()
            )
            or bool(snapshot.get("checking")),
        },
        "schedule": {
            "interval_seconds": max(
                int(plugin.config.get("game_check_interval", 21600) or 21600),
                1800,
            ),
            "next_check_at": next_check_at,
            "seconds_until_next": seconds_until_next,
        },
        "last_check": last_check,
        "counts": {
            "subscriptions": len(subscriptions),
            "groups": len({item["group_id"] for item in subscriptions}),
            "accounts": len(accounts),
            "tracked_games": len(seen_ids),
            "today": snapshot.get("today", {}),
        },
        "history": snapshot.get("history", []),
        "accounts": accounts,
        "config": config,
        "health": {
            "site": SITE_URL,
            # No extra request is made for the status poll; the last check is
            # the health signal. A dedicated probe can be added later.
            "reachable": last_check.get("ok") if last_check else None,
            "latency_ms": last_check.get("duration_ms") if last_check else None,
            "checked_at": last_check.get("finished_at") if last_check else None,
        },
    }


async def _build_subscription_items(plugin) -> tuple[list[dict], dict]:
    subscriptions = await _load_subscriptions(plugin)
    items = []
    states = []
    for subscription in subscriptions:
        try:
            evaluation = await plugin.evaluate_subscription(subscription)
        except Exception as exc:
            logger.warning("评估订阅路由失败: %s", type(exc).__name__)
            evaluation = {
                "state": "unknown",
                "platform_id": subscription.get("platform_id"),
                "bot": None,
            }
        state = evaluation.get("state", "no_eligible_account")
        states.append(state)
        resolved = evaluation.get("platform_id")
        bot = evaluation.get("bot")
        items.append(
            {
                "group_id": subscription["group_id"],
                "platform_id": subscription.get("platform_id"),
                "self_id": subscription.get("self_id"),
                "resolved_platform_id": resolved,
                "state": state,
                "state_label": STATE_LABELS.get(state, "未知状态"),
                "will_migrate": bool(
                    resolved
                    and subscription.get("platform_id")
                    and resolved != subscription.get("platform_id")
                ),
                "account_self_id": (
                    str(getattr(bot, "self_id", "") or "") or None
                    if bot is not None
                    else None
                ),
                "last_delivery": plugin.status.get_delivery(
                    subscription.get("platform_id") or resolved,
                    subscription["group_id"],
                ),
            }
        )
    summary = {
        "total": len(items),
        "ready": states.count("ready"),
        "legacy": states.count("legacy"),
    }
    summary["blocked"] = summary["total"] - summary["ready"] - summary["legacy"]
    return items, summary


# ---- API registration ----


def register_view_apis(plugin, context) -> None:
    """Register the read-only view API. Safe to call more than once."""
    register = getattr(context, "register_web_api", None)
    if not callable(register):
        return

    games_cache: dict[str, Any] = {
        "at": 0.0,
        "page": 0,
        "items": [],
        "total_pages": 1,
        "fetched_at": None,
    }
    manual_cooldown = {"at": 0.0}

    async def api_status():
        payload = await _build_status(plugin)
        payload["health"] = await get_health()
        return json_response(payload)

    async def api_subscriptions():
        items, summary = await _build_subscription_items(plugin)
        return json_response({"items": items, "summary": summary})

    async def api_events():
        limit = _query_get("limit", 100, int) or 100
        category = str(_query_get("category", "all") or "all")
        level = _query_get("level", None)
        query = _query_get("q", None)
        if category not in ("all", "check", "push", "error"):
            category = "all"
        if level not in (None, "info", "warning", "error"):
            level = None
        return json_response(
            {
                "items": plugin.status.events(
                    limit=max(1, min(int(limit), MAX_EVENT_LIMIT)),
                    category=category,
                    level=level,
                    query=str(query) if query else None,
                )
            }
        )

    async def api_games():
        page = int(_query_get("page", 1, int) or 1)
        page = max(1, min(page, 50))
        default_limit = int(plugin.config.get("max_display_items", 21) or 21)
        limit = int(_query_get("limit", default_limit, int) or default_limit)
        limit = max(1, min(limit, MAX_LIST_LIMIT))
        force = str(_query_get("force", "0") or "0").lower() in ("1", "true", "yes")

        now_mono = time.monotonic()
        cached = bool(
            not force
            and games_cache["items"]
            and games_cache["page"] == page
            and now_mono - games_cache["at"] < GAME_CACHE_TTL
        )
        if not cached:
            if getattr(plugin, "_scraper", None) is None:
                return error_response("插件尚未完成初始化", status_code=503)
            try:
                games, total_pages = await plugin._scraper.fetch_list(page)
            except Exception as exc:
                logger.warning("获取游戏列表失败: %s", type(exc).__name__)
                return error_response("获取游戏列表失败，请稍后重试", status_code=502)
            seen = _seen_ids(plugin)
            games_cache.update(
                {
                    "at": now_mono,
                    "page": page,
                    "items": [_game_to_dict(game, seen) for game in games],
                    "total_pages": total_pages,
                    "fetched_at": datetime.now().astimezone().isoformat(
                        timespec="seconds"
                    ),
                }
            )

        return json_response(
            {
                "page": page,
                "total_pages": games_cache["total_pages"],
                "cached": cached,
                "fetched_at": games_cache["fetched_at"],
                "items": games_cache["items"][:limit],
            }
        )

    async def api_search_games():
        query = str(_query_get("q", "") or "").strip()
        if not 1 <= len(query) <= 100:
            return error_response("搜索关键词长度需在 1-100 个字符之间")
        if getattr(plugin, "_scraper", None) is None:
            return error_response("插件尚未完成初始化", status_code=503)
        try:
            games, total_pages = await plugin._scraper.search(query)
        except Exception as exc:
            logger.warning("搜索游戏失败: %s", type(exc).__name__)
            return error_response("搜索失败，请稍后重试", status_code=502)
        seen = _seen_ids(plugin)
        return json_response(
            {
                "query": query,
                "total_pages": total_pages,
                "items": [
                    _game_to_dict(game, seen) for game in games[:MAX_LIST_LIMIT]
                ],
            }
        )

    async def api_game_detail():
        url = normalize_game_url(_query_get("url", None))
        if url is None:
            return error_response("只允许查询 online-fix.me 的游戏详情页")
        if getattr(plugin, "_scraper", None) is None:
            return error_response("插件尚未完成初始化", status_code=503)
        try:
            coop, multi = await plugin._scraper.fetch_player_counts(url)
        except Exception as exc:
            logger.warning("获取玩家人数失败: %s", type(exc).__name__)
            return error_response("获取游戏详情失败，请稍后重试", status_code=502)
        return json_response(
            {
                "url": url,
                "coop_players": coop,
                "multi_players": multi,
            }
        )

    async def api_check():
        payload = await _json_body(default={})
        if not isinstance(payload, dict):
            payload = {}
        mode = str(payload.get("mode", "dry_run") or "dry_run")
        if mode not in ("dry_run", "push"):
            return error_response("mode 必须是 dry_run 或 push", status_code=400)
        if getattr(plugin, "_scraper", None) is None:
            return error_response("插件尚未完成初始化", status_code=503)
        if mode == "push":
            subscriptions = await _load_subscriptions(plugin)
            if not subscriptions:
                return error_response("还没有订阅群，无法推送", status_code=400)
        if plugin._check_lock.locked():
            return error_response("已有检查正在进行", status_code=409)
        now_mono = time.monotonic()
        if now_mono - manual_cooldown["at"] < MANUAL_CHECK_COOLDOWN:
            return error_response("操作过于频繁，请稍后再试", status_code=429)
        manual_cooldown["at"] = now_mono
        try:
            result = await plugin.run_check(
                trigger="manual", dry_run=mode == "dry_run"
            )
        except CheckBusyError:
            return error_response("已有检查正在进行", status_code=409)
        except Exception as exc:
            logger.warning("手动检查异常: %s", type(exc).__name__)
            return error_response("检查失败，请查看插件日志", status_code=500)
        if not result.get("ok"):
            return error_response(
                str(result.get("error") or "检查失败"), status_code=502
            )
        return json_response(result)

    async def api_subscriptions_remove():
        payload = await _json_body(default={})
        if not isinstance(payload, dict):
            payload = {}
        group_id = _valid_group_id(payload.get("group_id"))
        if group_id is None:
            return error_response("group_id 不合法")
        platform_id = payload.get("platform_id")
        if platform_id is not None:
            platform_id = str(platform_id).strip() or None
        removed = await plugin.remove_subscription(group_id, platform_id)
        if not removed:
            return error_response("未找到该订阅", status_code=404)
        return json_response({"ok": True, "removed": removed, "group_id": group_id})

    test_cooldown: dict[str, float] = {}
    health_cache: dict[str, Any] = {"at": 0.0, "data": None}
    groups_cache: dict[str, dict[str, Any]] = {}

    async def get_health(force: bool = False) -> dict:
        scraper = getattr(plugin, "_scraper", None)
        probe = getattr(scraper, "probe", None)
        now_mono = time.monotonic()
        if (
            not force
            and health_cache["data"] is not None
            and now_mono - health_cache["at"] < 60
        ):
            return health_cache["data"]
        if not callable(probe):
            last = plugin.status.last_check
            return {
                "site": SITE_URL,
                "reachable": last.get("ok") if last else None,
                "status_code": None,
                "latency_ms": last.get("duration_ms") if last else None,
                "checked_at": last.get("finished_at") if last else None,
                "error": None,
            }
        try:
            data = await probe()
        except Exception as exc:
            data = {
                "site": SITE_URL,
                "reachable": False,
                "status_code": None,
                "latency_ms": None,
                "checked_at": datetime.now().astimezone().isoformat(
                    timespec="seconds"
                ),
                "error": type(exc).__name__,
            }
        health_cache["at"] = now_mono
        health_cache["data"] = data
        return data

    async def api_health():
        force = str(_query_get("force", "0") or "0").lower() in (
            "1",
            "true",
            "yes",
        )
        return json_response(await get_health(force=force))

    async def api_subscriptions_test():
        payload = await _json_body(default={})
        if not isinstance(payload, dict):
            payload = {}
        group_id = _valid_group_id(payload.get("group_id"))
        if group_id is None:
            return error_response("group_id 不合法")
        platform_id = payload.get("platform_id")
        if platform_id is not None:
            platform_id = str(platform_id).strip() or None

        subscriptions = await _load_subscriptions(plugin)
        subscription = next(
            (
                item
                for item in subscriptions
                if item["group_id"] == group_id
                and (platform_id is None or item.get("platform_id") == platform_id)
            ),
            None,
        )
        if subscription is None:
            return error_response("未找到该订阅", status_code=404)

        evaluation = await plugin.evaluate_subscription(subscription)
        state = str(evaluation.get("state") or "unknown")
        bot = evaluation.get("bot")
        if bot is None:
            return error_response(
                f"该订阅当前不可推送：{STATE_LABELS.get(state, '未知状态')}",
                status_code=409,
            )

        cooldown_key = f"{group_id}:{platform_id or ''}"
        now_mono = time.monotonic()
        if now_mono - test_cooldown.get(cooldown_key, 0.0) < TEST_PUSH_COOLDOWN:
            return error_response("测试推送过于频繁，请稍后再试", status_code=429)
        test_cooldown[cooldown_key] = now_mono

        platform_used = evaluation.get("platform_id") or subscription.get(
            "platform_id"
        )
        message = [
            {
                "type": "text",
                "data": {
                    "text": (
                        "getGameInfo 测试消息\n\n"
                        f"群号：{group_id}\n"
                        f"账号：{platform_used or '未知'}\n"
                        "如果你看到这条消息，说明该订阅的推送路由正常。"
                    )
                },
            }
        ]
        try:
            await call_action(
                bot, "send_group_msg", group_id=int(group_id), message=message
            )
        except OneBotActionError as exc:
            plugin.status.add_event(
                "warning",
                "push_test",
                f"群 {group_id} 测试推送失败：{exc.reason}",
                group_id=group_id,
                platform_id=platform_used,
                reason=exc.reason,
                retcode=exc.retcode,
                action=exc.action,
                detail=exc.detail,
            )
            plugin.status.save()
            logger.warning("测试推送失败: %s", exc.diagnostic())
            return error_response(exc.user_message(), status_code=502)
        except Exception as exc:
            plugin.status.add_event(
                "error",
                "push_test",
                f"群 {group_id} 测试推送异常：{type(exc).__name__}",
                group_id=group_id,
                platform_id=platform_used,
            )
            plugin.status.save()
            logger.warning("测试推送异常: %s", type(exc).__name__)
            return error_response("测试消息发送失败，请查看插件日志", status_code=500)

        plugin.status.add_event(
            "info",
            "push_test",
            f"群 {group_id} 测试推送成功",
            group_id=group_id,
            platform_id=platform_used,
        )
        plugin.status.save()
        return json_response(
            {
                "ok": True,
                "group_id": group_id,
                "platform_id": platform_used,
                "self_id": str(getattr(bot, "self_id", "") or "") or None,
            }
        )

    async def api_history():
        days = int(_query_get("days", 7, int) or 7)
        return json_response({"days": plugin.status.history(days)})

    async def api_diagnostics():
        status = await _build_status(plugin)
        status["health"] = await get_health()
        items, summary = await _build_subscription_items(plugin)
        seen = plugin._load_seen()
        seen_ids = seen.get("onlinefix", []) if isinstance(seen, dict) else []
        if not isinstance(seen_ids, list):
            seen_ids = []
        return json_response(
            {
                "generated_at": datetime.now().astimezone().isoformat(
                    timespec="seconds"
                ),
                "plugin": status["plugin"],
                "runtime": status["runtime"],
                "schedule": status["schedule"],
                "last_check": status["last_check"],
                "counts": status["counts"],
                "config": status["config"],
                "health": status["health"],
                "accounts": status["accounts"],
                "subscriptions": {"items": items, "summary": summary},
                "history": plugin.status.history(7),
                "events": plugin.status.events(limit=MAX_EVENT_LIMIT),
                "seen_games": [str(item) for item in seen_ids],
            }
        )

    async def api_maintenance_seen():
        payload = await _json_body(default={})
        if not isinstance(payload, dict):
            payload = {}
        action = str(payload.get("action") or "").strip()
        confirm = str(payload.get("confirm") or "").strip()
        if action not in ("reset", "clear"):
            return error_response("action 必须是 reset 或 clear")
        required = "RESET" if action == "reset" else "CLEAR"
        if confirm != required:
            return error_response(f"请输入 {required} 以确认此操作")

        if action == "reset":
            if getattr(plugin, "_scraper", None) is None:
                return error_response("插件尚未完成初始化", status_code=503)
            try:
                games, _ = await plugin._scraper.fetch_list(1)
            except Exception as exc:
                logger.warning("重置 seen 基线失败: %s", type(exc).__name__)
                return error_response("抓取当前游戏列表失败，请稍后重试", status_code=502)
            ids = [game.id for game in games]
            plugin._save_seen({"onlinefix": ids})
            plugin.status.add_event(
                "info",
                "maintenance",
                f"seen 基线已重置（{len(ids)} 款）",
                count=len(ids),
            )
            plugin.status.save()
            return json_response(
                {
                    "ok": True,
                    "action": "reset",
                    "tracked_games": len(ids),
                    "message": f"已把当前 {len(ids)} 款游戏设为 seen 基线，不会推送",
                }
            )

        plugin._save_seen({"onlinefix": [], "force_push_all": True})
        plugin.status.add_event(
            "warning",
            "maintenance",
            "seen 基线已清空，下次检查会推送当前全部游戏",
        )
        plugin.status.save()
        return json_response(
            {
                "ok": True,
                "action": "clear",
                "tracked_games": 0,
                "message": "seen 基线已清空；下次检查会推送当前全部游戏",
            }
        )

    async def fetch_groups(platform_id: str) -> list[dict]:
        cached = groups_cache.get(platform_id)
        if cached and time.monotonic() - cached["at"] < 30:
            return cached["items"]
        inst = plugin._find_platform(platform_id)
        if inst is None:
            raise ValueError("平台未加载")
        bot = plugin._inst_bot(inst)
        if bot is None:
            raise ValueError("机器人实例不可用")
        result = await call_action(bot, "get_group_list")
        groups = result.get("data") if isinstance(result, dict) else result
        if not isinstance(groups, list):
            groups = []
        items = []
        for item in groups:
            if not isinstance(item, dict):
                continue
            group_id = _valid_group_id(item.get("group_id") or item.get("group"))
            if group_id is None:
                continue
            name = str(item.get("group_name") or item.get("name") or "")[:100]
            items.append({"group_id": group_id, "group_name": name})
        items.sort(key=lambda item: item["group_id"])
        groups_cache[platform_id] = {
            "at": time.monotonic(),
            "items": items,
        }
        return items

    async def api_groups():
        platform_id = str(_query_get("platform_id", "") or "").strip()
        if not platform_id:
            return error_response("platform_id 不能为空")
        try:
            items = await fetch_groups(platform_id)
        except OneBotActionError as exc:
            logger.warning("获取群列表失败: %s", exc.diagnostic())
            return error_response(exc.user_message(), status_code=502)
        except ValueError as exc:
            return error_response(str(exc), status_code=409)
        return json_response({"platform_id": platform_id, "items": items})

    async def api_subscriptions_add():
        payload = await _json_body(default={})
        if not isinstance(payload, dict):
            payload = {}
        group_id = _valid_group_id(payload.get("group_id"))
        platform_id = str(payload.get("platform_id") or "").strip()
        if group_id is None:
            return error_response("group_id 不合法")
        if not platform_id:
            return error_response("platform_id 不能为空")
        try:
            groups = await fetch_groups(platform_id)
        except OneBotActionError as exc:
            return error_response(exc.user_message(), status_code=502)
        except ValueError as exc:
            return error_response(str(exc), status_code=409)
        if group_id not in {item["group_id"] for item in groups}:
            return error_response("机器人不在该群，无法添加订阅", status_code=400)

        evaluation = await plugin.evaluate_subscription(
            {"group_id": group_id, "platform_id": platform_id}
        )
        bot = evaluation.get("bot")
        if bot is None:
            return error_response(
                f"该账号当前不可推送：{STATE_LABELS.get(evaluation.get('state'), '未知状态')}",
                status_code=409,
            )
        changed = await plugin.add_subscription(
            group_id, platform_id, getattr(bot, "self_id", None)
        )
        return json_response(
            {
                "ok": True,
                "changed": changed,
                "group_id": group_id,
                "platform_id": platform_id,
                "message": "已添加订阅" if changed else "该群已订阅此账号",
            }
        )

    routes = [
        (f"/{PLUGIN_NAME}/status", api_status, ["GET"], "Get plugin status"),
        (f"/{PLUGIN_NAME}/health", api_health, ["GET"], "Probe site health"),
        (f"/{PLUGIN_NAME}/history", api_history, ["GET"], "Get daily history"),
        (
            f"/{PLUGIN_NAME}/diagnostics",
            api_diagnostics,
            ["GET"],
            "Export diagnostics",
        ),
        (
            f"/{PLUGIN_NAME}/subscriptions",
            api_subscriptions,
            ["GET"],
            "List game subscriptions",
        ),
        (f"/{PLUGIN_NAME}/events", api_events, ["GET"], "List recent events"),
        (f"/{PLUGIN_NAME}/games", api_games, ["GET"], "List latest games"),
        (
            f"/{PLUGIN_NAME}/games/search",
            api_search_games,
            ["GET"],
            "Search games",
        ),
        (
            f"/{PLUGIN_NAME}/games/detail",
            api_game_detail,
            ["GET"],
            "Get game player counts",
        ),
        (
            f"/{PLUGIN_NAME}/subscriptions/add",
            api_subscriptions_add,
            ["POST"],
            "Add a subscription",
        ),
        (
            f"/{PLUGIN_NAME}/subscriptions/remove",
            api_subscriptions_remove,
            ["POST"],
            "Remove a subscription",
        ),
        (
            f"/{PLUGIN_NAME}/subscriptions/test",
            api_subscriptions_test,
            ["POST"],
            "Send a test push",
        ),
        (
            f"/{PLUGIN_NAME}/groups",
            api_groups,
            ["GET"],
            "List groups for an account",
        ),
        (
            f"/{PLUGIN_NAME}/maintenance/seen",
            api_maintenance_seen,
            ["POST"],
            "Reset or clear the seen baseline",
        ),
        (f"/{PLUGIN_NAME}/check", api_check, ["POST"], "Run a check"),
    ]
    for route, handler, methods, desc in routes:
        try:
            register(route, handler, methods, desc)
        except Exception as exc:
            logger.warning("注册视图 API %s 失败: %s", route, exc)
