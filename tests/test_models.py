"""数据模型的单元测试。"""

from __future__ import annotations

import unittest

from plugin_loader import load_package

load_package()

from send_pixiv_under_test.core.models import Illust  # noqa: E402


def _multi_page_payload() -> dict:
    """一个两页作品的 App API 返回样例。"""
    return {
        "id": "100",
        "title": "测试作品",
        "type": "illust",
        "user": {"id": "9", "name": "画师"},
        "create_date": "2024-01-01T00:00:00+09:00",
        "tags": [{"name": "風景"}, {"name": "空"}],
        "x_restrict": 0,
        "sanity_level": 2,
        "total_view": 100,
        "total_bookmarks": 20,
        "page_count": 2,
        "illust_ai_type": 1,
        "meta_pages": [
            {
                "image_urls": {
                    "large": "https://i.pximg.net/large-a.jpg",
                    "original": "https://i.pximg.net/original-a.jpg",
                },
                "width": 1200,
                "height": 800,
            },
            {
                "image_urls": {
                    "large": "https://i.pximg.net/large-b.jpg",
                    "original": "https://i.pximg.net/original-b.jpg",
                },
                "width": 1200,
                "height": 800,
            },
        ],
    }


class IllustFromApiTest(unittest.TestCase):
    """`Illust.from_api` 的用例。"""

    def test_multi_page(self) -> None:
        illust = Illust.from_api(_multi_page_payload())
        self.assertEqual(illust.id, 100)
        self.assertEqual(illust.user_id, 9)
        self.assertEqual(illust.user_name, "画师")
        self.assertEqual(illust.tags, ["風景", "空"])
        self.assertEqual(illust.page_count, 2)
        self.assertEqual(len(illust.pages), 2)
        self.assertEqual(illust.pages[1].index, 1)
        self.assertEqual(illust.url, "https://www.pixiv.net/artworks/100")
        self.assertFalse(illust.is_ai)

    def test_quality_pick(self) -> None:
        page = Illust.from_api(_multi_page_payload()).pages[0]
        self.assertEqual(page.pick("large"), "https://i.pximg.net/large-a.jpg")
        self.assertEqual(page.pick("original"), "https://i.pximg.net/original-a.jpg")
        self.assertEqual(page.pick("乱写"), "https://i.pximg.net/large-a.jpg")

    def test_single_page_fallback(self) -> None:
        illust = Illust.from_api(
            {
                "id": 7,
                "image_urls": {"large": "https://i.pximg.net/l.jpg", "medium": "https://i.pximg.net/m.jpg"},
                "meta_single_page": {"original_image_url": "https://i.pximg.net/o.jpg"},
                "page_count": 1,
            }
        )
        self.assertEqual(len(illust.pages), 1)
        self.assertEqual(illust.pages[0].url, "https://i.pximg.net/l.jpg")
        self.assertEqual(illust.pages[0].original_url, "https://i.pximg.net/o.jpg")

    def test_ai_and_ugoira_flags(self) -> None:
        illust = Illust.from_api({"id": 1, "illust_ai_type": 2, "type": "ugoira"})
        self.assertTrue(illust.is_ai)
        self.assertTrue(illust.is_ugoira)

    def test_missing_fields_do_not_crash(self) -> None:
        illust = Illust.from_api({"id": "abc"})
        self.assertEqual(illust.id, 0)
        self.assertEqual(illust.pages, [])
        self.assertEqual(illust.sanity_level, 2)

    def test_large_falls_back_to_original(self) -> None:
        illust = Illust.from_api(
            {"id": 3, "meta_pages": [{"image_urls": {"original": "https://i.pximg.net/o.jpg"}}]}
        )
        self.assertEqual(illust.pages[0].url, "https://i.pximg.net/o.jpg")


if __name__ == "__main__":
    unittest.main()
