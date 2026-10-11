"""聊天里出现 pixiv 链接时自动发图。

订阅 `ON_MESSAGE_RECEIVED`。事件总线对处理器有时限，所以这里只做"快速判断"，
真正的下载与发送丢到后台任务里，处理器立刻返回 `PASS` 以免影响其他插件。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseEventHandler
from src.core.components.types import EventType
from src.core.models.message import Message
from src.kernel.event import EventDecision

from ..core.links import extract_refs
from ..core.runtime import resolve_own_plugin
from ..services.pixiv_service import PixivService

if TYPE_CHECKING:
    from ..plugin import SendPixivPlugin

__all__ = ["PixivLinkWatcher"]

logger = get_logger("send-pixiv.handler")


class PixivLinkWatcher(BaseEventHandler):
    """识别消息里的 pixiv 链接并自动发送对应作品。"""

    name: str = "pixiv_link_watcher"
    description: str = "识别聊天中的 pixiv 链接并自动发送作品"

    weight: int = 55
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
            logger.debug(f"[send-pixiv] 链接识别跳过: {exc}")
        return EventDecision.PASS, params

    def _dispatch(self, params: dict[str, Any]) -> None:
        """判断是否需要处理，需要就丢后台。

        Args:
            params: 事件参数。
        """
        plugin = resolve_own_plugin(self)
        if plugin is None:
            logger.warning("[send-pixiv] 拿不到插件实例，跳过链接解析（检查插件是否加载失败）")
            return
        cfg = plugin.cfg
        if cfg is None or not cfg.links.auto_parse:
            return

        message = params.get("message")
        if not isinstance(message, Message):
            return

        text = message.processed_plain_text or (
            message.content if isinstance(message.content, str) else ""
        )
        if not text or "pixiv" not in text.lower():
            return

        stream_id = message.stream_id or ""
        if not stream_id:
            return

        refs = [ref for ref in extract_refs(text) if ref.supported]
        if not refs:
            return

        fresh = [
            ref
            for ref in refs[: max(1, int(cfg.links.max_per_message))]
            if not plugin.debouncer.hit(stream_id, f"{ref.kind}:{ref.id}", ttl=cfg.links.debounce_seconds)
        ]
        if not fresh:
            return

        verdict = plugin.policy.check(stream_id)
        if not verdict.allowed:
            logger.debug(f"[send-pixiv] 链接命中但被策略拒绝: {verdict.reason}")
            return

        plugin.spawn(
            self._deliver(plugin, fresh, stream_id),
            name=f"send-pixiv-link-{stream_id[:8]}",
        )

    async def _deliver(
        self,
        plugin: "SendPixivPlugin",
        refs: list,
        stream_id: str,
    ) -> None:
        """后台下载并发送链接指向的作品。

        Args:
            plugin: 插件实例。
            refs: 命中的 pixiv 引用。
            stream_id: 目标聊天流。
        """
        cfg = plugin.cfg
        if cfg is None:
            return
        service = PixivService(plugin)
        for ref in refs:
            try:
                illusts = await service.resolve(url=ref.raw, count=cfg.download.max_images_per_request)
                if not illusts:
                    continue
                outcome = await service.send(
                    illusts,
                    stream_id=stream_id,
                    count=cfg.download.max_images_per_request,
                    rating_mode=plugin.rating_mode_for(stream_id),
                )
                if outcome.ok:
                    plugin.policy.commit(stream_id)
                    for illust in outcome.works:
                        plugin.mark_sent(stream_id, illust.id)
                    # 链接没有被"用掉"时解除去重记录，避免用户白贴一次。
                else:
                    plugin.debouncer.forget(stream_id, f"{ref.kind}:{ref.id}")
            except Exception as exc:  # noqa: BLE001 - 后台任务必须自己兜住异常
                plugin.debouncer.forget(stream_id, f"{ref.kind}:{ref.id}")
                logger.warning(f"[send-pixiv] 链接处理失败 {ref.raw}: {exc}")
