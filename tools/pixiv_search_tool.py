"""给 LLM 用的 pixiv 查询工具。

只做查询、不发图：真正"发消息"这种带副作用的动作按框架约定属于 Action
（见 `..actions.send_pixiv_illust`）。
"""

from __future__ import annotations

from typing import Annotated

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseTool

from ..core.errors import friendly
from ..core.rating import classify, filter_by_mode
from ..core.runtime import resolve_own_plugin
from ..services.pixiv_service import PixivService

__all__ = ["PixivSearchTool"]

logger = get_logger("send-pixiv.tool")


class PixivSearchTool(BaseTool):
    """搜索 pixiv 作品并返回摘要，供模型决定接下来发哪一张。"""

    name: str = "pixiv_search"
    description: str = (
        "在 pixiv 上搜索插画作品。输入关键词（pixiv 标签，如 風景、初音ミク、原神），"
        "返回作品的标题、作者、标签与链接。只查询不发送；要真的把图发到聊天里，"
        "请改用 send_pixiv_illust 动作。"
    )

    async def execute(
        self,
        keyword: Annotated[str, "搜索关键词，建议直接使用 pixiv 上常见的标签"],
        count: Annotated[int, "返回多少条结果，1-10，默认 5"] = 5,
    ) -> tuple[bool, str]:
        """执行搜索。

        Args:
            keyword: 搜索关键词。
            count: 返回条数。

        Returns:
            `(是否成功, 结果文本)`。
        """
        plugin = resolve_own_plugin(self)
        if plugin is None:
            return False, "send-pixiv 插件未正确加载，请查看 bot 日志里的插件加载错误"
        if not keyword or not keyword.strip():
            return False, "请提供搜索关键词"

        service = PixivService(plugin)
        # 触发消息上有平台信息，配置里写群号时靠它换算成 stream_id
        trigger = getattr(self, "trigger_message", None)
        mode = plugin.rating_mode_for(
            getattr(self, "stream_id", "") or "",
            getattr(trigger, "platform", "") or "",
        )
        limit = max(1, min(int(count or 5), 10))
        # 多取一些再按分级过滤，避免全年龄模式下结果被过滤空。
        fetch = limit * 3 if mode == "safe" else limit

        try:
            illusts = await service.resolve(keyword=keyword.strip(), count=fetch)
        except Exception as exc:  # noqa: BLE001 - 统一转成模型可读的失败信息
            logger.warning(f"[send-pixiv] 搜索失败 keyword={keyword!r}: {exc}")
            return False, f"pixiv 搜索失败：{friendly(exc, proxy=plugin.effective_proxy)}"

        allowed = filter_by_mode(illusts, mode)[:limit]
        if not allowed:
            return False, f"没有找到「{keyword}」相关的作品（或都被分级策略过滤了）"

        lines = [f"pixiv 搜索「{keyword}」共 {len(allowed)} 条候选："]
        for index, illust in enumerate(allowed, 1):
            tags = "、".join(illust.tags[:6])
            lines.append(
                f"{index}. {illust.title}（ID {illust.id}，{classify(illust)}）\n"
                f"   作者：{illust.user_name}｜页数：{illust.page_count}｜收藏：{illust.total_bookmarks}\n"
                f"   标签：{tags or '无'}\n"
                f"   链接：{illust.url}"
            )
        lines.append("提示：需要看哪一张，用 send_pixiv_illust 传入对应链接即可。")
        return True, "\n".join(lines)
