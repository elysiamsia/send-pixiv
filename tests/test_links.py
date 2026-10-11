"""链接解析的单元测试。"""

from __future__ import annotations

import unittest

from plugin_loader import load_package

load_package()

from send_pixiv_under_test.core.links import (  # noqa: E402
    extract_first,
    extract_refs,
    find_urls,
)


class ExtractRefsTest(unittest.TestCase):
    """`extract_refs` 的用例。"""

    def test_works_page(self) -> None:
        refs = extract_refs("看看这个 https://www.pixiv.net/artworks/12345678 ！")
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].kind, "illust")
        self.assertEqual(refs[0].id, 12345678)
        self.assertTrue(refs[0].supported)

    def test_locale_prefix_and_anchor(self) -> None:
        refs = extract_refs("https://www.pixiv.net/en/artworks/98765432#3")
        self.assertEqual((refs[0].kind, refs[0].id), ("illust", 98765432))

    def test_legacy_member_illust(self) -> None:
        refs = extract_refs("https://www.pixiv.net/member_illust.php?mode=medium&illust_id=555")
        self.assertEqual((refs[0].kind, refs[0].id), ("illust", 555))

    def test_app_deep_link(self) -> None:
        refs = extract_refs("分享自 pixiv://illusts/4242")
        self.assertEqual((refs[0].kind, refs[0].id), ("illust", 4242))

    def test_user_page(self) -> None:
        refs = extract_refs("作者 https://www.pixiv.net/users/4242")
        self.assertEqual((refs[0].kind, refs[0].id), ("user", 4242))

    def test_novel_is_recognised_but_unsupported(self) -> None:
        refs = extract_refs("https://www.pixiv.net/novel/show.php?id=777")
        self.assertEqual(refs[0].kind, "novel")
        self.assertFalse(refs[0].supported)

    def test_other_sites_ignored(self) -> None:
        self.assertEqual(extract_refs("https://www.bilibili.com/video/BV1xx"), [])
        # 长得像但不是 pixiv 的域名不能误判
        self.assertEqual(extract_refs("https://evil-pixiv.net/artworks/1"), [])
        self.assertEqual(extract_refs("https://pixiv.net.evil.com/artworks/1"), [])

    def test_dedupe_keeps_order(self) -> None:
        text = "https://www.pixiv.net/artworks/1 然后 https://www.pixiv.net/artworks/2 再来 https://www.pixiv.net/artworks/1"
        ids = [ref.id for ref in extract_refs(text)]
        self.assertEqual(ids, [1, 2])

    def test_extract_first(self) -> None:
        self.assertIsNotNone(extract_first("https://www.pixiv.net/artworks/9"))
        self.assertIsNone(extract_first("什么链接都没有"))

    def test_empty_input(self) -> None:
        self.assertEqual(extract_refs(""), [])
        self.assertEqual(find_urls(""), [])


if __name__ == "__main__":
    unittest.main()
