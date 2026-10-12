"""指令的共用逻辑。

前缀识别、路由、参数解析现在都由**框架原生的 Command 组件**负责
（`commands/pixiv_command.py`），这里只留两样东西：

- `effective_rating`：把"指令要求的分级"和"会话允许的上限"合到一起；
- `USAGE`：只给指令不给关键词时的提示语。

`USAGE` 刻意**不以命令前缀开头**——机器人自己发出的消息也会走一遍
`ON_MESSAGE_RECEIVED`，文案要是以 `#` 开头就会被当成新命令（回环）。
"""

from __future__ import annotations

from typing import Literal

__all__ = [
    "effective_rating",
    "rating_rank",
    "RATING_ORDER",
    "USAGE",
    "RatingMode",
]

RatingMode = Literal["safe", "r18", "all"]

#: 分级从松到严的排序，用于"取更严的那个"。
RATING_ORDER: tuple[str, ...] = ("safe", "r18", "all")

#: 只给指令不给关键词时的提示。
USAGE = "用法：#r18 关键词 —— 例如「#r18 猫」"


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
