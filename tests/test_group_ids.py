"""白名单写群号 / QQ 号的单元测试。

框架的 stream_id 是 SHA-256 哈希，人眼没法认。实测确认它的算法是确定的：

    群聊: sha256(f"{platform}_{group_id}")
    私聊: sha256(f"{platform}_{user_id}_private")

所以配置里可以直接写群号，插件在判断时换算成哈希再比对。
"""

from __future__ import annotations

import hashlib
import unittest

from plugin_loader import load_package

load_package()

from send_pixiv_under_test.core.policy import (  # noqa: E402
    Policy,
    looks_like_stream_id,
    stream_id_candidates,
)

QQ = "qq"
GROUP = "123456789"
FRIEND = "987654321"

GROUP_STREAM = hashlib.sha256(f"{QQ}_{GROUP}".encode()).hexdigest()
PRIVATE_STREAM = hashlib.sha256(f"{QQ}_{FRIEND}_private".encode()).hexdigest()
OTHER_STREAM = hashlib.sha256(b"qq_555555555").hexdigest()


class StreamIdCandidatesTest(unittest.TestCase):
    """换算函数的用例。"""

    def test_group_hash_matches_framework(self) -> None:
        group_id, _ = stream_id_candidates(QQ, GROUP)
        self.assertEqual(group_id, GROUP_STREAM)

    def test_private_hash_matches_framework(self) -> None:
        _, private_id = stream_id_candidates(QQ, FRIEND)
        self.assertEqual(private_id, PRIVATE_STREAM)

    def test_different_platforms_differ(self) -> None:
        self.assertNotEqual(stream_id_candidates(QQ, GROUP)[0], stream_id_candidates("telegram", GROUP)[0])


class LooksLikeStreamIdTest(unittest.TestCase):
    """区分"哈希"与"群号"。"""

    def test_hash(self) -> None:
        self.assertTrue(looks_like_stream_id(GROUP_STREAM))

    def test_group_number(self) -> None:
        self.assertFalse(looks_like_stream_id(GROUP))

    def test_short_or_non_hex(self) -> None:
        self.assertFalse(looks_like_stream_id(""))
        self.assertFalse(looks_like_stream_id("z" * 64))
        self.assertFalse(looks_like_stream_id(GROUP_STREAM[:-1]))


class PolicyWithGroupNumberTest(unittest.TestCase):
    """名单里写群号的行为。"""

    def test_whitelist_by_group_number(self) -> None:
        policy = Policy(allowed_streams=[GROUP])
        self.assertTrue(policy.check(GROUP_STREAM, QQ).allowed)
        self.assertFalse(policy.check(OTHER_STREAM, QQ).allowed)

    def test_whitelist_by_qq_number_private_chat(self) -> None:
        policy = Policy(allowed_streams=[FRIEND])
        self.assertTrue(policy.check(PRIVATE_STREAM, QQ).allowed)

    def test_blacklist_by_group_number(self) -> None:
        policy = Policy(blocked_streams=[GROUP])
        self.assertFalse(policy.check(GROUP_STREAM, QQ).allowed)
        self.assertTrue(policy.check(OTHER_STREAM, QQ).allowed)

    def test_blacklist_beats_whitelist_by_group_number(self) -> None:
        policy = Policy(allowed_streams=[GROUP], blocked_streams=[GROUP])
        self.assertFalse(policy.check(GROUP_STREAM, QQ).allowed)

    def test_r18_by_group_number(self) -> None:
        policy = Policy(default_rating="safe", r18_streams=[GROUP])
        self.assertEqual(policy.rating_mode_for(GROUP_STREAM, QQ), "r18")
        self.assertEqual(policy.rating_mode_for(OTHER_STREAM, QQ), "safe")

    def test_r18g_by_group_number(self) -> None:
        policy = Policy(r18_streams=[GROUP], r18g_streams=[FRIEND])
        self.assertEqual(policy.rating_mode_for(GROUP_STREAM, QQ), "r18")
        self.assertEqual(policy.rating_mode_for(PRIVATE_STREAM, QQ), "all")

    def test_raw_hash_still_works(self) -> None:
        "老写法（直接写哈希）不能被破坏。"
        policy = Policy(allowed_streams=[GROUP_STREAM])
        self.assertTrue(policy.check(GROUP_STREAM, QQ).allowed)
        self.assertTrue(policy.check(GROUP_STREAM).allowed, "不给 platform 也要能用哈希匹配")

    def test_group_number_needs_platform(self) -> None:
        "没有平台信息时，群号无从换算——这时只认哈希。"
        policy = Policy(allowed_streams=[GROUP])
        self.assertFalse(policy.check(GROUP_STREAM).allowed)
        self.assertFalse(policy.check(GROUP_STREAM, "").allowed)

    def test_wrong_platform_does_not_match(self) -> None:
        policy = Policy(allowed_streams=[GROUP])
        self.assertFalse(policy.check(GROUP_STREAM, "telegram").allowed)

    def test_hash_entry_is_not_treated_as_group_number(self) -> None:
        "名单里放哈希时，不该再拿它去当群号算一遍。"
        policy = Policy(allowed_streams=[GROUP_STREAM])
        self.assertFalse(policy.check(OTHER_STREAM, QQ).allowed)

    def test_mixed_entries(self) -> None:
        policy = Policy(allowed_streams=[GROUP, OTHER_STREAM])
        self.assertTrue(policy.check(GROUP_STREAM, QQ).allowed)
        self.assertTrue(policy.check(OTHER_STREAM, QQ).allowed)

    def test_blank_entries_ignored(self) -> None:
        policy = Policy(allowed_streams=["", "   "])
        self.assertFalse(policy.check(GROUP_STREAM, QQ).allowed)


if __name__ == "__main__":
    unittest.main()
