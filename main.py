"""astrbot_plugin_getGameInfo — 游戏插件"""

import asyncio
import base64
import io
import json
import time
from datetime import datetime
from pathlib import Path

import httpx
from PIL import Image as PILImage

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from . import webui
from .models import GameBrief
from .onebot_sender import OneBotActionError, call_action, sanitize_detail, text_only_payload
from .scraper import BaseGameScraper, OnlineFixScraper
from .status import CheckBusyError, RuntimeState

SCRAPER_REGISTRY: dict[str, type[BaseGameScraper]] = {"onlinefix": OnlineFixScraper}

# Used when the loader has not injected ``name`` yet (for example in tests).
PLUGIN_NAME_FALLBACK = "astrbot_plugin_getGameInfo"
# ``MessageType.GROUP_MESSAGE.value``; kept local so the plugin does not need
# another AstrBot import just to build a UMO for profile lookups.
GROUP_MESSAGE_TYPE = "GroupMessage"


class GameDownPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._scraper: BaseGameScraper | None = None
        self._poll_new_task: asyncio.Task | None = None
        self._image_client = httpx.AsyncClient(timeout=config.get("request_timeout", 30))
        self.status = RuntimeState()
        self._check_lock = asyncio.Lock()
        webui.register_view_apis(self, context)

    async def initialize(self):
        cls = SCRAPER_REGISTRY.get("onlinefix", OnlineFixScraper)
        self._scraper = cls(
            timeout=self.config.get("request_timeout", 30),
        )
        self.status.attach_storage(self._data_dir() / "status.json")
        self.status.load()
        self._poll_new_task = asyncio.create_task(self._poll_new_games())
        logger.info(f"game_down 已就绪（{self._scraper.site_name}）")

    async def terminate(self):
        if self._poll_new_task:
            self._poll_new_task.cancel()
            try:
                await self._poll_new_task
            except asyncio.CancelledError:
                pass
        try:
            if self._scraper:
                await self._scraper.close()
                self._scraper = None
        finally:
            self.status.save()
            await self._image_client.aclose()

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("game")
    async def on_game(self, event: AstrMessageEvent):
        parts = event.message_str.strip().split(maxsplit=2)
        if len(parts) < 2:
            yield event.plain_result("用法: /game <list|info|subscribe|unsubscribe>")
            return
        sub, args = parts[1], parts[2] if len(parts) > 2 else ""
        try:
            if sub == "list":
                async for r in self._cmd_list(event, args): yield r
            elif sub == "info":
                async for r in self._cmd_info(event, args): yield r
            elif sub == "subscribe":
                async for r in self._cmd_subscribe(event): yield r
            elif sub == "unsubscribe":
                async for r in self._cmd_unsubscribe(event): yield r
            else: yield event.plain_result(f"未知子命令: {sub}")
        except OneBotActionError as e:
            logger.warning("游戏信息发送失败: %s", e.diagnostic())
            yield event.plain_result(e.user_message())
        except Exception as e:
            logger.warning("游戏命令异常: %s: %s", type(e).__name__, sanitize_detail(str(e)))
            yield event.plain_result("获取游戏信息失败，请查看插件日志")

    # ---- /game list ----

    async def _cmd_list(self, event: AstrMessageEvent, args: str):
        page = self._parse_list_args(args)
        yield event.plain_result(f"正在获取第 {page} 页…")
        games, total_pages = await self._scraper.fetch_list(page)
        if not games:
            yield event.plain_result("暂无游戏。")
            return
        max_display = self.config.get("max_display_items", 21)
        games = games[:max_display]

        group_id = int(event.get_group_id()) if event.get_group_id() else 0
        if group_id == 0:
            lines = [f"第 {page}/{total_pages} 页\n"]
            for g in games:
                lines.append(f"  [{g.id}] {g.title}")
            yield event.plain_result("\n".join(lines))
            return

        bot = self._get_bot(event)
        if not bot:
            yield event.plain_result("无法获取 bot 实例。")
            return
        nodes = await self._build_nodes(games, event.get_self_id(), "Online-Fix.me")
        downgraded = await self._send_group_with_fallback(
            bot, "send_group_forward_msg", group_id, messages=nodes,
        )
        if downgraded:
            yield event.plain_result("图片上传失败，已改发纯文字游戏列表。")

    # ---- /game info ----

    async def _cmd_info(self, event: AstrMessageEvent, args: str):
        if not args:
            yield event.plain_result("用法: /game info <游戏名>")
            return
        yield event.plain_result(f"正在搜索: {args}…")
        games, _ = await self._scraper.search(args)
        if not games:
            yield event.plain_result(f"未找到与 '{args}' 相关的游戏。")
            return
        group_id = int(event.get_group_id()) if event.get_group_id() else 0
        if len(games) == 1:
            g = games[0]
            # Keep the existing detail-page player-count feature.
            if g.url:
                full_url = g.url if g.url.startswith("http") else f"https://online-fix.me{g.url}"
                try:
                    g.coop_players, g.multi_players = await self._scraper.fetch_player_counts(full_url)
                except Exception as e:
                    logger.warning("获取玩家人数失败: %s", type(e).__name__)
                    g.coop_players = g.multi_players = None
            if group_id == 0:
                # Private replies are text-only, so do not download a poster.
                yield event.plain_result(self._fmt_card(g))
                return
            bot = self._get_bot(event)
            if not bot:
                yield event.plain_result("无法获取 bot 实例。")
                return
            message = []
            message_id = getattr(event.message_obj, "message_id", None)
            if message_id is not None and message_id != "":
                message.append({"type": "reply", "data": {"id": message_id}})
            message.extend(await self._card_segments(g))
            downgraded = await self._send_group_with_fallback(
                bot, "send_group_msg", group_id, message=message,
            )
        else:
            if group_id == 0:
                lines = [f"'{args}' 搜索结果（{len(games)} 个）："]
                lines.extend(f"  {g.title}" for g in games)
                yield event.plain_result("\n".join(lines))
                return
            bot = self._get_bot(event)
            if not bot:
                yield event.plain_result("无法获取 bot 实例。")
                return
            nodes = await self._build_nodes(games, event.get_self_id(), "Online-Fix.me")
            downgraded = await self._send_group_with_fallback(
                bot, "send_group_forward_msg", group_id, messages=nodes,
            )
        if downgraded:
            yield event.plain_result("图片上传失败，已改发纯文字游戏信息。")

    # ---- 订阅 ----

    async def _cmd_subscribe(self, event: AstrMessageEvent):
        gid = event.get_group_id()
        if not gid:
            yield event.plain_result("仅支持在群聊中订阅。"); return
        subscriptions = self._normalize_subscriptions(
            await self.get_kv_data("subscriptions", [])
        )
        platform_id = self._event_platform_id(event)
        record = {
            "group_id": str(gid),
            "platform_id": platform_id,
            "self_id": str(event.get_self_id()) if event.get_self_id() else None,
        }
        existing = next(
            (item for item in subscriptions if item["group_id"] == record["group_id"]),
            None,
        )
        if existing is not None and existing.get("platform_id") == platform_id:
            yield event.plain_result("本群已订阅。"); return
        # A group is pushed by one account only: subscribing again (possibly
        # with another bot in the same group) moves the subscription here.
        subscriptions = [
            item for item in subscriptions if item["group_id"] != record["group_id"]
        ]
        subscriptions.append(record)
        await self.put_kv_data("subscriptions", subscriptions)
        self.status.add_event(
            "info",
            "subscribe",
            f"群 {record['group_id']} 新增订阅",
            group_id=record["group_id"],
            platform_id=platform_id,
        )
        yield event.plain_result("已订阅新游戏推送。\n发送 /game unsubscribe 取消。")

    async def _cmd_unsubscribe(self, event: AstrMessageEvent):
        gid = event.get_group_id()
        if not gid:
            yield event.plain_result("仅支持在群聊中操作。"); return
        subscriptions = self._normalize_subscriptions(
            await self.get_kv_data("subscriptions", [])
        )
        remaining = [item for item in subscriptions if item["group_id"] != str(gid)]
        if len(remaining) == len(subscriptions):
            yield event.plain_result("本群未订阅。"); return
        await self.put_kv_data("subscriptions", remaining)
        self.status.add_event(
            "info",
            "unsubscribe",
            f"群 {gid} 取消订阅",
            group_id=str(gid),
        )
        yield event.plain_result("已取消新游戏推送。")

    # ---- 新游戏轮询 ----

    @staticmethod
    def _data_dir() -> Path:
        return (
            Path(get_astrbot_data_path())
            / "plugin_data"
            / "astrbot_plugin_getGameInfo"
        )

    def _load_seen(self) -> dict:
        seen_path = self._data_dir() / "seen_games.json"
        if not seen_path.exists():
            return {}
        try:
            payload = json.loads(seen_path.read_text("utf-8") or "{}")
        except (OSError, ValueError) as exc:
            logger.warning("读取 seen_games.json 失败: %s", exc)
            return {}
        return payload if isinstance(payload, dict) else {}

    def _save_seen(self, seen: dict) -> None:
        data_dir = self._data_dir()
        seen_path = data_dir / "seen_games.json"
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            seen_path.write_text(json.dumps(seen, ensure_ascii=False, indent=2))
        except OSError as exc:
            logger.warning("写入 seen_games.json 失败: %s", exc)

    def _check_interval(self) -> int:
        try:
            return max(int(self.config.get("game_check_interval", 21600)), 1800)
        except (TypeError, ValueError):
            return 21600

    async def _poll_new_games(self):
        """定时轮询新游戏，按当前时钟时间对齐整点执行。

        例如 interval=7200（2小时），则在 00:00、02:00、04:00… 执行，
        而非基于插件启动时间偏移。
        """
        while True:
            interval = self._check_interval()
            now = time.time()
            next_tick = ((now // interval) + 1) * interval
            wait = max(0.0, next_tick - now)
            self.status.next_check_at = datetime.fromtimestamp(
                next_tick
            ).astimezone().isoformat(timespec="seconds")
            logger.info(
                "下次新游戏检查将在 %.0f 秒后（%s）",
                wait,
                time.strftime("%H:%M:%S", time.localtime(next_tick)),
            )
            try:
                await asyncio.sleep(wait)
                subs: list = await self.get_kv_data("subscriptions", [])
                if subs:
                    await self.run_check(trigger="schedule")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("新游戏轮询异常")

    async def run_check(self, trigger: str = "schedule", dry_run: bool = False) -> dict:
        """Run one check. Used by the scheduler and the WebUI."""
        if self._scraper is None:
            return {
                "mode": "dry_run" if dry_run else "push",
                "ok": False,
                "error": "插件尚未完成初始化",
            }
        if self._check_lock.locked():
            raise CheckBusyError("已有检查正在进行")

        async with self._check_lock:
            started = time.monotonic()
            self.status.start_check(trigger=trigger, dry_run=dry_run)
            try:
                games, _ = await self._scraper.fetch_list(1)
                seen = self._load_seen()
                old_ids = seen.get("onlinefix", []) if isinstance(seen, dict) else []
                baseline_missing = not bool(old_ids)
                new_games = (
                    []
                    if baseline_missing
                    else [game for game in games if game.id not in old_ids]
                )

                if dry_run:
                    duration_ms = int((time.monotonic() - started) * 1000)
                    record = self.status.finish_check(
                        ok=True,
                        trigger=trigger,
                        dry_run=True,
                        duration_ms=duration_ms,
                        games_seen=len(games),
                        new_games=len(new_games),
                        baseline_missing=baseline_missing,
                    )
                    return {
                        "mode": "dry_run",
                        "ok": True,
                        "duration_ms": duration_ms,
                        "games_seen": len(games),
                        "baseline_missing": baseline_missing,
                        "new_games": [
                            {"id": game.id, "title": game.title}
                            for game in new_games
                        ],
                        "finished_at": record.get("finished_at"),
                    }

                subs = await self.get_kv_data("subscriptions", [])
                summary = await self._push_new_games(subs, games, new_games)
                duration_ms = int((time.monotonic() - started) * 1000)
                self.status.finish_check(
                    ok=True,
                    trigger=trigger,
                    dry_run=False,
                    duration_ms=duration_ms,
                    games_seen=len(games),
                    new_games=len(new_games),
                    pushes=summary["pushes"],
                    failures=summary["failures"],
                    downgraded=summary["downgraded"],
                    skipped=summary["skipped"],
                    baseline_missing=baseline_missing,
                )
                if new_games:
                    self.status.add_event(
                        "info",
                        "new_games",
                        f"发现 {len(new_games)} 款新游戏",
                        count=len(new_games),
                    )
                return {
                    "mode": "push",
                    "ok": True,
                    "duration_ms": duration_ms,
                    "games_seen": len(games),
                    "baseline_missing": baseline_missing,
                    **summary,
                }
            except asyncio.CancelledError:
                self.status.checking = False
                raise
            except Exception as exc:
                duration_ms = int((time.monotonic() - started) * 1000)
                message = sanitize_detail(
                    f"{type(exc).__name__}: {exc}"
                ) or type(exc).__name__
                self.status.finish_check(
                    ok=False,
                    trigger=trigger,
                    dry_run=dry_run,
                    duration_ms=duration_ms,
                    error=message,
                )
                logger.warning("新游戏检查失败: %s", message)
                return {
                    "mode": "dry_run" if dry_run else "push",
                    "ok": False,
                    "error": message,
                    "duration_ms": duration_ms,
                }

    async def _push_new_games(
        self, subs: list, games, new_games: list[GameBrief] | None = None
    ) -> dict:
        seen = self._load_seen()
        current_ids = [g.id for g in games]
        old_ids = seen.get("onlinefix", []) if isinstance(seen, dict) else []
        summary: dict = {
            "new_games": [],
            "pushes": 0,
            "failures": 0,
            "downgraded": 0,
            "skipped": 0,
        }

        if not old_ids:
            seen["onlinefix"] = current_ids
            self._save_seen(seen)
            summary["baseline_initialized"] = True
            return summary

        if new_games is None:
            new_games = [g for g in games if g.id not in old_ids]
        if not new_games:
            return summary
        summary["new_games"] = [
            {"id": game.id, "title": game.title} for game in new_games
        ]

        # One AstrBot can run several OneBot accounts that are all members of
        # the same group. Resolve each subscription to the account that owns
        # it; sending through the first account may use one that has this
        # plugin disabled in its bound config profile.
        subscriptions = self._normalize_subscriptions(subs)
        targets: dict[int, tuple[object, list[dict]]] = {}
        for subscription in subscriptions:
            bot = await self._resolve_subscription_bot(subscription)
            if bot is None:
                summary["skipped"] += 1
                continue
            entries = targets.setdefault(id(bot), (bot, []))[1]
            if not any(
                item["group_id"] == subscription["group_id"] for item in entries
            ):
                entries.append(subscription)

        if summary["skipped"]:
            self.status.add_event(
                "warning",
                "push_skipped",
                f"{summary['skipped']} 个订阅没有可用的推送账号",
                count=summary["skipped"],
            )

        if not targets:
            logger.warning("没有可用的订阅机器人账号，本次新游戏推送已跳过")
            return summary

        if len(new_games) == 1:
            game = new_games[0]
            msg = await self._card_segments(game)
            msg[-1]["data"]["text"] = (
                f"新游戏上架！\n\n{self._fmt_card(game)}\n\n"
                f"发送 /game info {game.title[:20]} 查看详情"
            )
            for bot, entries in targets.values():
                for subscription in entries:
                    await self._push_one(
                        bot, subscription, "send_group_msg", message=msg,
                        summary=summary,
                    )
        else:
            # Posters are downloaded once and shared by every account.
            cards = [await self._card_segments(game) for game in new_games]
            for bot, entries in targets.values():
                self_id = await self._bot_self_id(bot)
                if not self_id:
                    logger.warning(
                        "无法确定推送账号，跳过 %s 个群的转发推送", len(entries),
                    )
                    summary["failures"] += len(entries)
                    for subscription in entries:
                        self._record_failure(
                            subscription, "无法确定推送账号",
                        )
                    continue
                nodes = [
                    {"type": "node", "data": {
                        "user_id": self_id, "nickname": "新游戏", "content": content,
                    }}
                    for content in cards
                ]
                for subscription in entries:
                    sent = await self._push_one(
                        bot, subscription, "send_group_forward_msg",
                        messages=nodes, summary=summary,
                    )
                    if not sent:
                        continue
                    gid = subscription["group_id"]
                    try:
                        await call_action(
                            bot,
                            "send_group_msg",
                            group_id=int(gid),
                            message=[{
                                "type": "text",
                                "data": {"text":
                                    f"新游戏上架 Online-Fix.me（{len(new_games)} 款）\n"
                                    "发送 /game info <游戏名> 查看详情"},
                            }],
                        )
                    except OneBotActionError as exc:
                        logger.warning(
                            "推送新游戏汇总到群 %s 失败: %s", gid, exc.diagnostic()
                        )
                    except Exception as exc:
                        logger.warning(
                            "推送新游戏汇总到群 %s 异常: %s",
                            gid,
                            type(exc).__name__,
                        )

        # Persist migrations from legacy group-id-only entries.
        try:
            await self.put_kv_data("subscriptions", subscriptions)
        except Exception:
            logger.warning("保存订阅机器人信息失败", exc_info=True)

        seen["onlinefix"] = current_ids
        self._save_seen(seen)
        return summary

    async def _push_one(
        self,
        bot,
        subscription: dict,
        action: str,
        *,
        summary: dict,
        **payload,
    ) -> bool:
        group_id = subscription["group_id"]
        platform_id = subscription.get("platform_id")
        try:
            downgraded = await self._send_group_with_fallback(
                bot, action, int(group_id), **payload,
            )
        except OneBotActionError as exc:
            summary["failures"] += 1
            self._record_failure(subscription, exc.reason)
            self.status.add_event(
                "warning",
                "push_fail",
                f"群 {group_id} 推送失败：{exc.reason}",
                group_id=group_id,
                platform_id=platform_id,
                reason=exc.reason,
                retcode=exc.retcode,
            )
            logger.warning("推送新游戏到群 %s 失败: %s", group_id, exc.diagnostic())
            return False
        except Exception as exc:
            summary["failures"] += 1
            self._record_failure(subscription, type(exc).__name__)
            self.status.add_event(
                "error",
                "push_fail",
                f"群 {group_id} 推送异常：{type(exc).__name__}",
                group_id=group_id,
                platform_id=platform_id,
            )
            logger.warning(
                "推送新游戏到群 %s 异常: %s", group_id, type(exc).__name__
            )
            return False

        summary["pushes"] += 1
        if downgraded:
            summary["downgraded"] += 1
            self.status.add_event(
                "warning",
                "push_fallback",
                f"群 {group_id} 图片上传失败，已改发纯文字",
                group_id=group_id,
                platform_id=platform_id,
            )
        self.status.record_delivery(
            platform_id, group_id, ok=True, fallback=downgraded,
        )
        return True

    def _record_failure(self, subscription: dict, error: str) -> None:
        self.status.record_delivery(
            subscription.get("platform_id"),
            subscription["group_id"],
            ok=False,
            error=error,
        )

    # ---- 工具 ----

    @staticmethod
    def _fmt_card(g: GameBrief) -> str:
        lines = [f"游戏名：{g.title}"]
        if g.release_date: lines.append(f"游戏发布日期：{g.release_date}")
        if g.post_date: lines.append(f"网站发布时间：{g.post_date}")
        if g.platforms: lines.append(f"平台：{' / '.join(g.platforms)}")
        if g.modes: lines.append(f"模式：{'、'.join(g.modes)}")
        # 玩家人数（仅详情页获取后才有值）
        if g.coop_players is not None or g.multi_players is not None:
            parts = []
            if g.coop_players is not None:
                parts.append(f"合作人数{g.coop_players}")
            if g.multi_players is not None:
                parts.append(f"多人人数{g.multi_players}")
            if parts:
                lines.append(f"人数：{'、'.join(parts)}")
        if g.update_info: lines.append(f"更新情况：{g.update_info}")
        url = g.url if g.url.startswith("http") else f"https://online-fix.me{g.url}"
        lines.append(f"详情页：{url}")
        return "\n".join(lines)

    @staticmethod
    def _parse_list_args(args: str) -> int:
        page = 1
        for p in args.split():
            if p.isdigit():
                page = max(1, int(p))
        return page

    def _get_bot(self, event: AstrMessageEvent | None = None):
        # Replies must use the account that received this command, not always
        # the first OneBot account in the platform manager.
        if event is not None and getattr(event, "bot", None) is not None:
            return event.bot
        for inst in self._aiocqhttp_insts():
            bot = self._inst_bot(inst)
            if bot is not None:
                return bot
        return None

    # ---- 多账号订阅 ----

    def _plugin_name(self) -> str:
        name = getattr(self, "name", None)
        return name if isinstance(name, str) and name else PLUGIN_NAME_FALLBACK

    @staticmethod
    def _event_platform_id(event) -> str | None:
        getter = getattr(event, "get_platform_id", None)
        if not callable(getter):
            return None
        try:
            platform_id = getter()
        except Exception:
            return None
        return str(platform_id) if platform_id else None

    @staticmethod
    def _inst_platform_id(inst) -> str | None:
        meta = getattr(inst, "meta", None)
        if callable(meta):
            try:
                meta = meta()
            except Exception:
                meta = None
        platform_id = getattr(meta, "id", None)
        if not platform_id:
            config = getattr(inst, "config", None)
            if isinstance(config, dict):
                platform_id = config.get("id")
        return str(platform_id) if platform_id else None

    @staticmethod
    def _inst_bot(inst):
        bot = getattr(inst, "bot", None)
        if bot is not None:
            return bot
        get_client = getattr(inst, "get_client", None)
        if callable(get_client):
            try:
                return get_client()
            except Exception:
                return None
        return None

    def _aiocqhttp_insts(self) -> list:
        insts = []
        for inst in self.context.platform_manager.platform_insts:
            meta = getattr(inst, "meta", None)
            if callable(meta):
                try:
                    meta = meta()
                except Exception:
                    meta = None
            if getattr(meta, "name", None) == "aiocqhttp":
                insts.append(inst)
        return insts

    def _find_platform(self, platform_id: str):
        for inst in self._aiocqhttp_insts():
            if self._inst_platform_id(inst) == platform_id:
                return inst
        return None

    async def _session_plugin_enabled(self, platform_id: str, group_id: str) -> bool:
        """Respect the per-session plugin disable list from the WebUI."""
        try:
            from astrbot.core.star.session_plugin_manager import SessionPluginManager
            return await SessionPluginManager.is_plugin_enabled_for_session(
                f"{platform_id}:{GROUP_MESSAGE_TYPE}:{group_id}",
                self._plugin_name(),
            )
        except Exception:
            # The helper is optional; never block pushes when it is missing.
            return True

    async def _plugin_allowed(self, platform_id: str, group_id: str) -> bool:
        """Check the config profile bound to ``platform_id`` for this plugin.

        AstrBot profiles select plugins via ``plugin_set``; a background task
        is not filtered by that list, so the pusher has to check it itself.
        """
        get_config = getattr(self.context, "get_config", None)
        if not callable(get_config):
            return await self._session_plugin_enabled(platform_id, group_id)
        try:
            config = get_config(umo=f"{platform_id}:{GROUP_MESSAGE_TYPE}:{group_id}")
            allowed = config.get("plugin_set", ["*"])
        except Exception:
            return await self._session_plugin_enabled(platform_id, group_id)
        if (
            isinstance(allowed, (list, tuple))
            and list(allowed) != ["*"]
            and self._plugin_name() not in allowed
        ):
            return False
        return await self._session_plugin_enabled(platform_id, group_id)

    @staticmethod
    def _normalize_subscriptions(raw) -> list[dict]:
        """Accept both the legacy ``["group_id"]`` list and the new records."""
        subscriptions: list[dict] = []
        if not isinstance(raw, list):
            return subscriptions
        for item in raw:
            if isinstance(item, str):
                item = {"group_id": item}
            if not isinstance(item, dict):
                continue
            group_id = item.get("group_id")
            if not group_id:
                continue
            platform_id = item.get("platform_id")
            self_id = item.get("self_id")
            subscriptions.append({
                "group_id": str(group_id),
                "platform_id": str(platform_id) if platform_id else None,
                "self_id": str(self_id) if self_id else None,
            })
        return subscriptions

    async def evaluate_subscription(self, subscription: dict) -> dict:
        """Read-only routing evaluation used by pushes and the WebUI."""
        group_id = subscription["group_id"]
        platform_id = subscription.get("platform_id")

        if platform_id:
            inst = self._find_platform(platform_id)
            if inst is not None:
                if not await self._plugin_allowed(platform_id, group_id):
                    return {
                        "state": "plugin_disabled",
                        "platform_id": platform_id,
                        "bot": None,
                    }
                bot = self._inst_bot(inst)
                if bot is None:
                    return {
                        "state": "bot_unavailable",
                        "platform_id": platform_id,
                        "bot": None,
                    }
                return {
                    "state": "ready",
                    "platform_id": platform_id,
                    "bot": bot,
                }

        # Legacy entries (or a renamed platform) only have the group id, so
        # pick the first account whose profile still enables this plugin.
        candidates: list[tuple[str, object]] = []
        for inst in self._aiocqhttp_insts():
            candidate_id = self._inst_platform_id(inst)
            if not candidate_id or not await self._plugin_allowed(
                candidate_id, group_id
            ):
                continue
            bot = self._inst_bot(inst)
            if bot is not None:
                candidates.append((candidate_id, bot))
        if candidates:
            return {
                "state": "legacy" if not platform_id else "platform_missing",
                "platform_id": candidates[0][0],
                "bot": candidates[0][1],
            }

        if any(self._inst_platform_id(inst) for inst in self._aiocqhttp_insts()):
            return {
                "state": "platform_missing" if platform_id else "no_eligible_account",
                "platform_id": None,
                "bot": None,
            }
        # Custom/older adapters may not expose a platform id; keep the old
        # first-account behavior for them instead of dropping the push.
        bot = self._get_bot()
        return {
            "state": "ready" if bot is not None else "no_eligible_account",
            "platform_id": platform_id,
            "bot": bot,
        }

    async def _resolve_subscription_bot(self, subscription: dict):
        """Return the account that should deliver this group's push."""
        group_id = subscription["group_id"]
        platform_id = subscription.get("platform_id")
        evaluation = await self.evaluate_subscription(subscription)
        state = evaluation["state"]
        bot = evaluation["bot"]
        resolved = evaluation["platform_id"]

        if state == "plugin_disabled":
            logger.warning(
                "平台 %s 未启用本插件（配置文件或会话规则），跳过群 %s 的推送",
                platform_id, group_id,
            )
            return None
        if state == "platform_missing":
            logger.warning(
                "订阅所属平台 %s 未加载，将在其他账号中重新选择", platform_id,
            )
        elif state == "no_eligible_account":
            logger.warning("群 %s 没有启用本插件的机器人账号，跳过推送", group_id)
            return None
        if bot is None:
            logger.warning(
                "平台 %s 的机器人实例不可用，跳过群 %s 的推送",
                resolved or platform_id, group_id,
            )
            return None
        if resolved and resolved != platform_id:
            subscription["platform_id"] = resolved
            self.status.add_event(
                "info",
                "migrate",
                f"群 {group_id} 的订阅已迁移到 {resolved}",
                group_id=group_id,
                platform_id=resolved,
            )
        return bot

    async def _bot_self_id(self, bot) -> str | None:
        self_id = getattr(bot, "self_id", None)
        if self_id:
            return str(self_id)
        try:
            login_info = await call_action(bot, "get_login_info")
        except OneBotActionError as e:
            logger.warning("获取订阅推送机器人账号失败: %s", e.diagnostic())
            return None
        if isinstance(login_info, dict) and isinstance(login_info.get("data"), dict):
            login_info = login_info["data"]
        user_id = login_info.get("user_id") if isinstance(login_info, dict) else None
        return str(user_id) if user_id else None

    async def _card_segments(self, g: GameBrief) -> list[dict]:
        content = []
        if self.config.get("send_game_posters", True) and g.poster_url:
            b64_url = await self._prepare_image(g.poster_url, g.title)
            if b64_url:
                content.append({"type": "image", "data": {"file": b64_url}})
        content.append({"type": "text", "data": {"text": self._fmt_card(g)}})
        return content

    async def _build_nodes(self, games, self_id: str, nickname: str) -> list[dict]:
        nodes = []
        for g in games:
            nodes.append({"type": "node", "data": {
                "user_id": self_id, "nickname": nickname,
                "content": await self._card_segments(g),
            }})
        return nodes

    async def _send_group_with_fallback(self, bot, action: str, group_id: int, **message) -> bool:
        payload = {"group_id": group_id, **message}
        try:
            await call_action(bot, action, **payload)
            return False
        except OneBotActionError as e:
            logger.warning("游戏消息发送失败: %s", e.diagnostic())
            if not self.config.get("fallback_to_text_on_upload_failure", True) or not e.media_upload_failed:
                raise
            fallback = text_only_payload(action, payload)
            if fallback is None:
                raise
            logger.warning("游戏封面上传明确失败，尝试一次纯文字降级: action=%s", action)
            # No loop, no image re-upload, no retry on an ambiguous timeout.
            await call_action(bot, action, **fallback)
            logger.info("游戏信息纯文字降级发送成功: action=%s", action)
            return True

    async def _prepare_image(self, poster_url: str, title: str) -> str | None:
        """Download/validate once, then use OneBot's shared base64 image format."""
        logger.info("[IMG] 游戏=%s | URL=%s", title, poster_url)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://online-fix.me/",
        }
        max_bytes = 5 * 1024 * 1024
        try:
            # A HEAD per poster doubles requests and can fail independently of
            # GET. Reuse one HTTP client and read GET headers instead.
            async with self._image_client.stream(
                "GET", poster_url, headers=headers, follow_redirects=True,
            ) as response:
                response.raise_for_status()
                logger.debug("[IMG] GET status=%s | Content-Type=%s | Content-Length=%s",
                             response.status_code, response.headers.get("content-type", "?"),
                             response.headers.get("content-length", "?"))
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("poster exceeds 5 MiB")
                    chunks.append(chunk)
                img_bytes = b"".join(chunks)
            with PILImage.open(io.BytesIO(img_bytes)) as img:
                width, height, image_format, mode = img.width, img.height, img.format, img.mode
                img.verify()
            logger.info("[IMG] 下载成功 → %s bytes | %sx%s | %s | %s",
                        len(img_bytes), width, height, image_format, mode)
            return "base64://" + base64.b64encode(img_bytes).decode("ascii")
        except Exception as e:
            logger.warning("[IMG] 下载或校验失败，改为纯文字: %s: %s", type(e).__name__, sanitize_detail(str(e)))
            return None
