"""astrbot_plugin_getGameInfo — 游戏插件"""

import asyncio
import json
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
import astrbot.api.message_components as Comp
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .models import GameBrief
from .scraper import BaseGameScraper, OnlineFixScraper

SCRAPER_REGISTRY: dict[str, type[BaseGameScraper]] = {"onlinefix": OnlineFixScraper}


class GameDownPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._scraper: BaseGameScraper | None = None
        self._poll_new_task: asyncio.Task | None = None

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
        if self._scraper:
            await self._scraper.close()

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
        except Exception as e:
            logger.exception(f"命令异常: {e}")
            yield event.plain_result(f"错误: {e}")

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

        bot = self._get_bot()
        if not bot:
            yield event.plain_result("无法获取 bot 实例。")
            return

        nodes = []
        for g in games:
            content = []
            if g.poster_url:
                content.append({"type": "image", "data": {"file": g.poster_url}})
            content.append({"type": "text", "data": {"text": self._fmt_card(g)}})
            nodes.append({"type": "node", "data": {"user_id": event.get_self_id(), "nickname": "Online-Fix.me", "content": content}})

        try:
            await bot.call_action("send_group_forward_msg", group_id=group_id, messages=nodes)
        except Exception as e:
            logger.error(f"合并转发失败: {e}")
            yield event.plain_result(f"发送失败: {e}")

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
        if len(games) == 1:
            g = games[0]
            chain = [Comp.Reply(id=event.message_obj.message_id)]
            if g.poster_url:
                chain.append(Comp.Image(file=g.poster_url))
            chain.append(Comp.Plain(self._fmt_card(g)))
            yield event.chain_result(chain)
        else:
            group_id = int(event.get_group_id()) if event.get_group_id() else 0
            if group_id == 0:
                lines = [f"'{args}' 搜索结果（{len(games)} 个）："]
                for g in games:
                    lines.append(f"  {g.title}")
                yield event.plain_result("\n".join(lines))
                return
            bot = self._get_bot()
            if not bot:
                yield event.plain_result("无法获取 bot 实例。")
                return
            nodes = []
            for g in games:
                content = []
                if g.poster_url:
                    content.append({"type": "image", "data": {"file": g.poster_url}})
                content.append({"type": "text", "data": {"text": self._fmt_card(g)}})
                nodes.append({"type": "node", "data": {"user_id": event.get_self_id(), "nickname": "Online-Fix.me", "content": content}})
            try:
                await bot.call_action("send_group_forward_msg", group_id=group_id, messages=nodes)
            except Exception as e:
                logger.error(f"合并转发失败: {e}")
                yield event.plain_result(f"发送失败: {e}")

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
        interval = max(self.config.get("game_check_interval", 21600), 1800)
        await asyncio.sleep(60)
        while True:
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
            await asyncio.sleep(interval)

    async def _push_new_games(self, subs: list, games):
        data_dir = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_getGameInfo"
        seen_path = data_dir / "seen_games.json"
        seen = json.loads(seen_path.read_text("utf-8") or "{}") if seen_path.exists() else {}

        current_ids = [g.id for g in games]
        old_ids = seen.get("onlinefix", [])
        if not old_ids:
            seen["onlinefix"] = current_ids
            data_dir.mkdir(parents=True, exist_ok=True)
            seen_path.write_text(json.dumps(seen, ensure_ascii=False, indent=2))
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
            msg = [{"type": "image", "data": {"file": g.poster_url}}] if g.poster_url else []
            msg.append({"type": "text", "data": {"text": f"新游戏上架！\n\n{self._fmt_card(g)}\n\n发送 /game info {g.title[:20]} 查看详情"}})
            for gid in subs:
                try:
                    await bot.call_action("send_group_msg", group_id=int(gid), message=msg)
                except Exception:
                    pass
        else:
            nodes = []
            for g in new_games:
                content = [{"type": "image", "data": {"file": g.poster_url}}] if g.poster_url else []
                content.append({"type": "text", "data": {"text": self._fmt_card(g)}})
                nodes.append({"type": "node", "data": {"user_id": str(bot.self_id) if hasattr(bot, "self_id") else "", "nickname": "新游戏", "content": content}})
            for gid in subs:
                try:
                    await bot.call_action("send_group_forward_msg", group_id=int(gid), messages=nodes)
                    await bot.call_action("send_group_msg", group_id=int(gid),
                        message=[{"type": "text", "data": {"text": f"新游戏上架 Online-Fix.me（{len(new_games)} 款）\n发送 /game info <游戏名> 查看详情"}}])
                except Exception:
                    pass

        seen["onlinefix"] = current_ids
        data_dir.mkdir(parents=True, exist_ok=True)
        seen_path.write_text(json.dumps(seen, ensure_ascii=False, indent=2))

    # ---- 工具 ----

    @staticmethod
    def _fmt_card(g: GameBrief) -> str:
        lines = [f"游戏名：{g.title}"]
        if g.release_date: lines.append(f"游戏发布日期：{g.release_date}")
        if g.post_date: lines.append(f"网站发布时间：{g.post_date}")
        if g.platforms: lines.append(f"平台：{' / '.join(g.platforms)}")
        if g.modes: lines.append(f"模式：{'、'.join(g.modes)}")
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

    def _get_bot(self):
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import AiocqhttpAdapter
        for p in self.context.platform_manager.platform_insts:
            if isinstance(p, AiocqhttpAdapter):
                return p.bot
        return None
