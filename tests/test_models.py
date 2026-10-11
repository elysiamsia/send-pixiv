"""数据模型的单元测试。"""

from __future__ import annotations

import unittest

from plugin_loader import load_package

load_package()

from send_pixiv_under_test.core.models import Illust, ImagePage, master1200_url  # noqa: E402


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
        # 默认档位是 master1200；地址推不出来时退回 large
        self.assertEqual(page.pick(""), "https://i.pximg.net/large-a.jpg")

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


class Master1200Test(unittest.TestCase):
    """master1200 地址的推导。

    实测要点：**后缀永远是 .jpg**。`_master1200.png` 一律 404，
    哪怕是 PNG 原图，真正的 master1200 也是 jpg。
    """

    def test_derive_from_jpg_original(self) -> None:
        self.assertEqual(
            master1200_url("https://i.pximg.net/img-original/img/2025/01/11/x_150_p0.jpg"),
            "https://i.pximg.net/img-master/img/2025/01/11/x_150_p0_master1200.jpg",
        )

    def test_png_original_still_uses_jpg_suffix(self) -> None:
        self.assertEqual(
            master1200_url("https://i.pximg.net/img-original/img/2026/10/12/x_150747678_p0.png"),
            "https://i.pximg.net/img-master/img/2026/10/12/x_150747678_p0_master1200.jpg",
        )

    def test_unrelated_url_returns_empty(self) -> None:
        self.assertEqual(master1200_url(""), "")
        self.assertEqual(master1200_url("https://i.pximg.net/c/600x1200_90_webp/img-master/a.jpg"), "")


class CandidatesTest(unittest.TestCase):
    """档位回退链。"""

    def _page(self) -> ImagePage:
        return ImagePage(
            index=0,
            url="https://i.pximg.net/c/600x1200_90_webp/img-master/a_p0_master1200.jpg",
            original_url="https://i.pximg.net/img-original/img/a_p0.png",
        )

    def test_master1200_first(self) -> None:
        chain = self._page().candidates("master1200")
        self.assertTrue(chain[0].endswith("a_p0_master1200.jpg"))
        self.assertIn("_webp/", chain[1])
        self.assertTrue(chain[2].startswith("https://i.pximg.net/img-original/"))

    def test_original_first(self) -> None:
        self.assertTrue(self._page().candidates("original")[0].startswith("https://i.pximg.net/img-original/"))

    def test_unknown_quality_falls_back_to_default_chain(self) -> None:
        self.assertEqual(self._page().candidates("乱写"), self._page().candidates("master1200"))

    def test_no_duplicates_when_original_missing(self) -> None:
        page = ImagePage(index=0, url="https://i.pximg.net/large.jpg", original_url="")
        chain = page.candidates("master1200")
        self.assertEqual(chain, ["https://i.pximg.net/large.jpg"])


class SuffixForTest(unittest.TestCase):
    """真实后缀的推断（文件名不能撒谎）。"""

    def test_webp_detected_from_path_marker(self) -> None:
        self.assertEqual(
            ImagePage.suffix_for("https://i.pximg.net/c/600x1200_90_webp/img-master/a_p0_master1200.jpg"),
            ".webp",
        )

    def test_plain_jpg(self) -> None:
        self.assertEqual(ImagePage.suffix_for("https://i.pximg.net/img-master/a_p0_master1200.jpg"), ".jpg")

    def test_png(self) -> None:
        self.assertEqual(ImagePage.suffix_for("https://i.pximg.net/img-original/a_p0.png"), ".png")

    def test_query_string_ignored(self) -> None:
        self.assertEqual(ImagePage.suffix_for("https://i.pximg.net/a.jpg?x=1"), ".jpg")

    def test_unknown_suffix_defaults_to_jpg(self) -> None:
        self.assertEqual(ImagePage.suffix_for("https://i.pximg.net/a"), ".jpg")


if __name__ == "__main__":
    unittest.main()
