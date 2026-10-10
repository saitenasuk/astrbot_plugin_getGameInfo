"""OneBot error diagnostics and non-mutating poster-free fallback payloads."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

_SOURCE = re.compile(r"(?:https?://|file://|base64://)[^\s\"'<>]+", re.IGNORECASE)
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_SECRET = re.compile(
    r"\b(?:authorization|cookie|set-cookie|token|password|secret|api[_-]?key|"
    r"sig[_-]?session|session[_-]?key|u[_-]?key)\b[\"']?\s*[:=]\s*[^\r\n]*",
    re.IGNORECASE,
)
_QUOTED_PATH = re.compile(r"""(["'])(?:[A-Za-z]:[\\/]|/|\\\\)[^\r\n]*?\1""")
_PATH = re.compile(r"(?:\b[A-Za-z]:[\\/]|\\\\|(?<![\w/])/[\w.~-]+/)[^\s\"'<>]+")


def sanitize_detail(text: str) -> str:
    text = _SOURCE.sub("[media source hidden]", text)
    text = _BEARER.sub("Bearer [hidden]", text)
    text = _SECRET.sub("[credential hidden]", text)
    text = _QUOTED_PATH.sub("[path hidden]", text)
    text = _PATH.sub("[path hidden]", text)
    return " ".join(text.splitlines()[0].split())[:600] if text.splitlines() else ""


class OneBotActionError(Exception):
    def __init__(
        self,
        action: str,
        reason: str,
        *,
        retcode: int | None = None,
        detail: str = "",
        confirmed_failure: bool = False,
    ) -> None:
        self.action = action
        self.reason = reason
        self.retcode = retcode
        self.detail = sanitize_detail(detail)
        self.confirmed_failure = confirmed_failure
        super().__init__(self.diagnostic())

    def diagnostic(self) -> str:
        return (
            f"action={self.action}, reason={self.reason}, "
            f"retcode={self.retcode}, detail={self.detail}"
        )

    @property
    def media_upload_failed(self) -> bool:
        return self.confirmed_failure and self.reason in (
            "HIGHWAY_CONNECT_TIMEOUT",
            "HIGHWAY_UPLOAD_FAILED",
            "MEDIA_UPLOAD_FAILED",
        )

    def user_message(self) -> str:
        if self.reason == "HIGHWAY_CONNECT_TIMEOUT":
            return "QQ 图片上传连接超时，请检查 SnowLuma/NapCat 到 QQ Highway 的网络；可关闭游戏封面后重试"
        if self.reason in ("HIGHWAY_UPLOAD_FAILED", "MEDIA_UPLOAD_FAILED"):
            return "QQ 图片上传失败，请查看 OneBot 后端日志；可关闭游戏封面后重试"
        if self.reason == "ONEBOT_TIMEOUT":
            return "等待 OneBot 响应超时，发送结果尚不确定，请先确认群内是否已收到消息"
        if self.reason == "ONEBOT_UNAVAILABLE":
            return "无法连接 OneBot 后端，请检查 AstrBot 与 SnowLuma/NapCat 的连接"
        suffix = f"（retcode={self.retcode}）" if self.retcode is not None else ""
        return "游戏信息发送失败，请查看插件及 OneBot 后端日志" + suffix

    @classmethod
    def from_failure(
        cls,
        action: str,
        result: Any = None,
        exception: Exception | None = None,
    ) -> OneBotActionError:
        result = result if isinstance(result, dict) else {}
        parts = [
            result[key]
            for key in ("wording", "message", "msg")
            if isinstance(result.get(key), str) and result[key]
        ]
        if not parts and exception is not None:
            parts = [
                getattr(exception, key)
                for key in ("wording", "message", "msg")
                if isinstance(getattr(exception, key, None), str)
                and getattr(exception, key)
            ]
        if not parts and exception is not None:
            # ActionFailed's repr can include the complete response and base64.
            parts = [
                f"{type(exception).__name__} (no wording)" if result else str(exception)
            ]
        detail = "; ".join(parts)
        lower = detail.lower()
        name = type(exception).__name__ if exception else ""
        confirmed = result.get("status") == "failed" or name == "ActionFailed"
        if name in ("TimeoutError", "TimingError"):
            reason = "ONEBOT_TIMEOUT"
            confirmed = False
        elif name in ("ApiNotAvailable", "NetworkError", "HttpFailed"):
            reason = "ONEBOT_UNAVAILABLE"
        elif "highway" in lower:
            reason = (
                "HIGHWAY_CONNECT_TIMEOUT"
                if "tcp connect timeout" in lower
                else "HIGHWAY_UPLOAD_FAILED"
            )
        elif any(
            marker in lower
            for marker in (
                "rich media transfer failed",
                "image upload failed",
                "uploadimage",
            )
        ):
            reason = "MEDIA_UPLOAD_FAILED"
        else:
            reason = "ONEBOT_ACTION_FAILED"
        code = result.get("retcode", getattr(exception, "retcode", None))
        try:
            code = (
                int(code)
                if isinstance(code, (int, str)) and not isinstance(code, bool)
                else None
            )
        except ValueError:
            code = None
        return cls(
            action, reason, retcode=code, detail=detail, confirmed_failure=confirmed
        )


async def call_action(bot, action: str, **payload: Any) -> Any:
    try:
        result = await bot.call_action(action, **payload)
    except Exception as exc:
        result = getattr(exc, "result", None)
        if not isinstance(result, dict) and exc.args and isinstance(exc.args[0], dict):
            result = exc.args[0]
        raise OneBotActionError.from_failure(action, result, exc) from exc
    if isinstance(result, dict) and result.get("status") == "failed":
        raise OneBotActionError.from_failure(action, result)
    return result


def text_only_payload(action: str, payload: dict) -> dict | None:
    """Drop posters without mutating nodes reused for other subscriber groups.

    Only handles the two message shapes built by this plugin. Return None when
    there were no images or no text; such errors cannot be fixed by this retry.
    """
    fallback = deepcopy(payload)
    had_image = False

    def strip(content: Any) -> list[dict]:
        nonlocal had_image
        if not isinstance(content, list):
            return []
        had_image |= any(
            isinstance(s, dict) and s.get("type") == "image" for s in content
        )
        return [s for s in content if isinstance(s, dict) and s.get("type") == "text"]

    if action == "send_group_forward_msg":
        nodes = fallback.get("messages")
        if not isinstance(nodes, list):
            return None
        kept = []
        for node in nodes:
            if not isinstance(node, dict) or node.get("type") != "node":
                continue
            data = node.get("data")
            if not isinstance(data, dict):
                continue
            data["content"] = strip(data.get("content"))
            if data["content"]:
                kept.append(node)
        fallback["messages"] = kept
        has_text = bool(kept)
    elif action == "send_group_msg":
        # Exclude the original reply too, matching the previous info fallback.
        fallback["message"] = strip(fallback.get("message"))
        has_text = bool(fallback["message"])
    else:
        return None
    return fallback if had_image and has_text else None
