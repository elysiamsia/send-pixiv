"""`core/commands.py` 的单元测试。

前缀识别与路由现在归框架的 Command 组件管（见 `test_command_component.py`），
这里只测留在核心模块里的两样：分级收敛与用法文案。
"""

from __future__ import annotations

import unittest

from plugin_loader import load_package

load_package()

from send_pixiv_under_test.core.commands import (  # noqa: E402
    RATING_ORDER,
    USAGE,
    effective_rating,
    rating_rank,
)


class EffectiveRatingTest(unittest.TestCase):
    """`effective_rating` 的用例。"""

    def test_ask_within_cap(self) -> None:
        self.assertEqual(effective_rating("r18", "r18"), ("r18", ""))
        self.assertEqual(effective_rating("r18", "all"), ("r18", ""))

    def test_ask_above_cap_is_refused(self) -> None:
        "要求超过上限时明确拒绝，不静默降级——否则用户以为发的是 R-18。"
        mode, reason = effective_rating("r18", "safe")
        self.assertEqual(mode, "safe")
        self.assertIn("safe", reason)

    def test_all_above_r18_cap(self) -> None:
        mode, reason = effective_rating("all", "r18")
        self.assertEqual(mode, "r18")
        self.assertTrue(reason)

    def test_empty_request_uses_cap(self) -> None:
        "`#pixiv` 不要求分级，用会话默认。"
        self.assertEqual(effective_rating("", "safe"), ("safe", ""))
        self.assertEqual(effective_rating("", "all"), ("all", ""))

    def test_garbage_falls_back_to_safe(self) -> None:
        self.assertEqual(effective_rating("", "乱写"), ("safe", ""))
        self.assertEqual(rating_rank("乱写"), 0)
        self.assertEqual(rating_rank(""), 0)

    def test_rank_order(self) -> None:
        self.assertLess(rating_rank("safe"), rating_rank("r18"))
        self.assertLess(rating_rank("r18"), rating_rank("all"))
        self.assertEqual(len(RATING_ORDER), 3)


class UsageSafetyTest(unittest.TestCase):
    """回执文案不能自己触发自己。"""

    def test_usage_has_no_command_prefix(self) -> None:
        "机器人自己发的消息也会走一遍事件链，文案以命令前缀开头就会形成回环。"
        stripped = USAGE.lstrip()
        self.assertFalse(stripped.startswith("#"))
        self.assertFalse(stripped.startswith("/"))

    def test_usage_mentions_the_command(self) -> None:
        self.assertIn("#r18", USAGE)


if __name__ == "__main__":
    unittest.main()
