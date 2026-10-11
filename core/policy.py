"""访问策略：白名单、冷却、每日配额与分级授权。

这些判断都基于 **聊天流 ID**（`stream_id`，框架里是 SHA-256 哈希）。
哈希值人眼不可读，所以先在群里发一次、再从日志里把 ID 抄进配置即可，
配置项的描述里写了取法。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable, Literal

__all__ = ["Policy", "Verdict", "Debouncer"]

RatingMode = Literal["safe", "r18", "all"]


@dataclass(frozen=True, slots=True)
class Verdict:
    """一次准入判断的结果。

    Attributes:
        allowed: 是否放行。
        reason: 不放行的原因（可直接回给用户）。
    """

    allowed: bool
    reason: str = ""


class Policy:
    """按聊天流控制"能不能发、能发什么"。

    Attributes:
        enabled: 插件总开关。
        allowed_streams: 允许使用的聊天流白名单，空列表表示不限制。
        blocked_streams: 禁止使用的聊天流黑名单，优先级高于白名单。
        cooldown_seconds: 同一聊天流两次请求之间的最小间隔。
        daily_quota: 同一聊天流每天最多成功发送多少次，0 表示不限。
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        allowed_streams: Iterable[str] = (),
        blocked_streams: Iterable[str] = (),
        r18_streams: Iterable[str] = (),
        r18g_streams: Iterable[str] = (),
        default_rating: str = "safe",
        cooldown_seconds: float = 30.0,
        daily_quota: int = 0,
    ) -> None:
        """初始化策略。

        Args:
            enabled: 插件总开关。
            allowed_streams: 聊天流白名单。
            blocked_streams: 聊天流黑名单。
            r18_streams: 允许出现 R-18 的聊天流。
            r18g_streams: 允许出现 R-18G 的聊天流。
            default_rating: 未特别授权的聊天流使用的分级模式。
            cooldown_seconds: 冷却秒数。
            daily_quota: 每日成功发送次数上限。
        """
        self.enabled = bool(enabled)
        self.allowed_streams = {s for s in allowed_streams if s}
        self.blocked_streams = {s for s in blocked_streams if s}
        self.r18_streams = {s for s in r18_streams if s}
        self.r18g_streams = {s for s in r18g_streams if s}
        self.default_rating = default_rating
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self.daily_quota = max(0, int(daily_quota))

        self._last_sent: dict[str, float] = {}
        self._quota_day: dict[str, str] = {}
        self._quota_used: dict[str, int] = {}

    # ------------------------------------------------------------------
    # 准入
    # ------------------------------------------------------------------

    def check(self, stream_id: str) -> Verdict:
        """判断这个聊天流当前能否发起一次请求。

        Args:
            stream_id: 聊天流 ID。

        Returns:
            判断结果；被拒时 `reason` 可直接答复用户。
        """
        if not self.enabled:
            return Verdict(False, "Pixiv 插件已被配置禁用")

        if stream_id in self.blocked_streams:
            return Verdict(False, "当前会话被禁止使用 pixiv 功能")

        if self.allowed_streams and stream_id not in self.allowed_streams:
            return Verdict(False, "当前会话不在 pixiv 允许列表内")

        wait = self.cooldown_left(stream_id)
        if wait > 0:
            return Verdict(False, f"手速太快啦，{wait:.0f} 秒后再试")

        if not self.quota_left(stream_id):
            return Verdict(False, f"今天的 pixiv 额度已用完（{self.daily_quota} 次）")

        return Verdict(True)

    def commit(self, stream_id: str) -> None:
        """记录一次成功的发送，用于冷却与配额统计。

        Args:
            stream_id: 聊天流 ID。
        """
        now = time.time()
        self._last_sent[stream_id] = now
        today = _today()
        if self._quota_day.get(stream_id) != today:
            self._quota_day[stream_id] = today
            self._quota_used[stream_id] = 0
        self._quota_used[stream_id] = self._quota_used.get(stream_id, 0) + 1

    # ------------------------------------------------------------------
    # 查询辅助
    # ------------------------------------------------------------------

    def cooldown_left(self, stream_id: str) -> float:
        """距离下次可用还剩多少秒。

        Args:
            stream_id: 聊天流 ID。

        Returns:
            剩余秒数，0 表示已可用。
        """
        if self.cooldown_seconds <= 0:
            return 0.0
        last = self._last_sent.get(stream_id)
        if last is None:
            return 0.0
        return max(0.0, self.cooldown_seconds - (time.time() - last))

    def quota_left(self, stream_id: str) -> bool:
        """今天是否还有配额。

        Args:
            stream_id: 聊天流 ID。

        Returns:
            还有配额返回 `True`。
        """
        if self.daily_quota <= 0:
            return True
        if self._quota_day.get(stream_id) != _today():
            return True
        return self._quota_used.get(stream_id, 0) < self.daily_quota

    def rating_mode_for(self, stream_id: str) -> RatingMode:
        """该聊天流允许的最高分级。

        Args:
            stream_id: 聊天流 ID。

        Returns:
            `safe` / `r18` / `all`。
        """
        if stream_id in self.r18g_streams:
            return "all"
        if stream_id in self.r18_streams:
            return "r18"
        mode = (self.default_rating or "safe").strip().lower()
        return mode if mode in ("safe", "r18", "all") else "safe"  # type: ignore[return-value]

    def reset(self) -> None:
        """清空运行期状态（冷却与配额），供测试与热重载使用。"""
        self._last_sent.clear()
        self._quota_day.clear()
        self._quota_used.clear()


class Debouncer:
    """按 (`namespace`, `key`) 去重的短时防抖。

    主要用于链接自动解析：同一条链接在短时间内被重复贴出时只处理一次。
    """

    def __init__(self, ttl_seconds: float = 300.0) -> None:
        """初始化。

        Args:
            ttl_seconds: 去重窗口秒数。
        """
        self.ttl = max(1.0, float(ttl_seconds))
        self._seen: dict[tuple[str, str], float] = {}

    def hit(self, namespace: str, key: str, ttl: float | None = None) -> bool:
        """判断是否处于去重窗口内，并记录本次访问。

        Args:
            namespace: 命名空间，例如聊天流 ID。
            key: 被去重的键，例如作品 ID。
            ttl: 覆盖实例默认窗口的秒数；<= 0 表示不做去重（永远不算命中）。

        Returns:
            命中（应当跳过）返回 `True`。
        """
        now = time.time()
        self._evict(now)
        slot = (namespace, key)
        last = self._seen.get(slot)
        self._seen[slot] = now

        window = self.ttl if ttl is None else max(0.0, float(ttl))
        if window <= 0:
            return False
        return last is not None and (now - last) < window

    def forget(self, namespace: str, key: str) -> None:
        """撤销一次去重记录。

        用于"命中了但最终没发出去"的情况，避免用户在一段时间内被静默忽略。

        Args:
            namespace: 命名空间。
            key: 被去重的键。
        """
        self._seen.pop((namespace, key), None)

    def _evict(self, now: float) -> None:
        """清掉过期条目，避免字典无限增长。"""
        if len(self._seen) < 512:
            return
        expired = [slot for slot, ts in self._seen.items() if now - ts >= self.ttl]
        for slot in expired:
            self._seen.pop(slot, None)


def _today() -> str:
    """本地日期字符串，用于每日配额换日判断。"""
    return time.strftime("%Y-%m-%d", time.localtime())
