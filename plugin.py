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

import time
from pathlib import Path
from typing import Any, Coroutine, TypeVar

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BasePlugin, register_plugin
from src.kernel.concurrency import get_task_manager

from .actions import SendPixivIllustAction
from .config import SendPixivConfig
from .core.cache import ImageCache
from .core.client import APP_API_BASE, APP_HEADERS, PixivClient
from .core.policy import Debouncer, Policy
from .core.proxy import detect_system_proxy, is_supported
from .core.token import TokenStore
from .commands import PixivR18Command, PixivSearchCommand
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

    configs = [SendPixivConfig]
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
        #: `{聊天流: {作品ID: 发送时间}}`，供"同一个关键词换图"排序用。
        self._sent: dict[str, dict[int, float]] = {}
        #: 实际生效的代理地址（配置值优先，其次自动探测到的系统代理）。
        self._proxy: str = ""
        #: 代理来源说明，用于日志与话术层。
        self._proxy_source: str = ""

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
            PixivR18Command,
            PixivSearchCommand,
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
            self._proxy, self._proxy_source = self._resolve_proxy(cfg)
            self._client = PixivClient(
                refresh_token=cfg.pixiv.refresh_token,
                access_token=cfg.pixiv.access_token,
                user_agent=cfg.pixiv.user_agent,
                proxy=self._proxy,
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

    def _resolve_proxy(self, cfg: SendPixivConfig) -> tuple[str, str]:
        """决定这次用哪个代理。

        配置里写了就用配置的；**没写时自动探测系统代理**——大多数 Windows 用户
        只会打开代理软件（Clash / v2rayN / sing-box）的「系统代理」开关，
        那写的是注册表，而 httpx 只认环境变量、读不到。不探测的话，
        他们明明挂着代理，插件却仍然直连、必然失败，最后只会觉得"这插件没用"。

        Args:
            cfg: 插件配置。

        Returns:
            `(代理地址, 来源说明)`；都没有返回 `("", "")`。
        """
        configured = (cfg.pixiv.proxy or "").strip()
        if configured:
            if not is_supported(configured):
                logger.warning(
                    f"[send-pixiv] pixiv.proxy 填的是 SOCKS 代理（{configured}），"
                    "而 httpx 不支持 SOCKS——请改成同一个客户端的 HTTP 代理端口"
                    "（Clash 的混合端口通常两者都支持，把 socks5:// 换成 http:// 即可）"
                )
            return configured, "配置文件"

        detected, source = detect_system_proxy()
        if not detected:
            return "", ""
        if not is_supported(detected):
            logger.warning(
                f"[send-pixiv] 探测到{source}是 SOCKS 代理（{detected}），"
                "httpx 不支持，已忽略——请在 pixiv.proxy 里填 HTTP 代理端口"
            )
            return "", ""
        logger.info(
            f"[send-pixiv] pixiv.proxy 未配置，已自动使用{source}：{detected}"
            "（要固定下来请显式写进配置）"
        )
        return detected, source

    @property
    def effective_proxy(self) -> str:
        """实际生效的代理地址。

        话术层（:func:`..core.errors.friendly`）用它来决定提示
        "去配代理"还是"去查代理"——两者要做的事完全不同。

        Returns:
            代理地址；直连时为空串。
        """
        self._ensure_runtime()
        return self._proxy

    async def _selfcheck_network(self) -> None:
        """加载后做一次轻量网络自检。

        目的是把"配错了"这件事**当场**暴露在日志里，而不是等几天后某个群友
        第一次发图时才炸——那时用户只会觉得"这插件没用"，然后卸载。

        只打一次 GET（预期 403/404 也算通），不碰凭据、不产生副作用。
        """
        client = self._client
        if client is None:
            return
        proxy = self._proxy
        try:
            await client.client.get(
                f"{APP_API_BASE}/",
                headers=APP_HEADERS,
                timeout=8.0,
            )
        except Exception as exc:  # noqa: BLE001 - 自检失败只提示，不影响加载
            if proxy:
                logger.warning(
                    f"[send-pixiv] 网络自检失败：配置的代理 {proxy} 连不上 pixiv（{exc}）。"
                    "请确认代理软件正在运行、地址与端口没写错"
                )
            else:
                logger.warning(
                    "[send-pixiv] 网络自检失败：连不上 pixiv。大陆网络下 pixiv 的 API 域名"
                    "无法直连，**必须配置代理**——把代理地址填进 "
                    "config/plugins/send-pixiv/config.toml 的 pixiv.proxy"
                    "（例如 http://127.0.0.1:7890），详见 README「大陆网络怎么配」"
                )
            return
        logger.info(f"[send-pixiv] 网络自检通过：能连上 pixiv（代理：{proxy or '直连'}）")

    # ------------------------------------------------------------------
    # 给组件用的小工具
    # ------------------------------------------------------------------

    def rating_mode_for(self, stream_id: str, platform: str = "") -> str:
        """某个聊天流允许的最高分级。

        Args:
            stream_id: 聊天流 ID。
            platform: 平台标识；给上之后配置里可以直接写群号。

        Returns:
            `safe` / `r18` / `all`。
        """
        return self.policy.rating_mode_for(stream_id or "", platform or "")

    def _register_hash_prefix(self) -> None:
        """把 `#` 追加进框架的命令前缀。

        框架默认只认 `/`（`CommandManager._command_prefixes`），而本插件的
        指令习惯写成 `#r18 猫`。这里**追加**而不是替换，避免踩掉别人配的前缀。

        注册成 Command 的消息**不会进聊天流**，所以模型看不到它，
        也就不会再冒出来回一句"这类内容我不查哦"——这正是要 Command 的原因。
        """
        try:
            from src.app.plugin_system.api import command_api

            manager = command_api._get_command_manager()
            current = list(getattr(manager, "_command_prefixes", ["/"]))
            if "#" in current:
                return
            command_api.set_prefixes([*current, "#"])
            logger.info(f"[send-pixiv] 已把 '#' 追加为命令前缀：{[*current, '#']}")
        except Exception as exc:  # noqa: BLE001 - 注册失败不该让插件加载不了
            logger.warning(f"[send-pixiv] 注册 '#' 命令前缀失败，只能使用 /r18：{exc}")

    def _has_credential(self, cfg: SendPixivConfig) -> bool:
        """判断是否具备可用凭据。

        配置里没填 `refresh_token` 不代表没有——登录脚本会把 token 写进
        `token_path`，运行期以那份为准。之前只看配置，导致明明能用却报
        "尚未配置凭据"。

        Args:
            cfg: 插件配置。

        Returns:
            有凭据返回 `True`。
        """
        if cfg.pixiv.refresh_token or cfg.pixiv.access_token:
            return True
        if self._client is not None and self._client.has_credential:
            return True
        return bool(TokenStore(resolve_path(cfg.pixiv.token_path)).refresh_token())

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
        # 另记一份带时间戳的，供"换图"排序用（防抖器只能回答"在不在窗口内"）
        self._sent.setdefault(stream_id, {})[int(illust_id)] = time.time()

    def recently_sent(self, stream_id: str, ttl: float = 3600.0) -> dict[int, float]:
        """本会话最近发过的作品，返回 `{作品ID: 发送时间}`。

        用于"同一个关键词每次换一批图"：调用方按"没发过的优先、
        发过的按最早发送排前"重排候选即可。

        Args:
            stream_id: 聊天流 ID。
            ttl: 超过这个时长的记录视为过期（可以再发），<= 0 表示全部视为过期。

        Returns:
            作品 ID 到发送时间戳的映射。
        """
        record = self._sent.get(stream_id)
        if not record:
            return {}
        now = time.time()
        expired = [key for key, stamp in record.items() if ttl <= 0 or now - stamp >= ttl]
        for key in expired:
            record.pop(key, None)
        return dict(record)

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

        if cfg.trigger.register_hash_prefix:
            self._register_hash_prefix()

        if not self._has_credential(cfg):
            logger.warning(
                "[send-pixiv] 尚未配置 pixiv 凭据：请在 config/plugins/send-pixiv/config.toml "
                "里填 pixiv.refresh_token（获取方法见项目 README 的「配置凭据」一节）"
            )

        # 网络自检丢后台：不阻塞加载，失败也只提示（详见 _selfcheck_network）
        self.spawn(self._selfcheck_network(), name="send-pixiv-selfcheck")

        # 注意：框架的 Logger.info(message, **kwargs) 只接受一个位置参数，
        # 不能像标准 logging 那样写 printf 风格（会报
        # "Logger.info() takes 2 positional arguments but N were given"）。
        logger.info(
            f"[send-pixiv] 已加载：分级={cfg.plugin.default_rating} "
            f"默认张数={cfg.search.default_count} 质量={cfg.download.image_quality} "
            f"自动解析链接={cfg.links.auto_parse} 图片直连={cfg.download.direct_image} "
            f"代理={cfg.pixiv.proxy or '直连'}"
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
