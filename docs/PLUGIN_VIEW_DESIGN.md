# getGameInfo 插件可视化视图 — 功能设计方案（讨论稿 v0.1）

> 目标：在 AstrBot WebUI 中提供一个「运行状态 + 订阅 + 游戏数据」控制台，让轮询、订阅路由、推送结果和错误一眼可见。
> 状态：P1 在 v0.0.4、P2 在 v0.0.5、P3 在 v0.0.6 实现。P3 包含近 7 天历史图表、seen 基线维护、诊断导出、从群列表添加订阅和测试失败回放。视图目录为 `pages/status/`。按既定选择继续使用 10 秒轮询，未引入 SSE；界面保持中文。

## 1. 背景与约束

AstrBot 的插件视图（Plugin Views）机制要点（已核对 v4.29.1 源码与官方文档）：

- 视图放在插件目录 `views/<view_name>/index.html`，AstrBot 自动扫描，无需改 `metadata.yaml`；`pages/` 是兼容别名。
- 本插件最低支持 AstrBot 4.25.1，而 4.25.x 只扫描 `pages/`，所以实现放在 `pages/status/`；4.29.x 同时兼容。i18n 同时写 `views.status` 与 `pages.status` 两套键。
- 视图运行在受限 iframe 中，只能通过 `window.AstrBotPluginView`（别名 `AstrBotPluginPage`）访问后端，不能读 Dashboard cookie / localStorage / 父页面 DOM。
- 后端用 `context.register_web_api(route, handler, ["GET"], desc)` 注册 API；route 必须以 `/{PLUGIN_NAME}` 开头，视图端调用时不需要带插件名前缀。
- 后端推荐使用 `astrbot.api.web` 的 `request` / `json_response` / `error_response` / `file_response` / `stream_response`。
- 视图标题和描述写在 `.astrbot-plugin/i18n/<locale>.json` 的 `views.<view_name>` 下；主题通过 `<html data-theme="light|dark">` 和 CSS 变量适配。
- 视图静态资源用相对路径即可，AstrBot 会通过路径令牌鉴权；不要手拼 `asset_token`。
- 官方文档：<https://docs.astrbot.app/dev/star/guides/plugin-pages.html>

参考插件 `astrbot_plugin_qq_group_daily_analysis` 的做法：

- `pages/daily-analysis/` 放 Vite 构建产物，`dashboard/` 放 React + Ant Design 源码，构建输出到 `pages/`。
- 在 `__init__` 中实例化 WebUI bridge 并调用 `register_routes()`，路由统一以 `/{PLUGIN_NAME}/...` 注册。
- 前端通过 bridge 的 `apiGet` / `apiPost` / `subscribeSSE` 访问后端，SSE 用于任务和日志实时更新。

对本插件来说，那套 React 工程偏重；本方案推荐**无构建的静态页面**（HTML + CSS + 原生 ES Module），避免引入 Node 工具链。

## 2. 目标与非目标

### 目标

1. 打开页面即可回答：插件是否在跑、下次什么时候检查、上次检查结果、哪些群订阅了、推送会从哪个机器人发出、有没有失败。
2. 查看最近检查/推送事件，快速定位「为什么没推送」「为什么是 B 机器人发的」。
3. 浏览站点最新游戏，标记哪些是 seen 基线之外的新游戏。
4. 提供少量低风险操作：刷新、仅检查（dry-run）、第二阶段再加入手动检查并推送、测试推送、取消订阅。

### 非目标

- 不复制 AstrBot 的插件配置页；`request_timeout`、间隔、封面开关等仍在插件配置中修改，视图只读展示。
- 不做完整的游戏资源站替代品；不做下载、搜索历史、用户系统。
- 不在视图内直接操作 Dashboard 的配置或数据库。

## 3. 信息架构

单页 + 顶部状态栏 + 4 个 Tab：

```
┌────────────────────────────────────────────────────────────────────┐
│ getGameInfo 控制台        ● 运行中 · v0.0.3 · AstrBot 4.29.1      │
│ 下次检查 14:00:00（还有 1 小时 23 分）        [刷新] [自动刷新 10s]│
├──────────────┬──────────────┬──────────────┬─────────────────────┤
│ 订阅群 3     │ 追踪游戏 21  │ 今日新游戏 2 │ 今日推送失败 0      │
├──────────────┴──────────────┴──────────────┴─────────────────────┤
│ [概览] [订阅 3] [游戏] [事件]                                     │
├────────────────────────────────────────────────────────────────────┤
│ 概览：最近检查 / 运行环境 / 账号 / 最近事件                         │
│ 订阅：群 + 平台 + 推送账号 + 可推送状态 + 最近投递 + 操作           │
│ 游戏：封面卡片网格 + 新游戏标记 + 搜索 + 详情                       │
│ 事件：按级别/类型过滤的时间线                                       │
└────────────────────────────────────────────────────────────────────┘
```

