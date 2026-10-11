"""分级判定的单元测试。"""

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
        self.assertEqual(classify(Illust(id=1, x_restrict=0, sanity_level=2)), "safe")

    def test_r18_from_x_restrict(self) -> None:
        self.assertEqual(classify(Illust(id=1, x_restrict=1, sanity_level=2)), "r18")

    def test_r18_from_sanity_level(self) -> None:
        # x_restrict 还没同步时，sanity_level 应能兜住
        self.assertEqual(classify(Illust(id=1, x_restrict=0, sanity_level=4)), "r18")

    def test_r18g_wins(self) -> None:
        self.assertEqual(classify(Illust(id=1, x_restrict=2, sanity_level=4)), "r18g")
        self.assertEqual(classify(Illust(id=1, x_restrict=1, sanity_level=6)), "r18g")


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
            Illust(id=1, x_restrict=0, sanity_level=2),
            Illust(id=2, x_restrict=1, sanity_level=4),
            Illust(id=3, x_restrict=2, sanity_level=6),
        ]
        self.assertEqual([i.id for i in filter_by_mode(illusts, "safe")], [1])
        self.assertEqual([i.id for i in filter_by_mode(illusts, "r18")], [1, 2])
        self.assertEqual([i.id for i in filter_by_mode(illusts, "all")], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
