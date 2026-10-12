"""聊天指令解析的单元测试。

对应 `#r18 关键词` 这个功能：用户主动发指令直接搜图，
比"等 LLM 决定调用工具"更可靠。
"""

from __future__ import annotations

import unittest

from plugin_loader import load_package

load_package()

from send_pixiv_under_test.core.commands import (  # noqa: E402
    Command,
    effective_rating,
    parse_command,
    rating_rank,
)


class ParseCommandTest(unittest.TestCase):
    """`parse_command` 的用例。"""

    def test_r18_basic(self) -> None:
        cmd = parse_command("#r18 猫")
        self.assertIsNotNone(cmd)
        assert cmd is not None
        self.assertEqual(cmd.kind, "r18")
        self.assertEqual(cmd.keyword, "猫")
        self.assertEqual(cmd.requested_rating, "r18")

    def test_r18_case_insensitive(self) -> None:
        for text in ("#R18 猫", "#r18 猫", "#R18 猫"):
            cmd = parse_command(text)
            self.assertIsNotNone(cmd, text)
            assert cmd is not None
            self.assertEqual(cmd.kind, "r18")

    def test_normal_prefix(self) -> None:
        cmd = parse_command("#pixiv 风景")
        self.assertIsNotNone(cmd)
        assert cmd is not None
        self.assertEqual(cmd.kind, "normal")
        self.assertEqual(cmd.keyword, "风景")
        self.assertEqual(cmd.requested_rating, "", "普通指令不该要求分级")

    def test_keyword_may_contain_spaces(self) -> None:
        cmd = parse_command("#r18 猫 少女")
        self.assertIsNotNone(cmd)
        assert cmd is not None
        self.assertEqual(cmd.keyword, "猫 少女")

    def test_prefix_only_gives_empty_keyword(self) -> None:
        "只给前缀时返回空关键词，由调用方回用法提示。"
        cmd = parse_command("#r18")
        self.assertIsNotNone(cmd)
        assert cmd is not None
        self.assertEqual(cmd.keyword, "")

    def test_leading_whitespace_ok(self) -> None:
        cmd = parse_command("   #r18 猫")
        self.assertIsNotNone(cmd)

    def test_prefix_must_be_at_start(self) -> None:
        "句中的 #r18 不算指令。"
        self.assertIsNone(parse_command("帮我找 #r18 猫"))
        self.assertIsNone(parse_command("看看这个#r18 猫"))

    def test_longer_prefix_not_eaten(self) -> None:
        "#r18g 不能被 #r18 吃掉（后面紧跟字母，不是空白）。"
        self.assertIsNone(parse_command("#r18g 猫"))

    def test_no_prefix(self) -> None:
        self.assertIsNone(parse_command(""))
        self.assertIsNone(parse_command("普通消息"))
        self.assertIsNone(parse_command("#其他 猫"))

    def test_custom_prefixes(self) -> None:
        cmd = parse_command("搞颜色 猫", r18_prefixes=["搞颜色"], normal_prefixes=[])
        self.assertIsNotNone(cmd)
        assert cmd is not None
        self.assertEqual(cmd.kind, "r18")
        self.assertEqual(cmd.keyword, "猫")

    def test_longer_prefix_wins(self) -> None:
        "同时配了 #r18 与 #r18g 时，长的优先。"
        cmd = parse_command("#r18g 猫", r18_prefixes=["#r18", "#r18g"])
        self.assertIsNotNone(cmd)
        assert cmd is not None
        self.assertEqual(cmd.keyword, "猫")

    def test_empty_prefixes_ignored(self) -> None:
        self.assertIsNone(parse_command("#r18 猫", r18_prefixes=["", "  "], normal_prefixes=[]))


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
        self.assertEqual(effective_rating("", "safe"), ("safe", ""))
        self.assertEqual(effective_rating("", "all"), ("all", ""))

    def test_garbage_falls_back_to_safe(self) -> None:
        self.assertEqual(effective_rating("", "乱写"), ("safe", ""))
        self.assertEqual(rating_rank("乱写"), 0)
        self.assertEqual(rating_rank(""), 0)


class NoSelfTriggerTest(unittest.TestCase):
    """防回环：机器人自己发的回执不能被当成指令。"""

    def test_usage_hint_is_not_a_command(self) -> None:
        from send_pixiv_under_test.core.commands import USAGE  # noqa: PLC0415

        self.assertIsNone(
            parse_command(USAGE),
            "用法提示以 #r18 开头的话，机器人会自己触发自己",
        )

    def test_other_replies_are_not_commands(self) -> None:
        for text in (
            "当前会话最高只允许 safe 级别。要发 R-18 请把本会话加进 plugin.r18_streams",
            "没搜到「猫」的图，换个词试试？",
            "手速太快啦，12 秒后再试",
            "🎨 标题\n👤 作者\n🔗 https://www.pixiv.net/artworks/1",
        ):
            self.assertIsNone(parse_command(text), text)


if __name__ == "__main__":
    unittest.main()