### 3.1 概览（默认 Tab）

- **状态胶囊**：插件已加载 / 未初始化、轮询任务运行中、检查锁占用中。
- **下次检查卡片**：间隔（6 小时）、下次时间、倒计时；用服务端 `server_time` 校准，避免客户端时钟偏差。
- **上次检查卡片**：触发方式（定时/手动）、开始/结束时间、耗时、抓取数量、新增数量、推送成功/失败、是否发生纯文字降级、错误摘要。
- **KPI**：订阅群数、追踪游戏数（seen 基线）、今日新游戏、今日推送失败。
- **账号列表**：平台 ID、self_id、运行状态、该账号下订阅数、最近一次投递结果。
- **运行环境**：站点可达性/延迟、`request_timeout`、`game_check_interval`、`max_display_items`、`send_game_posters`、`fallback_to_text_on_upload_failure`。
- **最近事件**：最近 8–10 条，点击跳到「事件」Tab。
- **操作**：
  - `刷新`：只刷新当前 Tab 数据。
  - `仅检查`：dry-run，只抓列表并返回「如果现在推送，会推哪些」，不发消息、不更新 seen。
  - `检查并推送`（P2）：执行一次完整轮询；二次确认。

### 3.2 订阅

表格列：

| 列 | 说明 |
| --- | --- |
| 群号 | 群 ID，可复制 |
| 平台 / 账号 | `platform_id` + self_id，如 `bot-a / 111` |
| 推送状态 | 可推送 / 平台未加载 / 配置文件未启用本插件 / 旧订阅待迁移 / 机器人实例不可用 |
| 最近投递 | 时间 + 成功/失败 + 是否降级；来自运行时投递记录 |
| 操作 | 取消订阅（P2）、测试推送（P2） |

补充：

- 顶部汇总：总订阅数、可推送数、被权限阻止数、旧格式数量。
- 对「旧订阅」解释：旧版只存群号，会在下次推送时自动迁移；也可用目标机器人重新 `/game subscribe`。
- 空状态给出 `/game subscribe` 使用提示。
- 取消订阅需要二次确认，并按群清理所有账号记录（与当前命令行为一致）。
- 暂不做「从 UI 新增订阅」：需要枚举机器人所在群，涉及 OneBot `get_group_list`，放 P3 讨论。

### 3.3 游戏

- 顶部：`刷新`、`搜索`（调用站点搜索）、数据时间戳。
- 默认展示站点第一页（受 `max_display_items` 影响），卡片网格：
  - 封面（`loading="lazy"`，`referrerpolicy="no-referrer"`，失败显示占位图）
  - 标题、发布日期、网站发布时间、平台、模式
  - 「新」徽标：ID 不在 `seen_games.json` 中
  - 详情链接：视图 iframe 的 sandbox 是 `allow-scripts allow-forms allow-downloads`，**没有 `allow-popups`**，因此不能直接 `window.open` 外链；详情抽屉里显示完整 URL + 「复制链接」按钮（`document.execCommand("copy")` 兜底），不做跳转。
  - 点击卡片打开详情抽屉：详情页玩家人数（合作/多人）、更新情况
- 数据缓存 60 秒，避免每次切 Tab 都请求站点；手动刷新传 `force=1`。
- 搜索长度限制 1–100，结果最多 50 条，复用 scraper 的限速。

### 3.4 事件

- 环形缓冲最近 200 条，字段：时间、级别（info/warning/error）、类型、消息、群号、平台、游戏名。
- 类型：`check_start`、`check_ok`、`check_error`、`new_games`、`push_ok`、`push_fail`、`push_fallback`、`subscribe`、`unsubscribe`、`migrate`。
- 过滤：全部 / 检查 / 推送 / 错误；关键字搜索。
- 错误消息沿用 `sanitize_detail`，不显示 URL 参数、base64、凭据、本地路径。
- P2 可选 SSE 实时追加；P1 用 10 秒轮询。

