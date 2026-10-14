"""错误话术层的单元测试。

这一层的存在意义就是"用户看得懂、知道下一步做什么"，所以测试也照着这个来：
每类异常都要给出**可操作**的话，而不是复读技术原文。
"""

from __future__ import annotations

import unittest

from plugin_loader import load_package

load_package()

from send_pixiv_under_test.core.errors import (  # noqa: E402
    REAUTH_HINT,
    PixivAuthError,
    PixivError,
    PixivNetworkError,
    PixivNotFoundError,
    PixivRateLimitError,
    friendly,
    network_hint,
)


class NetworkHintTest(unittest.TestCase):
    """`network_hint` 的用例。"""

    def test_without_proxy_points_to_config(self) -> None:
        hint = network_hint("")
        self.assertIn("pixiv.proxy", hint)
        self.assertIn("127.0.0.1:7890", hint, "要给出可直接照抄的例子")

    def test_with_proxy_asks_to_check_proxy(self) -> None:
        "配了代理还说'请配置代理'是最误导的——这里必须换方向。"
        hint = network_hint("http://127.0.0.1:7890")
        self.assertIn("http://127.0.0.1:7890", hint)
        self.assertNotIn("没有配置代理", hint)
        self.assertIn("代理软件", hint)


class FriendlyTest(unittest.TestCase):
    """`friendly` 的用例。"""

    def test_network_error_mentions_next_action(self) -> None:
        text = friendly(PixivNetworkError("请求 pixiv 失败: [WinError 10061] 拒绝连接"), proxy="")
        self.assertIn("连不上 pixiv", text)
        self.assertNotIn("WinError", text, "技术原文不该出现在给用户的话里")
        self.assertIn("pixiv.proxy", text)

    def test_network_error_with_proxy(self) -> None:
        text = friendly(PixivNetworkError("boom"), proxy="http://127.0.0.1:7890")
        self.assertIn("http://127.0.0.1:7890", text)

    def test_auth_error_has_no_scripts_path(self) -> None:
        "市场装 .mfp 的用户没有 scripts 目录，指引不能指向它（实测反馈过）。"
        text = friendly(PixivAuthError("refresh_token 无效"))
        self.assertIn("重新授权", text)
        self.assertNotIn("scripts/", text)
        self.assertNotIn("scripts/", REAUTH_HINT)

    def test_rate_limit(self) -> None:
        text = friendly(PixivRateLimitError("HTTP 429"))
        self.assertIn("频繁", text)

    def test_not_found(self) -> None:
        text = friendly(PixivNotFoundError("HTTP 404"))
        self.assertIn("不存在", text)

    def test_unknown_error_keeps_original_text(self) -> None:
        """「图片体积 8.3MB 超过上限」这类本身就是人话，不能被吃掉。"""
        text = friendly(ValueError("图片体积 8.3MB 超过上限 8.0MB"))
        self.assertIn("8.3MB", text)
        self.assertIn("上限", text)

    def test_long_text_is_truncated(self) -> None:
        text = friendly(PixivError("x" * 500))
        self.assertLessEqual(len(text), 160)

    def test_empty_message_falls_back_to_type_name(self) -> None:
        self.assertEqual(friendly(PixivError("")), "PixivError")


if __name__ == "__main__":
    unittest.main()
