"""send-pixiv 插件入口。

组件清单（与 `manifest.json` 的 `include` 一一对应）：

====================  ==============================  ============================
组件                   名字                            作用
====================  ==============================  ============================
Config                `config`                        配置模型，落到 config/plugins/send-pixiv/
Service               `pixiv_service`                 检索 → 过滤 → 下载 → 发送
Tool                  `pixiv_search`                 给模型查询作品信息（不发图）
Action                `send_pixiv_illust`            给模型发图（带副作用）
EventHandler          `pixiv_link_watcher`           聊天里出现链接时自动发图
====================  ==============================  ============================

运行期状态（HTTP 客户端、图片缓存、限流策略、去重器）统一挂在插件实例上：
Service 在框架里**不是单例**，把状态放在 Service 实例里会随每次 `get_service()` 丢失。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Coroutine, TypeVar

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BasePlugin, register_plugin
from src.kernel.concurrency import get_task_manager

from .actions import SendPixivIllustAction
from .config import SendPixivConfig
from .core.cache import ImageCache
from .core.client import PixivClient
from .core.policy import Debouncer, Policy
from .core.token import TokenStore
from .handlers import PixivLinkWatcher
from .services import PixivService
from .tools import PixivSearchTool

__all__ = ["SendPixivPlugin"]

logger = get_logger("send-pixiv")

_T = TypeVar("_T")

#: 配置缺失时使用的兜底策略：一切请求都拒绝，但不会抛异常。
_FALLBACK_POLICY = Policy(enabled=False)


@register_plugin
class SendPixivPlugin(BasePlugin):
    """Pixiv 发图插件。"""

    plugin_name: str = "send-pixiv"

    configs: list[type] = [SendPixivConfig]
    dependent_components: list[str] = []

    def __init__(self, config: SendPixivConfig | None = None) -> None:
        """初始化插件。

        Args:
            config: 由插件管理器注入的配置实例。
        """
        super().__init__(config)
        self._client: PixivClient | None = None
        self._cache: ImageCache | None = None
        self._policy: Policy | None = None
        self._debouncer = Debouncer()

    # ------------------------------------------------------------------
    # 组件声明
    # ------------------------------------------------------------------

    def get_components(self) -> list[type]:
        """返回插件提供的组件类。

        注意：`manifest.json` 的 `include` 只是声明文档，**这里才是注册真源**。
        要停用某个组件，得改这个方法的返回值，光把 include 里的 enabled 改成
        false 是没用的。

        Returns:
            组件类列表。
        """
        return [
            PixivService,
            PixivSearchTool,
            SendPixivIllustAction,
            PixivLinkWatcher,
        ]

    # ------------------------------------------------------------------
    # 运行期状态
    # ------------------------------------------------------------------

    @property
    def cfg(self) -> SendPixivConfig | None:
        """插件配置；加载失败时为 `None`。"""
        return self.config if isinstance(self.config, SendPixivConfig) else None

    @property
    def client(self) -> PixivClient | None:
        """pixiv 客户端；配置缺失时为 `None`。"""
        self._ensure_runtime()
        return self._client

    @property
    def cache(self) -> ImageCache | None:
        """图片缓存；配置缺失时为 `None`。"""
        self._ensure_runtime()
        return self._cache

    @property
    def policy(self) -> Policy:
        """访问策略；配置缺失时返回"一律拒绝"的兜底策略。"""
        self._ensure_runtime()
        return self._policy or _FALLBACK_POLICY

    @property
    def debouncer(self) -> Debouncer:
        """链接去重器。"""
        return self._debouncer

    def _ensure_runtime(self) -> None:
        """惰性构建客户端、缓存与策略（配置可能晚于构造注入）。"""
        cfg = self.cfg
        if cfg is None:
            return

        if self._policy is None:
            self._policy = Policy(
                enabled=cfg.plugin.enabled,
                allowed_streams=cfg.plugin.allowed_streams,
                blocked_streams=cfg.plugin.blocked_streams,
                r18_streams=cfg.plugin.r18_streams,
                r18g_streams=cfg.plugin.r18g_streams,
                default_rating=cfg.plugin.default_rating,
                cooldown_seconds=cfg.plugin.cooldown_seconds,
                daily_quota=cfg.plugin.daily_quota,
            )

        if self._client is None:
            self._client = PixivClient(
                refresh_token=cfg.pixiv.refresh_token,
                access_token=cfg.pixiv.access_token,
                user_agent=cfg.pixiv.user_agent,
                proxy=cfg.pixiv.proxy,
                timeout=cfg.pixiv.timeout_seconds,
                min_interval=cfg.pixiv.min_interval_seconds,
                max_retries=cfg.pixiv.max_retries,
                token_store=TokenStore(resolve_path(cfg.pixiv.token_path)),
                direct_image=cfg.download.direct_image,
                direct_image_ips=list(cfg.download.direct_image_ips),
            )

        if self._cache is None:
            self._cache = ImageCache(
                resolve_path(cfg.download.cache_dir),
                max_files=cfg.download.max_cache_files,
            )
            try:
                self._cache.ensure_root()
            except OSError as exc:
                logger.warning(f"[send-pixiv] 创建缓存目录失败: {exc}")

    # ------------------------------------------------------------------
    # 给组件用的小工具
    # ------------------------------------------------------------------

    def rating_mode_for(self, stream_id: str) -> str:
        """某个聊天流允许的最高分级。

        Args:
            stream_id: 聊天流 ID。

        Returns:
            `safe` / `r18` / `all`。
        """
        return self.policy.rating_mode_for(stream_id or "")

    def mark_sent(self, stream_id: str, illust_id: int) -> None:
        """记录"这个作品已经在本会话发过"。

        除了做去重，还顺手挡住了自己发出的说明文字里带的链接——
        否则那条消息再被当成入站消息处理就会形成回环。

        Args:
            stream_id: 聊天流 ID。
            illust_id: 作品 ID。
        """
        cfg = self.cfg
        ttl = float(cfg.links.debounce_seconds) if cfg is not None else 300.0
        self._debouncer.hit(stream_id, f"illust:{illust_id}", ttl=ttl)

    def spawn(self, coro: "Coroutine[Any, Any, _T]", *, name: str) -> None:
        """把耗时协程交给 TaskManager 的后台守护任务。

        Args:
            coro: 要执行的协程。
            name: 任务名，便于在日志里定位。
        """
        get_task_manager().create_task(
            coro,
            name=name,
            daemon=True,
            group_name="send-pixiv",
        )

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def on_plugin_loaded(self) -> None:
        """插件加载完成后的初始化。"""
        cfg = self.cfg
        if cfg is None:
            logger.error("[send-pixiv] 配置未加载，插件不可用")
            return

        self._ensure_runtime()
        if not (cfg.pixiv.refresh_token or cfg.pixiv.access_token):
            logger.warning(
                "[send-pixiv] 尚未配置 pixiv 凭据（pixiv.refresh_token），发图功能不可用"
            )
        logger.info(
            "[send-pixiv] 已加载：分级=%s 默认张数=%s 质量=%s 自动解析链接=%s 代理=%s",
            cfg.plugin.default_rating,
            cfg.search.default_count,
            cfg.download.image_quality,
            cfg.links.auto_parse,
            cfg.pixiv.proxy or "直连",
        )

    async def on_plugin_unloaded(self) -> None:
        """卸载前释放连接池。"""
        if self._client is not None:
            await self._client.close()
            self._client = None


def resolve_path(value: str) -> Path:
    """把配置里的路径解析成绝对路径。

    相对路径基于 bot 的工作目录（与 `./data/…` 这类既有约定一致）。

    Args:
        value: 配置里的路径字符串。

    Returns:
        绝对路径。
    """
    path = Path(value or "").expanduser()
    if path.is_absolute():
        return path
    return Path.cwd() / path