## 4. 后端 API 设计

统一前缀：`/astrbot_plugin_getGameInfo`。视图端调用 `apiGet("status")`、`apiPost("check", {...})`。

| 方法 | 路由 | 用途 | 阶段 |
| --- | --- | --- | --- |
| GET | `/status` | 运行状态、调度、计数、账号、配置摘要、健康检查 | P1 |
| GET | `/subscriptions` | 订阅列表 + 路由/权限状态 + 最近投递 | P1 |
| GET | `/events` | 最近事件，`limit`、`kind`、`level`、`q` | P1 |
| GET | `/games` | 站点列表，`page`、`limit`、`force` | P1 |
| GET | `/games/search` | 搜索，`q`、`page` | P1 |
| GET | `/games/detail` | 详情页人数，`url`（域名白名单） | P1 |
| POST | `/check` | `{"mode":"dry_run"}`；P2 支持 `"push"` | P1/P2 |
| POST | `/subscriptions/remove` | `{"group_id","platform_id"?}` | P2 |
| POST | `/subscriptions/test` | 向指定订阅发测试消息 | P2 |
| POST | `/maintenance/seen` | 重置/清空 seen 基线 | P3 |

### 4.1 `GET /status` 返回示例

```json
{
  "server_time": "2026-10-24T13:20:00+08:00",
  "plugin": {
    "name": "astrbot_plugin_getGameInfo",
    "display_name": "getGameInfo",
    "version": "v0.0.4",
    "astrbot_version": "4.29.1"
  },
  "runtime": {
    "started_at": "2026-10-24T10:00:00+08:00",
    "uptime_seconds": 12000,
    "scraper_ready": true,
    "poll_task_running": true,
    "checking": false
  },
  "schedule": {
    "interval_seconds": 21600,
    "next_check_at": "2026-10-24T14:00:00+08:00",
    "seconds_until_next": 2400
  },
  "last_check": {
    "trigger": "schedule",
    "started_at": "2026-10-24T08:00:02+08:00",
    "finished_at": "2026-10-24T08:00:04+08:00",
    "duration_ms": 2100,
    "ok": true,
    "games_seen": 21,
    "new_games": 2,
    "pushes": 3,
    "failures": 0,
    "downgraded": 0,
    "error": null
  },
  "counts": {
    "subscriptions": 3,
    "groups": 3,
    "accounts": 2,
    "tracked_games": 21,
    "new_games_today": 2,
    "pushes_today": 6,
    "failures_today": 0
  },
  "accounts": [
    {
      "platform_id": "bot-a",
      "self_id": "111",
      "status": "running",
      "subscriptions": 2,
      "last_delivery_ok": true
    }
  ],
  "config": {
    "request_timeout": 30,
    "game_check_interval": 21600,
    "max_display_items": 21,
    "send_game_posters": true,
    "fallback_to_text_on_upload_failure": true
  },
  "health": {
    "site": "https://online-fix.me",
    "reachable": true,
    "latency_ms": 234,
    "checked_at": "2026-10-24T13:19:40+08:00"
  }
}
```

### 4.2 `GET /subscriptions` 返回示例

```json
{
  "items": [
    {
      "group_id": "123456",
      "platform_id": "bot-a",
      "self_id": "111",
      "state": "ready",
      "state_label": "可推送",
      "last_delivery": {
        "at": "2026-10-24T08:00:03+08:00",
        "ok": true,
        "fallback": false,
        "new_games": 2
      }
    },
    {
      "group_id": "654321",
      "platform_id": "bot-b",
      "self_id": "222",
      "state": "plugin_disabled",
      "state_label": "配置文件未启用本插件"
    }
  ],
  "summary": {
    "total": 2,
    "ready": 1,
    "blocked": 1,
    "legacy": 0
  }
}
```

状态枚举：`ready`、`legacy`、`plugin_disabled`、`platform_missing`、`bot_unavailable`、`session_disabled`。

### 4.3 `GET /games` 返回示例

```json
{
  "fetched_at": "2026-10-24T13:18:00+08:00",
  "cached": false,
  "items": [
    {
      "id": "3063938452",
      "title": "Game Title",
      "url": "https://online-fix.me/games/3063938452-game.html",
      "poster_url": "https://online-fix.me/uploads/posts/2026-08/3063938452_poster.jpg",
      "post_date": "2026-08-01",
      "release_date": "2026",
      "platforms": ["Steam"],
      "modes": ["合作模式"],
      "update_info": "更新于2026年8月1日",
      "is_new": true
    }
  ]
}
```

