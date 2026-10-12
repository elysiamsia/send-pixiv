"""聊天指令：`#r18 关键词` 直接搜图。

订阅 `ON_MESSAGE_RECEIVED`，和链接解析是两条独立的路子：
链接是被动识别，指令是主动触发。

事件总线对处理器有时限，所以这里只做快速判断，下载发送丢后台任务。

**防回环**：机器人自己发出的消息也会走一遍 `ON_MESSAGE_RECEIVED`，
本处理器的回执文案一律不以指令前缀开头（"用法：…"、"没搜到…"），
再加上前缀必须独占消息开头，所以不会自己触发自己。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.send_api import send_text
from src.app.plugin_system.base import BaseEventHandler
from src.core.components.types import EventType
from src.core.models.message import Message
from src.kernel.event import EventDecision

from ..core.commands import USAGE, Command, effective_rating, parse_command
from ..core.runtime import resolve_own_plugin
from ..services.pixiv_service import PixivService

if TYPE_CHECKING:
    from ..plugin import SendPixivPlugin

__all__ = ["PixivCommandWatcher"]

logger = get_logger("send-pixiv.command")


class PixivCommandWatcher(BaseEventHandler):
    """识别 `#r18 关键词` / `#pixiv 关键词` 并直接搜图发送。"""

    name: str = "pixiv_command_watcher"
    description: str = "识别 #r18 关键词 这类指令，直接搜索并发送图片"

    weight: int = 54
    init_subscribe: list[EventType | str] = [EventType.ON_MESSAGE_RECEIVED]

    async def execute(
        self,
        event_name: str,
        params: dict[str, Any],
    ) -> tuple[EventDecision, dict[str, Any]]:
        """事件入口。

        Args:
            event_name: 事件名。
            params: 事件参数，含 `message`。

        Returns:
            始终 `PASS`：本处理器只旁路观察，不拦截消息。
        """
        try:
            self._dispatch(params)
        except Exception as exc:  # noqa: BLE001 - 不因识别失败影响消息链路
            logger.debug(f"[send-pixiv] 指令识别跳过: {exc}")
        return EventDecision.PASS, params

    # ------------------------------------------------------------------
    # 识别
    # ------------------------------------------------------------------

    def _dispatch(self, params: dict[str, Any]) -> None:
        """解析指令、过策略，然后丢后台。

        Args:
            params: 事件参数。
        """
        plugin = resolve_own_plugin(self)
        if plugin is None:
            return
        cfg = plugin.cfg
        if cfg is None or not cfg.trigger.enabled:
            return

        message = params.get("message")
        if not isinstance(message, Message):
            return

        text = message.processed_plain_text or (
            message.content if isinstance(message.content, str) else ""
        )
        if not text or "#" not in text:
            return

        command = parse_command(
            text,
            r18_prefixes=cfg.trigger.r18_prefixes,
            normal_prefixes=cfg.trigger.normal_prefixes,
        )
        if command is None:
            return

        stream_id = message.stream_id or ""
        if not stream_id:
            return
        platform = message.platform or ""

        key = f"cmd:{command.kind}:{command.keyword}"
        if not plugin.debouncer.hit(stream_id, key, ttl=cfg.trigger.debounce_seconds):
            return

        def abort(reason: str) -> None:
            """回一句原因，并解除防抖（否则用户重发也会被挡）。"""
            plugin.debouncer.forget(stream_id, key)
            plugin.spawn(
                self._reply(stream_id, reason),
                name=f"send-pixiv-cmd-reply-{stream_id[:8]}",
            )

        verdict = plugin.policy.check(stream_id, platform)
        if not verdict.allowed:
            logger.debug(f"[send-pixiv] 指令命中但被策略拒绝: {verdict.reason}")
            abort(verdict.reason)
            return

        if not command.keyword:
            abort(USAGE)
            return

        allowed = plugin.rating_mode_for(stream_id, platform)
        mode, refuse = effective_rating(command.requested_rating, allowed)
        if refuse:
            logger.info(f"[send-pixiv] 指令要求 {command.requested_rating}，但会话上限是 {allowed}")
            abort(f"{refuse}。要发 R-18 请把本会话加进 plugin.r18_streams")
            return

        plugin.spawn(
            self._deliver(plugin, command, stream_id, mode),
            name=f"send-pixiv-cmd-{stream_id[:8]}",
        )

    # ------------------------------------------------------------------
    # 后台执行
    # ------------------------------------------------------------------

    async def _deliver(
        self,
        plugin: "SendPixivPlugin",
        command: Command,
        stream_id: str,
        mode: str,
    ) -> None:
        """后台搜索并发送。

        Args:
            plugin: 插件实例。
            command: 解析出的指令。
            stream_id: 目标聊天流。
            mode: 已按会话上限收敛过的分级。
        """
        cfg = plugin.cfg
        if cfg is None:
            return

        key = f"cmd:{command.kind}:{command.keyword}"
        service = PixivService(plugin)
        try:
            illusts = await service.resolve(
                keyword=command.keyword,
                # 多取一些再按分级过滤，避免全年龄模式下被过滤空
                count=max(cfg.download.max_images_per_request * 3, 6),
                rating_mode=mode,
            )
            if not illusts:
                await self._reply(stream_id, f"没搜到「{command.keyword}」的图，换个词试试？")
                plugin.debouncer.forget(stream_id, key)
                return

            outcome = await service.send(
                illusts,
                stream_id=stream_id,
                count=cfg.download.max_images_per_request,
                rating_mode=mode,
            )
            if outcome.ok:
                plugin.policy.commit(stream_id)
                for illust in outcome.works:
                    plugin.mark_sent(stream_id, illust.id)
                return

            plugin.debouncer.forget(stream_id, key)
            detail = "；".join(outcome.skipped[:2]) or "可能都被分级过滤掉了"
            await self._reply(stream_id, f"搜到了但一张都没发出去（{detail}）")
        except Exception as exc:  # noqa: BLE001 - 后台任务必须自己兜住异常
            plugin.debouncer.forget(stream_id, key)
            logger.warning(f"[send-pixiv] 指令处理失败 {command.keyword!r}: {exc}")
            await self._reply(stream_id, f"搜索失败了：{exc}")

    async def _reply(self, stream_id: str, text: str) -> None:
        """回一句提示。

        文案刻意不以指令前缀开头，避免自己触发自己。

        Args:
            stream_id: 目标聊天流。
            text: 提示内容。
        """
        try:
            await send_text(text, stream_id=stream_id)
        except Exception as exc:  # noqa: BLE001 - 回执失败不该抛
            logger.debug(f"[send-pixiv] 指令回执发送失败: {exc}")
