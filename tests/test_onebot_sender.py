import unittest
from copy import deepcopy
from unittest.mock import AsyncMock

from onebot_sender import (
    OneBotActionError,
    call_action,
    sanitize_detail,
    text_only_payload,
)
from tests.support import HIGHWAY_ERROR, ActionFailed, TimingError


class SenderTests(unittest.IsolatedAsyncioTestCase):
    async def test_aiocqhttp_action_failed_result(self):
        bot = type("Bot", (), {})()
        bot.call_action = AsyncMock(side_effect=ActionFailed(HIGHWAY_ERROR))
        with self.assertRaises(OneBotActionError) as raised:
            await call_action(bot, "send_group_forward_msg", group_id=123, messages=[])
        error = raised.exception
        self.assertEqual(error.reason, "HIGHWAY_CONNECT_TIMEOUT")
        self.assertEqual(error.retcode, 100)
        self.assertTrue(error.media_upload_failed)
        self.assertIn("cmdId=1004 chunk=31175/31175 offset=0", error.detail)
        self.assertIn("QQ 图片上传连接超时", error.user_message())

    async def test_failed_return_envelope(self):
        bot = type("Bot", (), {})()
        bot.call_action = AsyncMock(return_value=ActionFailed(HIGHWAY_ERROR).result)
        with self.assertRaises(OneBotActionError) as raised:
            await call_action(bot, "send_group_msg", message=[])
        self.assertTrue(raised.exception.media_upload_failed)

    async def test_success_return_unchanged(self):
        result = {"message_id": 123}
        bot = type("Bot", (), {})()
        bot.call_action = AsyncMock(return_value=result)
        self.assertIs(await call_action(bot, "send_group_msg"), result)

    async def test_timeout_does_not_confirm_failure(self):
        bot = type("Bot", (), {})()
        bot.call_action = AsyncMock(side_effect=TimingError("timeout"))
        with self.assertRaises(OneBotActionError) as raised:
            await call_action(bot, "send_group_forward_msg")
        self.assertEqual(raised.exception.reason, "ONEBOT_TIMEOUT")
        self.assertFalse(raised.exception.media_upload_failed)
        self.assertIn("尚不确定", raised.exception.user_message())

    async def test_cancel_propagates(self):
        import asyncio

        bot = type("Bot", (), {})()
        bot.call_action = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await call_action(bot, "send_group_msg")

    def test_napcat_media_failure_is_supported(self):
        exc = ActionFailed("rich media transfer failed")
        error = OneBotActionError.from_failure("send_group_msg", exc.result, exc)
        self.assertEqual(error.reason, "MEDIA_UPLOAD_FAILED")
        self.assertTrue(error.media_upload_failed)

    def test_unknown_action_failure_is_not_media_retry(self):
        exc = ActionFailed("invalid group id", 1400)
        error = OneBotActionError.from_failure("send_group_msg", exc.result, exc)
        self.assertFalse(error.media_upload_failed)
        self.assertIn("retcode=1400", error.user_message())
        self.assertNotIn("invalid group", error.user_message())

    def test_unconfirmed_highway_message_is_not_retried(self):
        error = OneBotActionError.from_failure(
            "send_group_msg", exception=RuntimeError(HIGHWAY_ERROR)
        )
        self.assertEqual(error.reason, "HIGHWAY_CONNECT_TIMEOUT")
        self.assertFalse(error.media_upload_failed)

    def test_missing_wording_does_not_dump_response(self):
        exc = ActionFailed("")
        exc.result["data"] = {
            "secret": "private-value",
            "image": "base64://imagecontent",
        }
        error = OneBotActionError.from_failure("send_group_msg", exc.result, exc)
        self.assertNotIn("private-value", error.detail)
        self.assertNotIn("imagecontent", error.detail)

    def test_diagnostics_hide_sensitive_fields_and_media(self):
        for source, secret in (
            ("error https://cdn.example/image.jpg?sig=private-value", "private-value"),
            ("error base64://abcdefghijklmnop", "abcdefghijklmnop"),
            ("error Authorization: Bearer private-token", "private-token"),
            ("error Cookie=private-cookie", "private-cookie"),
            ("error file:///data/private-image.jpg", "private-image"),
            ("error '/data/private user/image.jpg'", "private user"),
            (r'error "C:\Users\private user\image.jpg"', "private user"),
        ):
            with self.subTest(source=source):
                self.assertNotIn(secret, sanitize_detail(source))
        self.assertEqual(sanitize_detail("first\nsecond"), "first")
        self.assertLessEqual(len(sanitize_detail("x" * 1000)), 600)


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "group_id": 123,
            "messages": [
                {
                    "type": "node",
                    "data": {
                        "user_id": "789",
                        "nickname": "Online-Fix.me",
                        "content": [
                            {"type": "image", "data": {"file": "base64://image"}},
                            {
                                "type": "text",
                                "data": {
                                    "text": "Game title\n详情页：https://example.com/game"
                                },
                            },
                        ],
                    },
                },
            ],
        }

    def test_strip_images_preserves_cards_without_mutating_source(self):
        original = deepcopy(self.payload)
        result = text_only_payload("send_group_forward_msg", self.payload)
        self.assertEqual(self.payload, original)
        self.assertEqual(result["group_id"], 123)
        self.assertEqual(result["messages"][0]["data"]["user_id"], "789")
        content = result["messages"][0]["data"]["content"]
        self.assertEqual(len(content), 1)
        self.assertEqual(content[0]["type"], "text")
        self.assertIn("详情页", content[0]["data"]["text"])

    def test_single_card_fallback_removes_reply_and_image(self):
        result = text_only_payload(
            "send_group_msg",
            {
                "group_id": 123,
                "message": [
                    {"type": "reply", "data": {"id": "original"}},
                    {"type": "image", "data": {"file": "base64://image"}},
                    {"type": "text", "data": {"text": "Game details"}},
                ],
            },
        )
        self.assertEqual([s["type"] for s in result["message"]], ["text"])

    def test_no_retry_if_message_was_already_text_only(self):
        self.payload["messages"][0]["data"]["content"] = [
            {"type": "text", "data": {"text": "title"}},
        ]
        self.assertIsNone(text_only_payload("send_group_forward_msg", self.payload))

    def test_no_empty_forward_if_source_only_had_images(self):
        self.payload["messages"][0]["data"]["content"].pop()
        self.assertIsNone(text_only_payload("send_group_forward_msg", self.payload))

    def test_unhandled_action_and_malformed_payload(self):
        self.assertIsNone(text_only_payload("upload_group_file", self.payload))
        self.assertIsNone(
            text_only_payload("send_group_forward_msg", {"messages": None})
        )
