"""分级判定的单元测试。

规则只用 `x_restrict` + R-18/R-18G 标签，**不看 `sanity_level`**。
这里的用例锁死这一点：`sanity_level` 曾经把 60 条样本里的 33 条判错。
"""

from __future__ import annotations

import unittest

from plugin_loader import load_package

load_package()

from send_pixiv_under_test.core.models import Illust  # noqa: E402
from send_pixiv_under_test.core.rating import (  # noqa: E402
    classify,
    filter_by_mode,
    is_allowed,
    normalize_mode,
)


class ClassifyTest(unittest.TestCase):
    """`classify` 的用例。"""

    def test_safe(self) -> None:
        self.assertEqual(classify(Illust(id=1, x_restrict=0)), "safe")

    def test_r18_from_x_restrict(self) -> None:
        self.assertEqual(classify(Illust(id=1, x_restrict=1)), "r18")

    def test_r18g_from_x_restrict(self) -> None:
        self.assertEqual(classify(Illust(id=1, x_restrict=2)), "r18g")

    def test_tags_are_a_safety_net(self) -> None:
        "x_restrict 缺失/没同步时，标签要能兜住。"
        self.assertEqual(classify(Illust(id=1, x_restrict=0, tags=["R-18"])), "r18")
        self.assertEqual(classify(Illust(id=1, x_restrict=0, tags=["R-18G"])), "r18g")
        self.assertEqual(classify(Illust(id=1, x_restrict=1, tags=["R-18G"])), "r18g")

    def test_tags_case_insensitive(self) -> None:
        self.assertEqual(classify(Illust(id=1, tags=["r-18"])), "r18")

    def test_sanity_level_must_not_affect_rating(self) -> None:
        "实测结论：App API 的 sanity_level 是噪音，绝不能拿它判分级。"
        for level in (0, 2, 4, 6):
            with self.subTest(sanity_level=level):
                self.assertEqual(
                    classify(Illust(id=1, x_restrict=0, sanity_level=level, tags=["風景"])),
                    "safe",
                )
                self.assertEqual(
                    classify(Illust(id=1, x_restrict=1, sanity_level=level, tags=["R-18"])),
                    "r18",
                )

    def test_real_world_sample(self) -> None:
        "来自实测的原始样本：x_restrict=1 / sanity_level=6 的作品其实是 R-18 而非 R-18G。"
        illust = Illust(id=150748675, x_restrict=1, sanity_level=6, tags=["R-18", "furry"])
        self.assertEqual(classify(illust), "r18")


class IsAllowedTest(unittest.TestCase):
    """模式 × 分级的放行矩阵。"""

    def test_matrix(self) -> None:
        expected = {
            ("safe", "safe"): True,
            ("safe", "r18"): False,
            ("safe", "r18g"): False,
            ("r18", "safe"): True,
            ("r18", "r18"): True,
            ("r18", "r18g"): False,
            ("all", "safe"): True,
            ("all", "r18"): True,
            ("all", "r18g"): True,
        }
        for (mode, rating), want in expected.items():
            with self.subTest(mode=mode, rating=rating):
                self.assertEqual(is_allowed(mode, rating), want)

    def test_unknown_mode_fails_safe(self) -> None:
        self.assertFalse(is_allowed("随便写的", "r18"))
        self.assertTrue(is_allowed("随便写的", "safe"))


class NormalizeModeTest(unittest.TestCase):
    """配置值的容错。"""

    def test_aliases(self) -> None:
        self.assertEqual(normalize_mode(" R-18 "), "r18")
        self.assertEqual(normalize_mode("NSFW"), "r18")
        self.assertEqual(normalize_mode("all"), "all")
        self.assertEqual(normalize_mode(""), "safe")
        self.assertEqual(normalize_mode("nonsense"), "safe")


class FilterByModeTest(unittest.TestCase):
    """列表过滤。"""

    def test_filter(self) -> None:
        illusts = [
            Illust(id=1, x_restrict=0),
            Illust(id=2, x_restrict=1),
            Illust(id=3, x_restrict=2),
        ]
        self.assertEqual([i.id for i in filter_by_mode(illusts, "safe")], [1])
        self.assertEqual([i.id for i in filter_by_mode(illusts, "r18")], [1, 2])
        self.assertEqual([i.id for i in filter_by_mode(illusts, "all")], [1, 2, 3])

    def test_filter_catches_tag_only_r18(self) -> None:
        "服务端没标 x_restrict、只靠标签标记的作品也要被挡住。"
        illusts = [Illust(id=1, x_restrict=0), Illust(id=2, x_restrict=0, tags=["R-18"])]
        self.assertEqual([i.id for i in filter_by_mode(illusts, "safe")], [1])


if __name__ == "__main__":
    unittest.main()