### 4.4 `POST /check`

```json
// dry_run（P1）
{"mode": "dry_run"}
// 返回
{
  "mode": "dry_run",
  "ok": true,
  "duration_ms": 900,
  "games_seen": 21,
  "new_games": [{"id": "3063938452", "title": "Game Title"}]
}
```

- 检查锁已占用时返回 `409` + `error_response("已有检查正在进行")`。
- `dry_run` 不写 `seen_games.json`、不发消息；P2 的 `push` 模式走与定时任务完全相同的路径。
- 若 `seen_games.json` 还没有基线，`dry_run` 返回 `baseline_missing: true`，说明当前所有游戏都会被当作新游戏，但仍不写文件。
- 手动检查与定时检查共享同一个 `asyncio.Lock`，并加 10 秒冷却，避免连续点击打爆站点。

### 4.5 响应约定

- 成功：直接返回业务 JSON（bridge 会 resolve 为完整 JSON）。
- 失败：`error_response("可读错误", status_code=4xx/5xx)`；不要把异常堆栈返回给视图。
- 所有列表接口限制 `limit <= 100`。

## 5. 运行时状态与数据来源

当前插件只有 KV 订阅、`seen_games.json` 和内存中的 scraper，缺少可展示的「上次检查/错误/投递」状态。需要新增一个轻量运行时状态模块。

### 5.1 新增 `status.py`

- `RuntimeState`：
  - `started_at`、`last_check`、`next_check_at`
  - 计数器：`checks`、`new_games`、`pushes`、`failures`、`downgraded`（按本地日期分桶）
  - `events: deque(maxlen=200)`，内存环形缓冲
  - `deliveries: dict[(platform_id, group_id)] -> {at, ok, fallback, error?}`
  - `snapshot()`：提供给 `/status`
- 线程模型：只在事件循环内访问，无需额外锁；检查用独立 `asyncio.Lock`。
- 持久化：把 `last_check`、昨日计数、最近投递写入 `data/plugin_data/astrbot_plugin_getGameInfo/status.json`（原子替换），启动时读取；事件流不持久化（P2 可加 `history.json`）。
- 所有对外错误文本经过 `sanitize_detail`。

### 5.2 `main.py` 改动

- `__init__`：创建 `RuntimeState`、`asyncio.Lock`，注册视图 API（`webui.py` 中的 `register_view_apis(self, context)`）。
- `initialize`：读取持久化状态；计算首次 `next_check_at`。
- `_poll_new_games` 重构为：
  - `_next_tick()`：按整点对齐计算下次时间；
  - `_run_check(trigger)`：加锁 → 抓列表 → 推送 → 记录状态；定时和手动共用。
- `_push_new_games` 返回摘要：`new_games`、`pushes`、`failures`、`downgraded`、`skipped`；逐群记录投递结果。
- `_cmd_subscribe` / `_cmd_unsubscribe` 记录 `subscribe` / `unsubscribe` 事件。

### 5.3 数据读取

- 订阅：现有 KV `subscriptions`，用 `_normalize_subscriptions` 兼容旧格式。
- 追踪基线：`seen_games.json` 的 `onlinefix` 列表。
- 路由状态：复用 `_resolve_subscription_bot` 的判断（平台是否存在、`plugin_set`、会话禁用），但状态接口只做只读评估，不发送。
- 健康检查：`GET` 站点首页或列表页，记录状态码/延迟，缓存 30–60 秒；失败不抛出到视图。

## 6. 前端实现

### 6.1 技术选型

推荐：**无构建静态页面**。

```
pages/status/
├─ index.html
├─ app.js            # 入口：bridge.ready、Tab 路由、自动刷新
├─ api.js            # bridge.apiGet/apiPost 封装 + 错误提示
├─ ui.js             # 转义、时间格式化、Toast、确认框、空状态
├─ pages/
│  ├─ overview.js
│  ├─ subscriptions.js
│  ├─ games.js
│  └─ events.js
└─ style.css
```

