"""给 LLM 用的 pixiv 发图动作。

按框架约定，**发消息属于副作用，要写成 Action 而不是 Tool**；`execute` 的返回值
形如 `(bool, str)`，失败原因会原样回到模型手里。

耗时的下载与发送丢给 `task_manager` 在后台跑，动作本身尽快返回，
避免撞上上层的事件/工具超时。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseAction

from ..core.runtime import resolve_own_plugin
from ..services.pixiv_service import PixivService

if TYPE_CHECKING:
    from ..plugin import SendPixivPlugin

__all__ = ["SendPixivIllustAction"]

logger = get_logger("send-pixiv.action")


class SendPixivIllustAction(BaseAction):
    """把 pixiv 作品发到当前聊天流。

    三种触发方式任选其一：给 `url`（作品或作者主页链接）、给 `keyword`（按标签搜索）、
    给 `ranking`（排行榜）。都不给时按默认排行榜取图。
    """

    name: str = "send_pixiv_illust"
    description: str = (
        "把 pixiv 插画发送到当前聊天。可以传 pixiv 链接（作品或作者主页）、"
        "关键词（按标签搜索）或排行榜模式。适合「来点猫娘的图」「把这张图发出来」这类请求。"
    )
    primary_action: bool = False

    #: 框架要求 Action 必须声明非空的关联类型，否则**整个插件加载失败**
    #: （plugin_manager._register_components 会直接抛异常中断注册）。
    #: 运行时还会用 chat_context.check_types() 过滤：当前聊天流不支持的类型
    #: 会把 Action 剔除。图片是 QQ 群聊的标配，取 "image"。
    associated_types: list[str] = ["image"]

    async def execute(
        self,
        url: Annotated[str, "pixiv 链接：作品页 https://www.pixiv.net/artworks/123 或作者主页 /users/123"] = "",
        keyword: Annotated[str, "按标签搜索的关键词，例如 猫娘、風景"] = "",
        count: Annotated[int, "发送几张图，1-9；留 0 用配置里的默认值"] = 0,
        ranking: Annotated[str, "排行榜模式：day/week/month/day_male/day_female 等，留空不用"] = "",
    ) -> tuple[bool, str]:
        """执行发图动作。

        Args:
            url: pixiv 链接。
            keyword: 搜索关键词。
            count: 发送张数。
            ranking: 排行榜模式。

        Returns:
            `(是否成功受理, 说明文本)`。
        """
        plugin = resolve_own_plugin(self)
        if plugin is None:
            return False, "send-pixiv 插件未正确加载，请查看 bot 日志里的插件加载错误"
        cfg = plugin.cfg
        if cfg is None:
            return False, "插件配置未加载，请检查 config/plugins/send-pixiv/config.toml"

        stream_id = getattr(self.chat_stream, "stream_id", "") or ""
        # 平台要一起带上：配置里可以直接写群号，靠它换算成 stream_id
        platform = getattr(self.chat_stream, "platform", "") or ""
        if not stream_id:
            return False, "拿不到当前聊天流，无法发送"

        verdict = plugin.policy.check(stream_id, platform)
        if not verdict.allowed:
            return False, verdict.reason

        want = int(count or 0) or int(cfg.search.default_count)
        want = max(1, min(want, cfg.download.max_images_per_request))

        # 分级要在检索前算好：R-18 会话得放开服务端过滤，否则搜不到
        mode = plugin.rating_mode_for(stream_id, platform)

        service = PixivService(plugin)
        try:
            illusts = await service.resolve(
                url=(url or "").strip(),
                keyword=(keyword or "").strip(),
                ranking=(ranking or "").strip(),
                count=max(want * 3, 6),
                rating_mode=mode,
            )
        except Exception as exc:  # noqa: BLE001 - 失败信息要能回到模型
            logger.warning(f"[send-pixiv] 解析失败 url={url!r} keyword={keyword!r}: {exc}")
            return False, f"没找到可发送的作品：{exc}"

        if not illusts:
            return False, "没找到符合条件的作品（可能被分级或 AI 过滤挡掉了）"

        reply_to = getattr(self.chat_stream, "message", None)
        reply_to_id = getattr(reply_to, "message_id", "") or None

        plugin.spawn(
            self._deliver(plugin, illusts, stream_id, want, mode, reply_to_id),
            name=f"send-pixiv-action-{stream_id[:8]}",
        )
        first = illusts[0]
        return True, f"找到《{first.title}》，正在下载发送（最多 {want} 张）…"

    async def _deliver(
        self,
        plugin: "SendPixivPlugin",
        illusts: list,
        stream_id: str,
        count: int,
        mode: str,
        reply_to: str | None,
    ) -> None:
        """后台真正的下载与发送。

        Args:
            plugin: 插件实例。
            illusts: 候选作品。
            stream_id: 目标聊天流。
            count: 张数上限。
            mode: 该会话的分级模式。
            reply_to: 回复目标消息 ID。
        """
        try:
            service = PixivService(plugin)
            outcome = await service.send(
                illusts,
                stream_id=stream_id,
                count=count,
                rating_mode=mode,
                reply_to=reply_to,
            )
            if outcome.ok:
                plugin.policy.commit(stream_id)
                for illust in outcome.works:
                    plugin.mark_sent(stream_id, illust.id)
            else:
                logger.info(f"[send-pixiv] 没有成功发出任何图片 stream={stream_id[:8]}")
        except Exception as exc:  # noqa: BLE001 - 后台任务必须自己兜住异常
            logger.error(f"[send-pixiv] 后台发图失败: {exc}", exc_info=True)
