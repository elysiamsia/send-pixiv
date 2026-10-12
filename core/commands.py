"""聊天指令：发 `#r18 关键词` 就直接搜图。

和"等 LLM 自己决定调用工具"相比，指令是**用户主动触发**的——不会因为模型
判断失误而漏掉。指令本身只负责解析，能不能发仍然由策略说了算
（白名单 / 分级上限 / 冷却 / 每日配额）。

解析规则：

- 前缀必须**独占开头**，后面跟空格（或直接结束）；
- 前缀大小写不敏感；
- 只给前缀不给关键词时，`keyword` 为空串，由调用方回一句用法提示。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

__all__ = [
    "Command",
    "parse_command",
    "effective_rating",
    "rating_rank",
    "RATING_ORDER",
    "USAGE",
]

#: 只给前缀不给关键词时的提示。
#: 刻意**不以指令前缀开头**，否则机器人自己发的回执会被当成新指令（回环）。
USAGE = "用法：#r18 关键词 —— 例如「#r18 猫」"

RatingMode = Literal["safe", "r18", "all"]

#: 分级从松到严的排序，用于"取更严的那个"。
RATING_ORDER: tuple[str, ...] = ("safe", "r18", "all")


def rating_rank(mode: str) -> int:
    """把分级换成可比较的序号。

    Args:
        mode: `safe` / `r18` / `all`；写错按 `safe` 处理。

    Returns:
        序号，越大越宽松。
    """
    text = (mode or "").strip().lower()
    return RATING_ORDER.index(text) if text in RATING_ORDER else 0


def effective_rating(requested: str, allowed: str) -> tuple[str, str]:
    """算出实际生效的分级。

    指令可以"要求"某个分级，但会话的上限说了算。要求超过上限时**不静默降级**，
    而是返回原因让调用方明确拒绝——否则用户以为发的是 R-18，其实是全年龄。

    Args:
        requested: 指令要求的分级，空串表示"按会话默认"。
        allowed: 会话允许的上限。

    Returns:
        `(生效模式, 拒绝原因)`；没被拒时原因为空串。
    """
    want = (requested or "").strip().lower()
    cap = (allowed or "").strip().lower()
    if cap not in RATING_ORDER:
        cap = "safe"
    if not want:
        return cap, ""
    if rating_rank(want) > rating_rank(cap):
        return cap, f"当前会话最高只允许 {cap} 级别"
    return want, ""


@dataclass(frozen=True, slots=True)
class Command:
    """一条解析出来的指令。

    Attributes:
        kind: `r18` 表示明确要 R-18；`normal` 表示按会话默认分级。
        keyword: 搜索关键词。
        raw: 命中的原始文本。
    """

    kind: Literal["r18", "normal"]
    keyword: str
    raw: str = ""

    @property
    def requested_rating(self) -> str:
        """这条指令要求的分级（`normal` 为空串＝按会话默认）。"""
        return "r18" if self.kind == "r18" else ""


def _match_prefix(text: str, prefixes: Iterable[str]) -> tuple[str, str] | None:
    """在文本开头匹配一个前缀。

    长的前缀优先，这样同时配了 `#r18` 与 `#r18g` 时不会被短的抢走。

    Args:
        text: 原始文本。
        prefixes: 候选前缀。

    Returns:
        `(命中的前缀, 剩余内容)`；没命中返回 `None`。
    """
    stripped = text.lstrip()
    lowered = stripped.lower()
    for prefix in sorted({(p or "").strip() for p in prefixes}, key=len, reverse=True):
        if not prefix or not lowered.startswith(prefix.lower()):
            continue
        rest = stripped[len(prefix) :]
        # 前缀后面必须是空白或结束，否则 "#r18g" 会被 "#r18" 吃掉
        if rest and not rest[0].isspace():
            continue
        return prefix, rest.strip()
    return None


def parse_command(
    text: str,
    *,
    r18_prefixes: Iterable[str] = ("#r18",),
    normal_prefixes: Iterable[str] = ("#pixiv",),
) -> Command | None:
    """把一条消息解析成指令。

    Args:
        text: 消息文本。
        r18_prefixes: R-18 指令的前缀。
        normal_prefixes: 普通搜索指令的前缀。

    Returns:
        解析出的指令；不是指令时返回 `None`。
    """
    if not text:
        return None

    hit = _match_prefix(text, r18_prefixes)
    if hit is not None:
        return Command(kind="r18", keyword=hit[1], raw=text.strip())

    hit = _match_prefix(text, normal_prefixes)
    if hit is not None:
        return Command(kind="normal", keyword=hit[1], raw=text.strip())

    return None
