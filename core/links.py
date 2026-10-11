"""pixiv 链接识别与解析。

聊天里出现的链接形态很杂，这里统一收敛成 :class:`PixivRef`，
供 Action 与 EventHandler 使用。

实现上刻意**先按主机名筛选、再匹配路径**，而不是拿一条大正则去啃整条 URL：
`evil-pixiv.net/artworks/1` 这种域名里恰好含 `pixiv.net` 的地址必须被挡掉。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal
from urllib.parse import parse_qs, urlsplit

__all__ = ["PixivRef", "RefKind", "extract_refs", "extract_first", "find_urls"]

RefKind = Literal["illust", "user", "novel", "image", "unsupported"]

#: 视为 pixiv 的主机（含子域）。
_PIXIV_HOSTS: Final = ("pixiv.net",)
_IMAGE_HOSTS: Final = ("pximg.net",)

# 带协议的 URL；中文标点与空白作为天然分隔符。
_URL_RE: Final = re.compile(r"https?://[^\s<>\"'\u3000\uff0c\u3002\uff09\u3011\uff1b]+", re.IGNORECASE)
# 用户常常只贴 `pixiv.net/artworks/1` 或 `www.pixiv.net/...`，没有协议头。
_BARE_PIXIV_RE: Final = re.compile(
    r"(?<![\w./-])((?:www\.)?pixiv\.net/[^\s<>\"'\u3000\uff0c\u3002\uff09\u3011\uff1b]+)",
    re.IGNORECASE,
)
# App 内分享出来的深链。
_DEEP_RE: Final = re.compile(r"pixiv://[^\s<>\"'\u3000]+", re.IGNORECASE)

# 路径级模式：主机已经校验过了，这里只看 path（查询串单独处理）。
_ARTWORK_PATH: Final = re.compile(r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?artworks/(?P<id>\d+)", re.IGNORECASE)
_MEMBER_PATH: Final = re.compile(r"/member_illust\.php$", re.IGNORECASE)
_USER_PATH: Final = re.compile(r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?users/(?P<id>\d+)", re.IGNORECASE)
_NOVEL_PATH: Final = re.compile(r"/novel/show\.php$", re.IGNORECASE)
_DEEP_ILLUST: Final = re.compile(r"pixiv://illusts/(?P<id>\d+)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class PixivRef:
    """一条被识别出来的 pixiv 引用。

    Attributes:
        kind: `illust` / `user` / `novel` / `image` / `unsupported`。
        id: 作品或作者 ID；`image` 与 `unsupported` 类型为 0。
        raw: 命中的原始文本。
    """

    kind: RefKind
    id: int
    raw: str

    @property
    def supported(self) -> bool:
        """当前版本能否直接发图。"""
        return self.kind in ("illust", "user")


def find_urls(text: str) -> list[str]:
    """从文本中提取所有 http(s) URL。

    Args:
        text: 任意文本。

    Returns:
        按出现顺序排列的 URL 列表。
    """
    return _URL_RE.findall(text or "")


def _host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    """判断主机名是否属于给定域名（含子域）。

    Args:
        host: 主机名，小写。
        suffixes: 允许的根域名。

    Returns:
        命中返回 `True`。
    """
    if not host:
        return False
    return any(host == suffix or host.endswith("." + suffix) for suffix in suffixes)


def _classify_url(candidate: str) -> PixivRef | None:
    """把一条 URL 判定成 :class:`PixivRef`。

    Args:
        candidate: 完整的 http(s) URL。

    Returns:
        引用对象；不属于 pixiv 时返回 `None`。
    """
    parts = urlsplit(candidate)
    host = (parts.hostname or "").lower()

    if _host_matches(host, _IMAGE_HOSTS):
        return PixivRef(kind="image", id=0, raw=candidate)

    if not _host_matches(host, _PIXIV_HOSTS):
        return None

    path = parts.path or ""
    query = parse_qs(parts.query or "")

    found = _ARTWORK_PATH.search(path)
    if found:
        return PixivRef(kind="illust", id=int(found.group("id")), raw=candidate)

    if _MEMBER_PATH.search(path):
        raw_id = (query.get("illust_id") or [""])[0]
        if raw_id.isdigit():
            return PixivRef(kind="illust", id=int(raw_id), raw=candidate)
        return PixivRef(kind="unsupported", id=0, raw=candidate)

    found = _USER_PATH.search(path)
    if found:
        return PixivRef(kind="user", id=int(found.group("id")), raw=candidate)

    if _NOVEL_PATH.search(path):
        raw_id = (query.get("id") or [""])[0]
        return PixivRef(kind="novel", id=int(raw_id) if raw_id.isdigit() else 0, raw=candidate)

    return PixivRef(kind="unsupported", id=0, raw=candidate)


def extract_refs(text: str) -> list[PixivRef]:
    """从任意文本里提取 pixiv 引用（去重，保序）。

    Args:
        text: 消息文本，可以夹杂普通 URL 与其他内容。

    Returns:
        解析出的引用列表；同一 `(kind, id, raw 来源)` 只保留第一次出现。
    """
    if not text:
        return []

    candidates: list[str] = []
    candidates.extend(_DEEP_RE.findall(text))
    candidates.extend(find_urls(text))
    candidates.extend("https://" + match for match in _BARE_PIXIV_RE.findall(text))

    refs: list[PixivRef] = []
    seen: set[tuple[str, int]] = set()

    for candidate in candidates:
        deep = _DEEP_ILLUST.search(candidate)
        if deep:
            ref = PixivRef(kind="illust", id=int(deep.group("id")), raw=candidate)
        else:
            ref = _classify_url(candidate)
        if ref is None:
            continue
        key = (ref.kind, ref.id)
        if key in seen:
            continue
        seen.add(key)
        refs.append(ref)

    return refs


def extract_first(text: str) -> PixivRef | None:
    """只取第一条 pixiv 引用。

    Args:
        text: 消息文本。

    Returns:
        第一条引用；没有则返回 `None`。
    """
    refs = extract_refs(text)
    return refs[0] if refs else None
