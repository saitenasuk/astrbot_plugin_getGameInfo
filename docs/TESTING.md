# getGameInfo / SnowLuma 修复验收

## 错误边界与修复内容

用户提供的日志已经显示网站 JPEG 下载成功，随后 `send_group_forward_msg` 在 QQ 的图片上传阶段出现：

```text
cmdId=1004 chunk=31175/31175 offset=0
TCP connect timeout
```

31,175 字节的图片在首个分块建连失败；不能简单归因于图片大小、网站防盗链或 base64 格式。已检查 SnowLuma v1.14.22 源码提交 `87527cb7641a5a42f8f0efb73cb066102e004dee`，其支持 `send_group_forward_msg` 和 base64 图片；原生 TCP Highway 连接失败仍属于后端到 QQ 的网络路径。

插件侧改造：

1. 列表、搜索和订阅推送共用有限降级逻辑。收到明确的媒体上传失败响应时，最多尝试一次去掉图片的发送，保留全部游戏卡片及详情链接。
2. 单游戏详情也只在明确媒体失败时降级。OneBot 响应超时不能证明发送失败，不自动重复消息。
3. 纯文字降级不修改原始消息节点；同一批订阅推送中，一个群上传失败不会删掉其他群的封面。
4. `ActionFailed.result.wording`、`retcode` 被提取到脱敏诊断；URL、图片 base64、鉴权字段和本地路径不会原样出现在错误诊断中。
5. 可关闭 `send_game_posters`，直接跳过封面下载和上传，避免每次都等待失败后降级。
6. 命令回复使用该事件的 bot；订阅转发需要时用 `get_login_info` 获取账号，不生成空 user_id。
7. 保留现有玩家人数获取、订阅存储和站点爬虫逻辑；补齐直接使用的 httpx、Pillow 依赖。

这不是对 SnowLuma 的网络修复，也不代表 QQ 图片上传一定恢复。网站下载、OneBot 连接、QQ 媒体上传是不同阶段；图片上传失败时，纯文字转发也需要后端的转发服务正常工作。

## 自动化测试

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m compileall -q main.py onebot_sender.py models.py scraper.py tests
```

测试不需要真实 AstrBot、QQ 或站点身份，覆盖 21 张图片列表的失败场景、一次纯文字降级、超时不重复发送、封面开关、搜索、订阅推送、事件 bot 路由、图片 GET / 校验 / 大小限制，以及已有的玩家人数解析。

## 真实环境复测（使用者执行）

1. 更新所有修改的插件文件，确保新增 `onebot_sender.py` 在插件目录；重新安装依赖 / 重载插件。
2. 在配置中确认新增的“是否发送游戏封面”“封面上传失败时自动降级为纯文字”可见。
3. 保持两个开关开启，执行 `/game list`。正常时应发带图转发；如果重现原日志，应只尝试一次纯文字转发，21 条游戏信息和详情链接仍然保留。
4. 关闭 `send_game_posters` 再执行 `/game list`，应直接发纯文字转发，日志不再出现该命令的图片下载或图片上传。
5. 分别测试 `/game info <单结果游戏名>`、多结果搜索及私聊查询；玩家人数功能保留，私聊不下载封面。
6. 模拟 OneBot 响应超时时，先检查群里是否已经收到消息；插件不应自动重发相册。
7. 已有订阅群无需重新订阅；首次建立游戏记录仍不会把当前全部游戏当作新上架游戏发送。多账号下，主动命令使用接收账号；后台订阅仍采用原有的群号存储和平台选择方式。

## 若图片仍不能发送

插件日志应包含类似：

```text
action=send_group_forward_msg, reason=HIGHWAY_CONNECT_TIMEOUT, retcode=100,
detail=highway upload transport failed ... TCP connect timeout
```

如需恢复封面上传，仍需检查 SnowLuma 所在机器 / 容器到本次实际 Highway host / port 的出站 TCP 连接。可临时开启 SnowLuma 的 trace，提取 `highway_put_start` 的 host、port；不要公开整份 trace、headHex、Cookie、会话密钥或 OneBot access token。

无需为了插件升级专门收集这些信息；只有继续排查底层上传连接失败时才需要。调大插件 `request_timeout` 只影响网站 HTTP 请求，不改变 SnowLuma 的 Highway 建连逻辑。

## WebUI 运行状态视图（v0.0.4）

1. 在 AstrBot 插件详情页打开「运行状态」视图，确认标题与描述来自 `.astrbot-plugin/i18n/zh-CN.json`。
2. 概览应显示下次检查时间、上次检查结果、订阅数、追踪游戏数、账号列表和最近事件；明暗主题切换、窄屏（≤480px）可用。
3. 订阅页应能区分「可推送」「插件未启用」「平台未加载」「旧订阅待迁移」，并显示最近一次投递结果。
4. 点击「仅检查」：群内不应收到消息，`data/plugin_data/astrbot_plugin_getGameInfo/seen_games.json` 内容不应变化；再次点击应提示操作过于频繁。
5. 游戏页首次打开会请求站点；搜索、详情人数和「新」标记正常，封面加载失败时显示占位块。
6. 事件页应出现检查、订阅、推送失败/降级记录；错误文本不包含 URL、base64 或本地路径。

## P2 操作验收（v0.0.5）

1. 概览/顶部点击「检查并推送」应弹出二次确认，文案包含当前订阅群数；确认后群内应收到新游戏消息，结果弹窗显示成功/失败/跳过/降级数量。
2. 没有新游戏时执行「检查并推送」，群内不应收到消息，结果弹窗显示新增 0。
3. 订阅页对可推送的订阅点击「测试推送」，确认后只收到一条文字测试消息；被插件权限阻止的订阅按钮应禁用，直接调 API 应返回 409。
4. 点击「取消订阅」，确认后该群从列表移除；之后 `/game subscribe` 可重新添加。
5. 连续点击「检查并推送」或在检查进行中点击，应提示已有检查正在进行或操作过于频繁。
6. 重启插件后打开事件页，上一轮的检查/推送/失败事件仍应存在（来自 `status.json`）。
7. 手机窄屏下订阅操作按钮换行显示，不遮挡表格内容。
