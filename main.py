"""astrbot_plugin_getGameInfo — 游戏插件"""

import asyncio
import base64
import io
import json
from pathlib import Path

import httpx
from PIL import Image as PILImage

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .models import GameBrief
from .onebot_sender import OneBotActionError, call_action, sanitize_detail, text_only_payload
from .scraper import BaseGameScraper, OnlineFixScraper

SCRAPER_REGISTRY: dict[str, type[BaseGameScraper]] = {"onlinefix": OnlineFixScraper}


class GameDownPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._scraper: BaseGameScraper | None = None
        self._poll_new_task: asyncio.Task | None = None
        self._image_client = httpx.AsyncClient(timeout=config.get("request_timeout", 30))

    async def initialize(self):
        cls = SCRAPER_REGISTRY.get("onlinefix", OnlineFixScraper)
        self._scraper = cls(
            timeout=self.config.get("request_timeout", 30),
        )
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
        finally:
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
        subs: list = await self.get_kv_data("subscriptions", [])
        if gid in subs:
            yield event.plain_result("本群已订阅。"); return
        subs.append(gid)
        await self.put_kv_data("subscriptions", subs)
        yield event.plain_result("已订阅新游戏推送。\n发送 /game unsubscribe 取消。")

    async def _cmd_unsubscribe(self, event: AstrMessageEvent):
        gid = event.get_group_id()
        if not gid:
            yield event.plain_result("仅支持在群聊中操作。"); return
        subs: list = await self.get_kv_data("subscriptions", [])
        if gid not in subs:
            yield event.plain_result("本群未订阅。"); return
        subs.remove(gid)
        await self.put_kv_data("subscriptions", subs)
        yield event.plain_result("已取消新游戏推送。")

    # ---- 新游戏轮询 ----

    async def _poll_new_games(self):
        """定时轮询新游戏，按当前时钟时间对齐整点执行。

        例如 interval=7200（2小时），则在 00:00、02:00、04:00… 执行，
        而非基于插件启动时间偏移。
        """
        import time

        interval = max(self.config.get("game_check_interval", 21600), 1800)

        while True:
            # 计算距离下一个对齐时刻的秒数
            now = time.time()
            next_tick = ((now // interval) + 1) * interval
            wait = next_tick - now
            logger.info(f"下次新游戏检查将在 {wait:.0f} 秒后（{time.strftime('%H:%M:%S', time.localtime(next_tick))}）")
            await asyncio.sleep(wait)

            try:
                subs: list = await self.get_kv_data("subscriptions", [])
                if subs:
                    games, _ = await self._scraper.fetch_list(1)
                    if games:
                        await self._push_new_games(subs, games)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("新游戏轮询异常")

    async def _push_new_games(self, subs: list, games):
        data_dir = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_getGameInfo"
        seen_path = data_dir / "seen_games.json"
        seen = json.loads(seen_path.read_text("utf-8") or "{}") if seen_path.exists() else {}

        current_ids = [g.id for g in games]
        old_ids = seen.get("onlinefix", [])
        if not old_ids:
            seen["onlinefix"] = current_ids
            try:
                data_dir.mkdir(parents=True, exist_ok=True)
                seen_path.write_text(json.dumps(seen, ensure_ascii=False, indent=2))
            except Exception as e:
                logger.warning(f"写入 seen_games.json 失败: {e}")
            return

        new_ids = [i for i in current_ids if i not in old_ids]
        if not new_ids:
            return

        new_games = [g for g in games if g.id in new_ids]
        bot = self._get_bot()
        if not bot:
            return

        if len(new_games) == 1:
            g = new_games[0]
            msg = await self._card_segments(g)
            msg[-1]["data"]["text"] = (
                f"新游戏上架！\n\n{self._fmt_card(g)}\n\n发送 /game info {g.title[:20]} 查看详情"
            )
            for gid in subs:
                try:
                    await self._send_group_with_fallback(
                        bot, "send_group_msg", int(gid), message=msg,
                    )
                except OneBotActionError as e:
                    logger.warning("推送新游戏到群 %s 失败: %s", gid, e.diagnostic())
                except Exception as e:
                    logger.warning("推送新游戏到群 %s 异常: %s", gid, type(e).__name__)
        else:
            self_id = getattr(bot, "self_id", None)
            if not self_id:
                try:
                    login_info = await call_action(bot, "get_login_info")
                    if isinstance(login_info, dict) and isinstance(login_info.get("data"), dict):
                        login_info = login_info["data"]
                    self_id = login_info.get("user_id") if isinstance(login_info, dict) else None
                except OneBotActionError as e:
                    logger.warning("获取订阅推送机器人账号失败: %s", e.diagnostic())
                    return
            if not self_id:
                logger.warning("无法确定订阅推送机器人账号，暂不生成转发节点")
                return
            nodes = await self._build_nodes(new_games, str(self_id), "新游戏")
            for gid in subs:
                try:
                    await self._send_group_with_fallback(
                        bot, "send_group_forward_msg", int(gid), messages=nodes,
                    )
                    await call_action(bot, "send_group_msg", group_id=int(gid), message=[{
                        "type": "text", "data": {"text":
                            f"新游戏上架 Online-Fix.me（{len(new_games)} 款）\n发送 /game info <游戏名> 查看详情"},
                    }])
                except OneBotActionError as e:
                    logger.warning("推送新游戏到群 %s 失败: %s", gid, e.diagnostic())
                except Exception as e:
                    logger.warning("推送新游戏到群 %s 异常: %s", gid, type(e).__name__)

        seen["onlinefix"] = current_ids
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            seen_path.write_text(json.dumps(seen, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.warning(f"更新 seen_games.json 失败: {e}")

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
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import AiocqhttpAdapter
        for p in self.context.platform_manager.platform_insts:
            if isinstance(p, AiocqhttpAdapter):
                return p.bot
        return None

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