- 原生 ES Module，浏览器直接加载；不需要 Vite/React，插件仓库不增加 Node 依赖。
- 不使用外部 CDN；图表用内联 SVG/CSS 手绘（如最近 10 次检查耗时柱状图）。
- 视图使用 hash 路由（`#overview`、`#subscriptions`），刷新后停留在原 Tab；官方建议 SPA 用 hash routing。
- iframe 没有 `allow-same-origin`：`localStorage` / `sessionStorage` / `document.cookie` 都不可用（访问会抛异常），不要用它们存 Tab 或自动刷新偏好；偏好只放内存，需要持久化时写到后端。
- 不能直接 `fetch` Dashboard API（拿不到 Authorization），所有请求必须走 bridge。

### 6.2 主题与视觉

- 通过 `[data-theme="dark"]` 覆盖 CSS 变量，与 AstrBot 明暗主题同步。
- 视觉层级：
  - 卡片使用 1px 边框 + 轻阴影，圆角 10–12px；
  - 状态色：绿（正常）、黄（降级/待迁移）、红（失败）、灰（禁用）；
  - 表格在窄屏改为卡片列表；
  - 所有按钮有 hover/focus-visible；`prefers-reduced-motion` 下关闭动画。
- 图标使用内联 SVG（不使用 emoji），保持跨平台一致。

### 6.3 自动刷新

- 默认 10 秒刷新当前 Tab 的轻量数据（`/status`、`/events`）；`/games` 只在打开 Tab 或手动刷新时请求。
- `document.visibilityState === "hidden"` 时暂停；页面卸载时清理定时器。
- 顶部显示「最后更新 13:20:05」，请求失败显示重试按钮，不清空旧数据。

### 6.4 国际化

- `.astrbot-plugin/i18n/zh-CN.json`：

```json
{
  "views": {
    "status": {
      "title": "getGameInfo",
      "description": "查看轮询、订阅与推送状态"
    }
  }
}
```

- 视图内文案通过 `bridge.t("views.status.xxx", "默认中文")` 读取（4.25.x 回退到 `pages.status`）；第一版只提供 zh-CN。
- 修改 locale 文件后需要重载插件，才能刷新视图标题/描述。

### 6.5 封面图

- 已实测 `online-fix.me` 的封面在无 Referer 的普通 GET 下返回 200 JPEG，因此视图可以直接 `<img src="poster_url" referrerpolicy="no-referrer" loading="lazy">`。
- 若后续发现部分网络环境下防盗链拦截，再加后端代理：`GET /poster?url=...`，只允许 `online-fix.me` 域名，`stream_response` 返回图片流，限制 5 MiB。第一版不增加代理复杂度。
- 图片失败时显示统一占位块，不影响卡片布局。

## 7. 安全与权限

- 所有 API 走 Dashboard 认证与插件 scope，由 AstrBot 的 `/plugins/extensions/...` 路由保护；视图本身无法绕过 bridge 直接访问。
- 输入校验：
  - `group_id`：仅数字字符串，长度 5–20；
  - `platform_id`：必须存在于当前 `platform_insts`；
  - `q`：1–100 字符；`limit`：1–100；
  - `url`：仅 `https://online-fix.me` 及其子域，拒绝 IP、端口、账号密码、非 80/443；不跟随到白名单外的重定向（若做代理）。
- 操作限流：手动检查 10 秒冷却；测试推送 30 秒冷却；同一时间只允许一个检查。
- 错误脱敏：视图只拿到 `error_response` 的可读文本；日志/事件里的异常经过 `sanitize_detail`。
- 危险操作（重置 seen、测试推送、取消订阅）在 UI 中二次确认，文案明确写出影响范围。

## 8. 分阶段范围

### P1 — 只读状态面板（建议先做）

- `status.py` + 运行时状态记录。
- API：`/status`、`/subscriptions`、`/events`、`/games`、`/games/search`、`/games/detail`、`/check`（仅 dry_run）。
- 视图：顶部状态、KPI、概览、订阅表（只读）、游戏网格、事件列表、自动刷新。
- 测试：API 形状、dry-run 不写 seen、订阅状态评估、URL 校验。

### P2 — 安全操作与实时

- `/check` 支持 `push`；`/subscriptions/remove`；`/subscriptions/test`。
- SSE 推送事件（`bridge.subscribeSSE`），替代/补充 10 秒轮询。
- 最近投递持久化、事件持久化（最近 200 条）。
- 中英文 i18n 完整化。

### P3 — 历史与维护

- 新增 `history.json`：每日新游戏、推送成功率、检查耗时趋势；概览加 SVG 折线图。
- 维护：重置 seen 基线、清空 seen（需输入确认文本）、导出诊断 JSON。
- 从机器人所在群添加订阅（`get_group_list`）。
- 可选：推送测试的失败原因回放。

