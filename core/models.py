"""Pixiv 作品的数据模型。

App API 返回的是蛇形命名的大字典，这里把它收敛成插件内部使用的轻量对象，
避免业务代码到处写 `data["meta_pages"][0]["image_urls"]["original"]`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

__all__ = ["ImagePage", "Illust", "ImageQuality", "master1200_url"]

ImageQuality = Literal["master1200", "large", "original"]

#: master1200 档位的真实后缀。**永远是 jpg**，哪怕原图是 png——
#: 实测 `_master1200.png` 一律 404，`_master1200.jpg` 才是真的。
MASTER_SUFFIX = ".jpg"


@dataclass(slots=True)
class ImagePage:
    """作品中的单张图片。

    同时保留 `large` 与 `original` 两个地址：聊天场景多数时候用 `large` 就够了，
    原图动辄十几 MB，发进群既慢又容易被平台吞掉。

    Attributes:
        index: 在作品中的页码，从 0 开始。
        url: `large` 档地址（约 1200px 长边），下载时必须带 Referer。
        original_url: 原图地址；接口没给时为原图的回退值。
        width: `large` 档宽度，未知为 0。
        height: `large` 档高度，未知为 0。
    """

    index: int
    url: str
    original_url: str = ""
    width: int = 0
    height: int = 0

    def pick(self, quality: str = "master1200") -> str:
        """按质量档位取首选地址。

        Args:
            quality: `master1200`（默认）/ `large` / `original`。

        Returns:
            该档位的地址；该档位不适用时回退到别的档。
        """
        candidates = self.candidates(quality)
        return candidates[0] if candidates else ""

    def candidates(self, quality: str = "master1200") -> list[str]:
        """给出该档位的下载回退链（首选在前）。

        master1200 并非对所有作品都存在（老作品、特殊格式会 404），
        所以调用方需要一个回退顺序，而不是只试一次。

        Args:
            quality: `master1200` / `large` / `original`。

        Returns:
            去重后的地址列表。
        """
        master = master1200_url(self.original_url)
        order = {
            "master1200": [master, self.url, self.original_url],
            "large": [self.url, master, self.original_url],
            "original": [self.original_url, master, self.url],
        }.get((quality or "").strip().lower(), [master, self.url, self.original_url])

        seen: set[str] = set()
        result: list[str] = []
        for url in order:
            if url and url not in seen:
                seen.add(url)
                result.append(url)
        return result

    @staticmethod
    def suffix_for(url: str) -> str:
        """从图片地址推断真实文件后缀。

        App API 给的 `large` 地址是 webp 缩略图（路径里带 `_webp/`），
        文件名却仍然是 `.jpg`——照抄会写出"扩展名撒谎"的文件。

        Args:
            url: 图片地址。

        Returns:
            形如 `.jpg` / `.webp` 的后缀。
        """
        path = url.split("?")[0]
        if "_webp/" in path or path.lower().endswith(".webp"):
            return ".webp"
        suffix = path[path.rfind(".") :].lower() if "." in path else ""
        return suffix if suffix in (".jpg", ".jpeg", ".png", ".gif", ".webp") else ".jpg"


@dataclass(slots=True)
class Illust:
    """一个 pixiv 作品（插画 / 漫画 / 动图）。

    Attributes:
        id: 作品 ID。
        title: 标题。
        type: `illust` / `manga` / `ugoira`。
        user_id: 作者 ID。
        user_name: 作者名。
        create_date: 投稿时间（ISO 8601 字符串，原样保留）。
        tags: 标签名列表。
        x_restrict: 0 全年龄 / 1 R-18 / 2 R-18G。
        sanity_level: 2 一般 / 4 R-18 / 6 R-18G。
        total_view: 浏览量。
        total_bookmarks: 收藏数。
        page_count: 页数。
        pages: 每页的图片信息。
        is_ai: 是否为 AI 生成作品。
    """

    id: int
    title: str = ""
    type: str = "illust"
    user_id: int = 0
    user_name: str = ""
    create_date: str = ""
    tags: list[str] = field(default_factory=list)
    x_restrict: int = 0
    sanity_level: int = 2
    total_view: int = 0
    total_bookmarks: int = 0
    page_count: int = 1
    pages: list[ImagePage] = field(default_factory=list)
    is_ai: bool = False

    @property
    def url(self) -> str:
        """作品在网页端的地址。"""
        return f"https://www.pixiv.net/artworks/{self.id}"

    @property
    def is_ugoira(self) -> bool:
        """是否为动图（动图需要额外合成 GIF，当前版本不解码）。"""
        return self.type == "ugoira"

    @property
    def page_urls(self) -> list[str]:
        """所有页的 `large` 档地址。"""
        return [page.url for page in self.pages]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Illust":
        """从 App API 的作品字典构造。

        Args:
            data: `/v1/illust/detail`、`/v1/search/illust` 等端点返回的单个作品对象。

        Returns:
            解析后的 :class:`Illust` 实例。
        """
        user = data.get("user") or {}
        tags = [
            str(tag.get("name"))
            for tag in (data.get("tags") or [])
            if isinstance(tag, dict) and tag.get("name")
        ]
        return cls(
            id=_to_int(data.get("id")),
            title=str(data.get("title") or ""),
            type=str(data.get("type") or "illust"),
            user_id=_to_int(user.get("id")),
            user_name=str(user.get("name") or ""),
            create_date=str(data.get("create_date") or ""),
            tags=tags,
            x_restrict=_to_int(data.get("x_restrict")),
            sanity_level=_to_int(data.get("sanity_level")) or 2,
            total_view=_to_int(data.get("total_view")),
            total_bookmarks=_to_int(data.get("total_bookmarks")),
            page_count=max(1, _to_int(data.get("page_count")) or 1),
            pages=_extract_pages(data),
            is_ai=_to_int(data.get("illust_ai_type")) == 2,
        )


def master1200_url(original_url: str) -> str:
    """从原图地址推导 master1200（约 1200px 的 JPEG）地址。

    聊天场景里这是最合适的档位：`large` 只有 600x1200 的 webp 缩略图（十几 KB），
    `original` 动辄好几 MB。master1200 一般几百 KB，画质够看。

    Args:
        original_url: `img-original` 的原图地址。

    Returns:
        推导出的 master1200 地址；无法推导时返回空串。
    """
    if not original_url or "/img-original/" not in original_url:
        return ""
    url = original_url.replace("/img-original/", "/img-master/")
    base, dot, _extension = url.rpartition(".")
    if not dot:
        return ""
    return f"{base}_master1200{MASTER_SUFFIX}"


def _to_int(value: Any) -> int:
    """把 App API 里出现的 "123" 与 123 统一成 int。"""
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _extract_pages(data: dict[str, Any]) -> list[ImagePage]:
    """从作品字典里抽取每一页的图片地址。

    多图作品走 `meta_pages`；单图作品走 `meta_single_page.original_image_url`
    加上 `image_urls`，两者都缺失时返回空列表。
    """
    pages: list[ImagePage] = []

    for index, page in enumerate(data.get("meta_pages") or []):
        if not isinstance(page, dict):
            continue
        urls = page.get("image_urls") or {}
        large = urls.get("large") or urls.get("medium") or urls.get("original")
        original = urls.get("original") or large
        if not large and not original:
            continue
        pages.append(
            ImagePage(
                index=index,
                url=str(large or original),
                original_url=str(original or large),
                width=_to_int(page.get("width")),
                height=_to_int(page.get("height")),
            )
        )

    if pages:
        return pages

    urls = data.get("image_urls") or {}
    large = urls.get("large") or urls.get("medium") or urls.get("square_medium")
    original = (data.get("meta_single_page") or {}).get("original_image_url") or large
    if large or original:
        pages.append(
            ImagePage(
                index=0,
                url=str(large or original),
                original_url=str(original or large),
                width=_to_int(data.get("width")),
                height=_to_int(data.get("height")),
            )
        )
    return pages
