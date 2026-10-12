"""「换图」逻辑的单元测试。

背景：同一个关键词，pixiv 每次返回的都是同一批结果，所以用户会看到
"目标不变的话，发的图不会刷新"。

解法不是随机（会重复），而是**排序**：把候选排成
"没发过的（保持 API 顺序）在前、发过的按最早发送在后"。
调用方照旧取前 N 个，效果就是头几次都发新的，全发完之后最久没出现的先回来。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import framework_stub  # noqa: E402

framework_stub.install()

from plugin_loader import load_package  # noqa: E402

load_package()

from send_pixiv_under_test.core.models import Illust  # noqa: E402
from send_pixiv_under_test.services.pixiv_service import PixivService  # noqa: E402


def make(illust_id: int) -> Illust:
    """造一个最小可用的作品。

    Args:
        illust_id: 作品 ID。

    Returns:
        `Illust` 实例。
    """
    return Illust(id=illust_id, title=f"w{illust_id}")


def ids(illusts: list[Illust]) -> list[int]:
    """取出 ID 序列，便于断言。

    Args:
        illusts: 作品列表。

    Returns:
        ID 列表。
    """
    return [item.id for item in illusts]


class RotateTest(unittest.TestCase):
    """`PixivService._rotate` 的用例。"""

    def setUp(self) -> None:
        """一批固定候选：ID 1..5，API 顺序。"""
        self.pool = [make(i) for i in range(1, 6)]

    def test_without_avoid_nothing_changes(self) -> None:
        self.assertEqual(ids(PixivService._rotate(self.pool, None)), [1, 2, 3, 4, 5])
        self.assertEqual(ids(PixivService._rotate(self.pool, {})), [1, 2, 3, 4, 5])

    def test_fresh_first_in_api_order(self) -> None:
        "发过 2、4 之后，没发过的 1、3、5 应该排在前面且保持原序。"
        avoid = {2: 100.0, 4: 200.0}
        self.assertEqual(ids(PixivService._rotate(self.pool, avoid)), [1, 3, 5, 2, 4])

    def test_stale_sorted_by_oldest_first(self) -> None:
        "都发过时，最早发的先回来——自然轮换。"
        avoid = {1: 500.0, 2: 100.0, 3: 300.0, 4: 200.0, 5: 400.0}
        self.assertEqual(ids(PixivService._rotate(self.pool, avoid)), [2, 4, 3, 5, 1])

    def test_partial_avoid_keeps_others(self) -> None:
        avoid = {3: 50.0}
        self.assertEqual(ids(PixivService._rotate(self.pool, avoid)), [1, 2, 4, 5, 3])

    def test_avoid_with_unknown_ids_is_harmless(self) -> None:
        "记录里有已经不在候选里的 ID 时不该出错。"
        avoid = {999: 1.0, 2: 2.0}
        self.assertEqual(ids(PixivService._rotate(self.pool, avoid)), [1, 3, 4, 5, 2])

    def test_take_first_n_gives_fresh_images(self) -> None:
        "这是调用方真正的用法：每次取前 2 个，两次之间不重复。"
        avoid: dict[int, float] = {}
        first = ids(PixivService._rotate(self.pool, avoid))[:2]
        self.assertEqual(first, [1, 2])
        # 模拟第一次发送后记录时间
        avoid.update({1: 10.0, 2: 20.0})
        second = ids(PixivService._rotate(self.pool, avoid))[:2]
        self.assertEqual(second, [3, 4])
        avoid.update({3: 30.0, 4: 40.0})
        third = ids(PixivService._rotate(self.pool, avoid))[:2]
        self.assertEqual(third, [5, 1], "只剩 5 没发过，第二个位置应该轮到最早发的 1")

    def test_empty_pool(self) -> None:
        self.assertEqual(PixivService._rotate([], {1: 1.0}), [])


if __name__ == "__main__":
    unittest.main()