## 9. 测试与验收

### 自动化

- `tests/test_webui.py`：
  - `/status` 在初始化前/后都能返回安全结构；
  - 订阅状态：ready / legacy / plugin_disabled / platform_missing；
  - `POST /check` dry-run 不发送、不修改 seen；
  - `POST /check` 在检查锁占用时返回 409；
  - `POST /subscriptions/remove` 只删目标记录；
  - `url`/`group_id`/`limit` 非法输入返回 4xx；
  - 错误信息脱敏。
- 复用 `tests/support.py` 的 FakeBot/FakeAdapter/FakeScraper；用 `unittest.mock.patch` 替换 `astrbot.api.web.request`。
- 前端：`node --check` 检查每个 JS 文件语法；`tests/test_view_assets.py` 校验 `pages/status/index.html` 存在且引用 `./app.js`；手工验收明暗主题、窄屏、刷新、错误态。

### 手工验收

1. WebUI 插件详情页/侧边栏出现「getGameInfo」视图，标题/描述正确。
2. 概览显示下次检查倒计时，与日志中「下次新游戏检查将在…」一致。
3. 订阅表能正确标出 A 可推送、B 被配置文件禁用。
4. `/check` dry-run 后群内无消息、`seen_games.json` 不变。
5. 断网/站点 5xx 时页面显示错误摘要，不崩溃、不泄露 URL。
6. 明暗主题切换、窄屏（≤480px）可用。

## 10. 待确认问题

1. **第一版范围**：只读 + dry-run（推荐），还是直接带「检查并推送 / 测试推送 / 取消订阅」？
2. **前端形态**：无构建原生页面（推荐，仓库轻）还是像群分析插件那样 Vue/React + Vite 构建产物？
3. **实时性**：10 秒轮询（推荐，简单）还是必须 SSE 实时事件？
4. **历史数据**：是否需要「最近 7 天新游戏 / 推送成功率 / 检查耗时」图表？需要则要增加持久化。
5. **操作权限**：测试推送、取消订阅是否只允许 Dashboard 管理员？目前插件 API 只能拿到 `request.username`，没有角色信息，需要确认是否接受「能打开 Dashboard 就能操作」。
6. **语言**：首版仅中文，还是中英双语？
7. **封面**：直接外链（已实测可用）还是必须走后端代理（更稳但增加复杂度）？

## 11. 建议的落地顺序

1. 先实现 `status.py` + `_run_check` 重构（不动 UI），保证定时任务行为不变。
2. 注册 `/status`、`/subscriptions`、`/events`，用 curl/bridge 调通。
3. 搭 `pages/status/` 静态页，先只做概览 + 订阅两张表。
4. 加游戏 Tab（列表/搜索/详情）。
5. 加 dry-run 检查与错误态，补测试。
6. 根据讨论结果决定 P2 操作与 SSE。

## 附录：最小接入骨架（讨论用，不是最终实现）

### 后端注册

```python
# webui.py
from astrbot.api.web import error_response, json_response, request

PLUGIN_NAME = "astrbot_plugin_getGameInfo"

def register_view_apis(plugin, context) -> None:
    async def api_status():
        return json_response(plugin.status.snapshot())

    async def api_check():
        payload = await request.json(default={})
        mode = payload.get("mode", "dry_run")
        if mode not in ("dry_run", "push"):
            return error_response("mode must be dry_run or push")
        return json_response(await plugin.run_check(mode=mode))

    context.register_web_api(
        f"/{PLUGIN_NAME}/status", api_status, ["GET"], "Get plugin status"
    )
    context.register_web_api(
        f"/{PLUGIN_NAME}/check", api_check, ["POST"], "Run a status check"
    )
```

```python
# main.py __init__
register_view_apis(self, context)
```

### 视图端

```html
<!-- pages/status/index.html -->
<main id="app"></main>
<script type="module" src="./app.js"></script>
```

```js
// pages/status/app.js
const bridge = window.AstrBotPluginView;
const context = await bridge.ready();

const status = await bridge.apiGet("status");
document.title = bridge.t("views.status.title", "getGameInfo");

// POST 示例
const result = await bridge.apiPost("check", { mode: "dry_run" });
```

### i18n

```json
{
  "views": {
    "status": {
      "title": "getGameInfo",
      "description": "查看轮询、订阅与推送状态"
    }
  }
}
```
