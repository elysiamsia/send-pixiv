"""作品分级判定与过滤。

pixiv 用两个字段表达分级：

- `x_restrict`：0 全年龄 / 1 R-18 / 2 R-18G
- `sanity_level`：2 一般 / 4 R-18 / 6 R-18G

两个字段偶尔不同步（例如被举报后 sanity_level 先变），所以取更严格的一方。
"""

from __future__ import annotations

from typing import Final, Literal

from .models import Illust

__all__ = [
    "Rating",
    "RatingMode",
    "RATING_ORDER",
    "classify",
    "is_allowed",
    "normalize_mode",
    "filter_by_mode",
]

Rating = Literal["safe", "r18", "r18g"]
RatingMode = Literal["safe", "r18", "all"]

#: 分级的严格程度排序，数值越大越"重"。
RATING_ORDER: Final[dict[str, int]] = {"safe": 0, "r18": 1, "r18g": 2}

#: 每种模式允许出现的最高分级。
_MODE_CEILING: Final[dict[str, str]] = {
    "safe": "safe",
    "r18": "r18",
    "all": "r18g",
}


def classify(illust: Illust) -> Rating:
    """判定作品分级。

    Args:
        illust: pixiv 作品。

    Returns:
        `safe` / `r18` / `r18g`。
    """
    if illust.x_restrict >= 2 or illust.sanity_level >= 6:
        return "r18g"
    if illust.x_restrict >= 1 or illust.sanity_level >= 4:
        return "r18"
    return "safe"


def normalize_mode(mode: str) -> RatingMode:
    """把配置里写的模式归一化。

    Args:
        mode: 用户配置值，大小写与空白都不敏感。

    Returns:
        合法模式之一，无法识别时退回 `safe`（失败安全）。
    """
    value = (mode or "").strip().lower()
    if value in ("r18", "r-18", "nsfw"):
        return "r18"
    if value in ("all", "any", "allages_all", "unlimited"):
        return "all"
    return "safe"


def is_allowed(mode: str, rating: str) -> bool:
    """判断某个分级在给定模式下是否放行。

    Args:
        mode: `safe` / `r18` / `all`。
        rating: `safe` / `r18` / `r18g`。

    Returns:
        是否允许发送。
    """
    ceiling = _MODE_CEILING.get(normalize_mode(mode), "safe")
    return RATING_ORDER.get(rating, 2) <= RATING_ORDER[ceiling]


def filter_by_mode(illusts: list[Illust], mode: str) -> list[Illust]:
    """按模式过滤作品列表。

    Args:
        illusts: 候选作品。
        mode: `safe` / `r18` / `all`。

    Returns:
        放行的作品（保持原顺序）。
    """
    return [illust for illust in illusts if is_allowed(mode, classify(illust))]
