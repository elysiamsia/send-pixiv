"""Pixiv 业务服务：检索 → 过滤 → 下载 → 发送。

Service 在框架里**不是单例**（`service_api.get_service()` 每次都新建实例），
所以这里不保存任何跨调用状态，全部状态挂在插件实例上（见 `..plugin`）。
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.send_api import send_image, send_text
from src.app.plugin_system.base import BaseService

from ..core.links import extract_refs
from ..core.models import Illust, ImagePage
from ..core.rating import filter_by_mode

if TYPE_CHECKING:
    from ..plugin import SendPixivPlugin

__all__ = ["PixivService", "SendOutcome"]

logger = get_logger("send-pixiv.service")


class SendOutcome:
    """一次发送的结果统计。

    Attributes:
        sent: 成功发出的图片张数。
        works: 成功发出的作品列表。
        skipped: 被跳过的说明（超体积、下载失败等）。
    """

    __slots__ = ("sent", "works", "skipped")

    def __init__(self) -> None:
        self.sent: int = 0
        self.works: list[Illust] = []
        self.skipped: list[str] = []

    @property
    def ok(self) -> bool:
        """是否至少发出了一张图。"""
        return self.sent > 0


class PixivService(BaseService):
    """把"找到一个作品并把它发进聊天流"这条链路封装起来。"""

    name: str = "pixiv_service"
    description: str = "Pixiv 作品检索、下载与发送能力"

    def __init__(self, plugin: "SendPixivPlugin") -> None:
        """初始化。

        Args:
            plugin: 所属插件实例，运行期状态（客户端、缓存、策略）都从它取。
        """
        super().__init__(plugin)
        self._plugin: "SendPixivPlugin" = plugin  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------

    async def resolve(
        self,
        *,
        url: str = "",
        keyword: str = "",
        ranking: str = "",
        count: int = 4,
    ) -> list[Illust]:
        """把用户的输入（链接 / 关键词 / 榜单）解析成候选作品列表。

        Args:
            url: pixiv 链接，支持作品页、App 深链与作者主页。
            keyword: 搜索关键词（按标签匹配）。
            ranking: 排行榜模式；给了就取排行榜。
            count: 最多返回多少个作品。

        Returns:
            候选作品列表，已按插件配置做过基础清洗。

        Raises:
            ValueError: 输入为空或链接不被支持。
        """
        cfg = self._plugin.cfg
        client = self._plugin.client
        if cfg is None or client is None:
            raise ValueError("插件尚未完成初始化，请检查配置")

        want_r18 = self._plugin.rating_mode_for("") != "safe"
        limit = max(1, count)

        if url:
            refs = extract_refs(url)
            if not refs:
                raise ValueError("没有识别出 pixiv 链接")
            primary = refs[0]
            if primary.kind == "illust":
                return [await client.illust_detail(primary.id)]
            if primary.kind == "user":
                illusts = await client.user_illusts(
                    primary.id, limit=limit, include_r18=want_r18
                )
                return self._clean(illusts)
            if primary.kind == "novel":
                raise ValueError("小说作品暂不支持发送")
            if primary.kind == "image":
                raise ValueError("这是图片直链，不是 pixiv 作品页，暂不支持")
            raise ValueError("暂不支持这种 pixiv 链接")

        if ranking:
            illusts = await client.illust_ranking(
                ranking, limit=limit, include_r18=want_r18
            )
            return self._clean(illusts)

        if keyword:
            illusts = await client.search_illust(
                keyword,
                limit=limit,
                sort=cfg.search.sort,
                search_target=cfg.search.search_target,
                include_r18=want_r18,
            )
            return self._clean(illusts)

        raise ValueError("需要提供 pixiv 链接、搜索关键词或榜单")

    def _clean(self, illusts: list[Illust]) -> list[Illust]:
        """按配置过滤候选作品。

        Args:
            illusts: 原始候选列表。

        Returns:
            过滤后的列表。
        """
        cfg = self._plugin.cfg
        result = [item for item in illusts if item.pages]
        if cfg is not None:
            if cfg.search.exclude_ai:
                result = [item for item in result if not item.is_ai]
            if cfg.search.exclude_ugoira:
                result = [item for item in result if not item.is_ugoira]
        return result

    # ------------------------------------------------------------------
    # 发送
    # ------------------------------------------------------------------

    async def send(
        self,
        illusts: list[Illust],
        *,
        stream_id: str,
        count: int = 2,
        rating_mode: str = "safe",
        reply_to: str | None = None,
    ) -> SendOutcome:
        """下载并发送作品。

        Args:
            illusts: 候选作品（会按分级再过滤一次）。
            stream_id: 目标聊天流。
            count: 最多发送几张图。
            rating_mode: 该会话允许的分级模式。
            reply_to: 要回复的消息 ID。

        Returns:
            发送结果统计。
        """
        outcome = SendOutcome()
        cfg = self._plugin.cfg
        cache = self._plugin.cache
        client = self._plugin.client
        if cfg is None or cache is None or client is None:
            raise ValueError("插件尚未完成初始化，请检查配置")

        budget = max(1, min(count, cfg.download.max_images_per_request))
        allowed = filter_by_mode(illusts, rating_mode)
        if not allowed:
            return outcome

        for illust in allowed:
            if outcome.sent >= budget:
                break
            pages = illust.pages[: budget - outcome.sent]
            sent_here = 0
            for page in pages:
                try:
                    path = await self._fetch(illust, page, cfg.download.image_quality)
                except Exception as exc:  # noqa: BLE001 - 单张失败不应中断整批
                    logger.warning(f"[send-pixiv] 下载失败 illust={illust.id} page={page.index}: {exc}")
                    outcome.skipped.append(f"{illust.id} 第 {page.index + 1} 张下载失败")
                    continue

                if not await self._push(path, illust, stream_id=stream_id, reply_to=reply_to):
                    outcome.skipped.append(f"{illust.id} 第 {page.index + 1} 张发送失败")
                    continue
                sent_here += 1
                outcome.sent += 1

            if sent_here:
                outcome.works.append(illust)

        if outcome.works:
            await self._send_caption(outcome.works, stream_id=stream_id)
        return outcome

    async def _fetch(self, illust: Illust, page: ImagePage, quality: str) -> Path:
        """取一张图：命中缓存直接复用，否则按档位回退链下载。

        为什么要回退链：master1200 并非对所有作品都存在（老作品、特殊格式会 404），
        而 `original` 动辄十几 MB。所以按 `master1200 → large → original` 依次试，
        谁能用就用谁。

        Args:
            illust: 所属作品。
            page: 页码对象。
            quality: 首选档位。

        Returns:
            本地文件路径。

        Raises:
            ValueError: 所有档位都不可用，或图片超过体积上限。
        """
        cache = self._plugin.cache
        client = self._plugin.client
        cfg = self._plugin.cfg
        assert cache is not None and client is not None and cfg is not None

        cached = cache.find(illust.id, page.index, quality)
        if cached is not None:
            cache.touch(cached)
            return cached

        limit_mb = float(cfg.download.max_image_mb or 0)
        last_error: Exception | None = None

        for url in page.candidates(quality):
            suffix = ImagePage.suffix_for(url)
            target = cache.path_for(illust.id, page.index, suffix, quality)
            try:
                path = await client.download_image(url, target)
            except Exception as exc:  # noqa: BLE001 - 换下一个档位再试
                last_error = exc
                logger.debug(f"[send-pixiv] 档位不可用 {url[:90]}: {exc}")
                continue

            if limit_mb > 0 and path.stat().st_size > limit_mb * 1024 * 1024:
                size_mb = path.stat().st_size / 1024 / 1024
                try:
                    path.unlink()
                except OSError:
                    pass
                last_error = ValueError(f"图片体积 {size_mb:.1f}MB 超过上限 {limit_mb:.1f}MB")
                continue
            return path

        raise last_error or ValueError("没有可用的图片地址")

    async def _push(
        self,
        path: Path,
        illust: Illust,
        *,
        stream_id: str,
        reply_to: str | None,
    ) -> bool:
        """把本地图片作为 base64 发进聊天流。

        这里刻意不走 `file://` URL：框架的 `send_media` 只对
        `http(s)://` 与 `file://` 前缀做转换，而 `file://` 在 Windows
        上的路径还原依赖 `url2pathname`，行为不稳。自己读字节转 base64 最确定。

        Args:
            path: 本地图片文件。
            illust: 所属作品。
            stream_id: 目标聊天流。
            reply_to: 回复目标。

        Returns:
            是否发送成功。
        """
        try:
            data = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError as exc:
            logger.warning(f"[send-pixiv] 读取缓存文件失败 {path}: {exc}")
            return False

        try:
            return await send_image(
                data,
                stream_id=stream_id,
                processed_plain_text=f"pixiv《{illust.title}》by {illust.user_name}",
                reply_to=reply_to,
            )
        except Exception as exc:  # noqa: BLE001 - 发送失败不应抛出到调用方
            logger.warning(f"[send-pixiv] 发送图片失败 illust={illust.id}: {exc}")
            return False

    async def _send_caption(self, works: list[Illust], *, stream_id: str) -> None:
        """在图片之后补一条说明文字。

        Args:
            works: 本次成功发送的作品。
            stream_id: 目标聊天流。
        """
        lines: list[str] = []
        for illust in works[:3]:
            lines.append(f"🎨 {illust.title}\n👤 {illust.user_name}\n🔗 {illust.url}")
        if len(works) > 3:
            lines.append(f"…等 {len(works)} 个作品")
        try:
            await send_text("\n".join(lines), stream_id=stream_id)
        except Exception as exc:  # noqa: BLE001 - 说明文字失败不影响图片
            logger.debug(f"[send-pixiv] 发送说明文字失败: {exc}")
