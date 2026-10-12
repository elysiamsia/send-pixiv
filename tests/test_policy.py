"""策略与去重的单元测试。"""

from __future__ import annotations

import unittest

from plugin_loader import load_package

load_package()

from send_pixiv_under_test.core.policy import Debouncer, Policy  # noqa: E402


class PolicyTest(unittest.TestCase):
    """`Policy` 的用例。"""

    def test_disabled(self) -> None:
        policy = Policy(enabled=False)
        self.assertFalse(policy.check("s1").allowed)

    def test_whitelist(self) -> None:
        policy = Policy(allowed_streams=["s1"])
        self.assertTrue(policy.check("s1").allowed)
        self.assertFalse(policy.check("s2").allowed)

    def test_blacklist_beats_whitelist(self) -> None:
        policy = Policy(allowed_streams=["s1", "s2"], blocked_streams=["s1"])
        self.assertFalse(policy.check("s1").allowed)
        self.assertTrue(policy.check("s2").allowed)

    def test_cooldown(self) -> None:
        policy = Policy(cooldown_seconds=60)
        self.assertTrue(policy.check("s1").allowed)
        policy.commit("s1")
        verdict = policy.check("s1")
        self.assertFalse(verdict.allowed)
        self.assertIn("秒后再试", verdict.reason)
        self.assertGreater(policy.cooldown_left("s1"), 0)

    def test_daily_quota(self) -> None:
        policy = Policy(daily_quota=2, cooldown_seconds=0)
        self.assertTrue(policy.check("s1").allowed)
        policy.commit("s1")
        policy.commit("s1")
        self.assertFalse(policy.check("s1").allowed)
        # 配额是按会话算的，别的会话不受影响
        self.assertTrue(policy.check("s2").allowed)

    def test_rating_mode_by_stream(self) -> None:
        policy = Policy(
            default_rating="safe",
            r18_streams=["r18-chat"],
            r18g_streams=["r18g-chat"],
        )
        self.assertEqual(policy.rating_mode_for("normal"), "safe")
        self.assertEqual(policy.rating_mode_for("r18-chat"), "r18")
        self.assertEqual(policy.rating_mode_for("r18g-chat"), "all")

    def test_bad_default_rating_fails_safe(self) -> None:
        self.assertEqual(Policy(default_rating="whatever").rating_mode_for("s"), "safe")

    def test_reset(self) -> None:
        policy = Policy(cooldown_seconds=60)
        policy.commit("s1")
        policy.reset()
        self.assertTrue(policy.check("s1").allowed)


class DebouncerTest(unittest.TestCase):
    """`Debouncer` 的用例。"""

    def test_first_call_is_not_a_hit(self) -> None:
        debouncer = Debouncer(ttl_seconds=60)
        self.assertFalse(debouncer.hit("s1", "illust:1"))
        self.assertTrue(debouncer.hit("s1", "illust:1"))

    def test_namespace_isolation(self) -> None:
        debouncer = Debouncer(ttl_seconds=60)
        debouncer.hit("s1", "illust:1")
        self.assertFalse(debouncer.hit("s2", "illust:1"))

    def test_ttl_override_zero_disables(self) -> None:
        debouncer = Debouncer(ttl_seconds=60)
        debouncer.hit("s1", "illust:1")
        self.assertFalse(debouncer.hit("s1", "illust:1", ttl=0))

    def test_forget(self) -> None:
        debouncer = Debouncer(ttl_seconds=60)
        debouncer.hit("s1", "illust:1")
        debouncer.forget("s1", "illust:1")
        self.assertFalse(debouncer.hit("s1", "illust:1"))

    def test_first_sight_is_the_inverse(self) -> None:
        """`first_sight` 就是 `not hit`，但读起来不会搞反。

        实测踩过：指令 handler 里写成 `if not hit(...): return`，
        等于「不是重复才返回」，把每条新指令都丢掉了。
        """
        debouncer = Debouncer(ttl_seconds=60)
        self.assertTrue(debouncer.first_sight("s1", "cmd:r18:猫"))
        self.assertFalse(debouncer.first_sight("s1", "cmd:r18:猫"))

    def test_first_sight_records_like_hit(self) -> None:
        "走 first_sight 也要留下记录，否则 forget 无从下手。"
        debouncer = Debouncer(ttl_seconds=60)
        debouncer.first_sight("s1", "cmd:r18:猫")
        debouncer.forget("s1", "cmd:r18:猫")
        self.assertTrue(debouncer.first_sight("s1", "cmd:r18:猫"))


if __name__ == "__main__":
    unittest.main()
