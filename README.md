# astrbot-plugin-helloworld

AstrBot 插件模板 / A template plugin for AstrBot plugin feature

> [!NOTE]
> This repo is just a template of [AstrBot](https://github.com/AstrBotDevs/AstrBot) Plugin.
> 
> [AstrBot](https://github.com/AstrBotDevs/AstrBot) is an agentic assistant for both personal and group conversations. It can be deployed across dozens of mainstream instant messaging platforms, including QQ, Telegram, Feishu, DingTalk, Slack, LINE, Discord, Matrix, etc. In addition, it provides a reliable and extensible conversational AI infrastructure for individuals, developers, and teams. Whether you need a personal AI companion, an intelligent customer support agent, an automation assistant, or an enterprise knowledge base, AstrBot enables you to quickly build AI applications directly within your existing messaging workflows.

# Supports

- [AstrBot Repo](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot Plugin Development Docs (Chinese)](https://docs.astrbot.app/dev/star/plugin-new.html)
- [AstrBot Plugin Development Docs (English)](https://docs.astrbot.app/en/dev/star/plugin-new.html)

## getGameInfo 使用及 SnowLuma 兼容修复（v0.0.2）

本插件的管理员命令：

- `/game list [页码]`：查询游戏列表，群聊使用合并转发，私聊使用文字列表。
- `/game info <游戏名>`：查询游戏信息；单结果保留详情页玩家人数，多结果使用合并转发。
- `/game subscribe` / `/game unsubscribe`：订阅或取消本群的新游戏推送。

保留已有 `request_timeout`、`game_check_interval`、`max_display_items` 配置，新增：

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `send_game_posters` | `true` | 是否下载和发送游戏封面；关闭时完全跳过图片，文字和详情链接保留 |
| `fallback_to_text_on_upload_failure` | `true` | 收到明确图片上传失败时，移除封面并尝试一次纯文字发送；响应超时、结果不确定时不重发 |

两项配置同时应用于游戏列表、单 / 多结果查询以及订阅推送。命令回复优先使用收到该命令的机器人实例，不再固定使用第一个 OneBot 账号。

SnowLuma 的 `highway ... cmdId=1004 ... TCP connect timeout` 属于下载后的 QQ 图片上传故障，不能通过改变网站请求超时或把 base64 改成图片 URL 来保证修复。此版本不会修复后端 TCP 网络，而是确保图片上传明确失败时仍有一次纯文字降级机会，并保留脱敏的 `action`、`reason`、`retcode` 和 `wording` 诊断。

图片仍由插件携带 Referer 下载后以 `base64://` 发送，不需要 AstrBot 与 OneBot 后端共享目录。每张封面仅 GET 一次，复用 HTTP 客户端；图片先校验且最大 5 MiB，下载或校验失败时跳过图片。私聊文字查询不下载图片。

## 多机器人账号（v0.0.3）

同一个 AstrBot 可以同时运行多个 OneBot 账号。订阅会记录收到 `/game subscribe` 的账号，新游戏推送由该账号发送，不再固定使用平台列表里的第一个账号；推送前还会确认该账号绑定的配置文件启用了本插件。旧版只保存群号的订阅会在下一次推送时自动迁移，若迁移后仍不是预期账号，用目标机器人重新发送 `/game subscribe` 即可。

安装 / 本地验证：

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

测试使用模拟 AstrBot / OneBot 及合成网页，不会发送 QQ 消息或访问游戏网站。真实环境复测见 [docs/TESTING.md](docs/TESTING.md)。
完整版本记录见 [CHANGELOG.md](CHANGELOG.md)。
