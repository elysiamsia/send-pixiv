"""图片缓存的单元测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from plugin_loader import load_package

load_package()

from send_pixiv_under_test.core.cache import ImageCache  # noqa: E402


class ImageCacheTest(unittest.TestCase):
    """`ImageCache` 的用例。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.cache = ImageCache(self.tmp / "cache", max_files=10)

    def _put(self, illust_id: int, page: int, quality: str, suffix: str, size: int = 10) -> Path:
        path = self.cache.path_for(illust_id, page, suffix, quality)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
        return path

    def test_roundtrip(self) -> None:
        self.assertIsNone(self.cache.find(1, 0, "master1200"))
        self._put(1, 0, "master1200", ".jpg")
        self.assertIsNotNone(self.cache.find(1, 0, "master1200"))

    def test_quality_is_part_of_the_key(self) -> None:
        "换档位不能命中旧档位的文件——实测踩过这个坑。"
        self._put(1, 0, "large", ".webp")
        self.assertIsNotNone(self.cache.find(1, 0, "large"))
        self.assertIsNone(self.cache.find(1, 0, "master1200"))

    def test_pages_are_separate(self) -> None:
        self._put(1, 0, "master1200", ".jpg")
        self.assertIsNone(self.cache.find(1, 1, "master1200"))

    def test_zero_byte_file_is_a_miss(self) -> None:
        self._put(1, 0, "master1200", ".jpg", size=0)
        self.assertIsNone(self.cache.find(1, 0, "master1200"))

    def test_quality_name_is_sanitised(self) -> None:
        path = self.cache.path_for(1, 0, ".jpg", "../../etc")
        self.assertNotIn("..", str(path.relative_to(self.cache.root)))
        self.assertEqual(path.parent.name, "etc")

    def test_empty_quality_uses_default_segment(self) -> None:
        self.assertEqual(self.cache.path_for(1, 0, ".jpg", "").parent.name, "default")

    def test_bad_suffix_falls_back(self) -> None:
        self.assertTrue(self.cache.path_for(1, 0, "没有点", "large").name.endswith(".jpg"))

    def test_prune_keeps_newest(self) -> None:
        cache = ImageCache(self.tmp / "p", max_files=3)
        import os, time

        paths = []
        for i in range(6):
            p = cache.path_for(i, 0, ".jpg", "master1200")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x" * 5)
            os.utime(p, (1000 + i, 1000 + i))
            paths.append(p)
        removed = cache.prune()
        self.assertEqual(removed, 3)
        self.assertTrue(paths[-1].exists(), "最新的应保留")
        self.assertFalse(paths[0].exists(), "最旧的应被删除")

    def test_prune_unlimited(self) -> None:
        cache = ImageCache(self.tmp / "q", max_files=0)
        self.assertEqual(cache.prune(), 0)


if __name__ == "__main__":
    unittest.main()
