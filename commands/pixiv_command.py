"""聊天指令组件：`#r18 关键词` / `#pixiv 关键词`。

## 为什么用 Command 而不是 EventHandler

框架的 `distributor`（`src/core/transport/distribution/distributor.py`）在
`ON_MESSAGE_RECEIVED` 里做了一件关键的事：

```python
cmd_mgr = get_command_manager()
_path, matched_cls, _args = cmd_mgr.match_command(text)
if matched_cls is not None:
    await cmd_mgr.execute_command(message=message)
    return EventDecision.SUCCESS, params   # ← 直接返回，**不把消息注入聊天流**
```

也就是说：**注册成 Command 的消息不会进 chatter**，模型不会看到它，
也就不会再冒出来回一句"这类内容我不查哦"。

之前用 EventHandler 实现时，消息照样进流，于是插件发完图、LLM 还要再插一句，
体验很怪。改成 Command 之后这个问题从机制上消失。

## 前缀

框架的命令前缀默认只有 `/`（`CommandManager._command_prefixes`）。
`plugin.py` 在加载时会把 `#` **追加**进去（可配置关闭），
所以 `#r18 猫` 和 `/r18 猫` 都能用。

## 为什么重写 execute()

框架默认走 Trie 路由，`_call_handler` 按位置逐个传参，
多词关键词（`#r18 猫 少女`）只会传进去第一个词。
这里直接重写 `execute()`，把 name 之后的**整段文本**当关键词，更符合直觉。

## 为什么不用公共基类

两条指令的流程几乎一样，但 `mpdt plugin check` 不认间接继承
（会报"基类不在已知类型列表中"），所以共用逻辑放在模块级函数里，
两个命令类都**直接继承 `BaseCommand`**。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.send_api import send_text
from src.app.plugin_system.base import BaseCommand

from ..core.commands import USAGE, effective_rating
from ..core.runtime import resolve_own_plugin
from ..services.pixiv_service import PixivService

if TYPE_CHECKING:
    from ..plugin import SendPixivPlugin

__all__ = ["PixivR18Command", "PixivSearchCommand"]

logger = get_logger("send-pixiv.command")


async def _reply(stream_id: str, text: str) -> None:
    """回一句提示。

    Args:
        stream_id: 目标聊天流。
        text: 提示内容。
    """
    try:
        await send_text(text, stream_id=stream_id)
    except Exception as exc:  # noqa: BLE001 - 回执失败不该抛
        logger.debug(f"[send-pixiv] 指令回执发送失败: {exc}")


async def _deliver(
    plugin: "SendPixivPlugin",
    stream_id: str,
    keyword: str,
    mode: str,
) -> None:
    """后台搜索并发送。

    Args:
        plugin: 插件实例。
        stream_id: 目标聊天流。
        keyword: 关键词。
        mode: 已按会话上限收敛过的分级。
    """
    cfg = plugin.cfg
    if cfg is None:
        return

    want = max(1, cfg.download.max_images_per_request)
    # 换图：多取一些候选，并把"最近发过的"排到后面——这样同一个关键词
    # 每次发的都是没发过的，全发过之后自动从头轮一遍。
    pool = max(want * 3, cfg.search.rotate_pool) if cfg.search.rotate else max(want * 3, 6)
    avoid = (
        plugin.recently_sent(stream_id, ttl=cfg.search.rotate_window_seconds)
        if cfg.search.rotate
        else None
    )

    service = PixivService(plugin)
    try:
        illusts = await service.resolve(
            keyword=keyword,
            count=pool,
            rating_mode=mode,
            avoid=avoid,
        )
        if not illusts:
            await _reply(stream_id, f"没搜到「{keyword}」的图，换个词试试？")
            return

        outcome = await service.send(
            illusts,
            stream_id=stream_id,
            count=want,
            rating_mode=mode,
        )
        if outcome.ok:
            plugin.policy.commit(stream_id)
            for illust in outcome.works:
                plugin.mark_sent(stream_id, illust.id)
            return

        detail = "；".join(outcome.skipped[:2]) or "可能都被分级过滤掉了"
        await _reply(stream_id, f"搜到了但一张都没发出去（{detail}）")
    except Exception as exc:  # noqa: BLE001 - 后台任务必须自己兜住异常
        logger.warning(f"[send-pixiv] 指令处理失败 {keyword!r}: {exc}")
        await _reply(stream_id, f"搜索失败了：{exc}")


async def _run(
    command: BaseCommand,
    message_text: str,
    requested_rating: str,
) -> tuple[bool, str]:
    """两条指令共用的流程：策略 → 分级 → 丢后台检索。

    框架已经剥掉了前缀和 name，所以 `message_text` 就是关键词原文。

    Args:
        command: 命令实例（提供 `stream_id` / `_message`）。
        message_text: name 之后的整段文本，例如 `猫 少女`。
        requested_rating: 这条指令"要求"的分级；空串表示按会话默认。

    Returns:
        `(是否受理, 说明)`；说明会进框架日志。
    """
    plugin = resolve_own_plugin(command)
    if plugin is None:
        return False, "send-pixiv 插件未正确加载，请查看 bot 日志里的插件加载错误"

    cfg = plugin.cfg
    if cfg is None or not cfg.trigger.enabled:
        return False, "指令功能已关闭"

    stream_id = command.stream_id or ""
    if not stream_id:
        return False, "拿不到当前聊天流"

    platform = getattr(getattr(command, "_message", None), "platform", "") or ""

    verdict = plugin.policy.check(stream_id, platform)
    if not verdict.allowed:
        await _reply(stream_id, verdict.reason)
        return False, verdict.reason

    keyword = (message_text or "").strip()
    if not keyword:
        await _reply(stream_id, USAGE)
        return False, "缺少关键词"

    allowed = plugin.rating_mode_for(stream_id, platform)
    mode, refuse = effective_rating(requested_rating, allowed)
    if refuse:
        await _reply(stream_id, f"{refuse}。要发 R-18 请把本会话加进 plugin.r18_streams")
        return False, refuse

    plugin.spawn(
        _deliver(plugin, stream_id, keyword, mode),
        name=f"send-pixiv-cmd-{stream_id[:8]}",
    )
    return True, f"正在搜索「{keyword}」"


class PixivR18Command(BaseCommand):
    """`#r18 关键词` —— 搜索 R-18 作品并发送。"""

    name: str = "r18"
    description: str = "搜索并发送 R-18 的 pixiv 作品（需要会话已开启 R-18）"
    command_prefix: str = "/"

    async def execute(self, message_text: str) -> tuple[bool, str]:
        """执行指令。

        Args:
            message_text: name 之后的整段文本。

        Returns:
            `(是否受理, 说明)`。
        """
        return await _run(self, message_text, "r18")


class PixivSearchCommand(BaseCommand):
    """`#pixiv 关键词` —— 按会话默认分级搜索。"""

    name: str = "pixiv"
    description: str = "搜索并发送 pixiv 作品"
    command_prefix: str = "/"

    async def execute(self, message_text: str) -> tuple[bool, str]:
        """执行指令。

        Args:
            message_text: name 之后的整段文本。

        Returns:
            `(是否受理, 说明)`。
        """
        return await _run(self, message_text, "")
