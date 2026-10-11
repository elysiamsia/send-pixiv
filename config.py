"""send-pixiv 插件配置定义。

配置文件会生成在 `config/plugins/send-pixiv/config.toml`，改完需要重启 bot
（Neo-MoFox 的插件与配置都不热加载）。
"""

from __future__ import annotations

from typing import ClassVar

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section

_RATING_MODES = "safe（仅全年龄）/ r18（允许 R-18）/ all（不限）"


class SendPixivConfig(BaseConfig):
    """send-pixiv 插件配置。"""

    name: ClassVar[str] = "config"
    description: ClassVar[str] = "Pixiv 发图插件配置"

    @config_section("plugin")
    class PluginSection(SectionBase):
        """开关、白名单、分级与配额。"""

        enabled: bool = Field(default=True, description="是否启用插件")
        default_rating: str = Field(
            default="safe",
            description=f"默认分级模式：{_RATING_MODES}",
        )
        allowed_streams: list[str] = Field(
            default_factory=list,
            description=(
                "允许使用本插件的聊天流 ID 白名单，留空表示所有会话都允许。"
                "stream_id 是框架对会话做的 SHA-256 哈希（不是群号），开 debug 日志发一次图就能看到"
            ),
        )
        blocked_streams: list[str] = Field(
            default_factory=list,
            description="禁止使用的聊天流 ID 黑名单，优先级高于白名单",
        )
        r18_streams: list[str] = Field(
            default_factory=list,
            description="允许出现 R-18 的聊天流 ID（仅建议私聊或小群）",
        )
        r18g_streams: list[str] = Field(
            default_factory=list,
            description="允许出现 R-18G 的聊天流 ID（请谨慎配置）",
        )
        cooldown_seconds: float = Field(
            default=30.0,
            ge=0,
            description="同一会话两次请求之间的冷却秒数，0 表示不限制",
        )
        daily_quota: int = Field(
            default=0,
            ge=0,
            description="同一会话每天最多成功发图次数，0 表示不限制",
        )
    @config_section("pixiv")
    class PixivSection(SectionBase):
        """pixiv 凭据与网络。"""

        refresh_token: str = Field(
            default="",
            description=(
                "pixiv 账号的 refresh_token（必填）。"
                "用本仓库的 python scripts/pixiv_login.py 跑一次浏览器授权即可拿到；"
                "也可以留空，直接让登录脚本把 token 写进 token_path"
            ),
        )
        access_token: str = Field(
            default="",
            description="可选的 access_token，用于临时绕过首次刷新",
        )
        token_path: str = Field(
            default="./data/send-pixiv/token.json",
            description="refresh_token 的落盘位置。pixiv 刷新时会轮换 refresh_token，必须持久化",
        )
        proxy: str = Field(
            default="",
            description="访问 pixiv 的 HTTP(S) 代理，例如 http://127.0.0.1:7890；直连能通就留空",
        )
        user_agent: str = Field(
            default="PixivAndroidApp/5.0.234 (Android 11; Pixel 6)",
            description="请求 pixiv 使用的 User-Agent",
        )
        timeout_seconds: float = Field(
            default=20.0,
            ge=1,
            le=120,
            description="单次请求超时秒数",
        )
        min_interval_seconds: float = Field(
            default=1.2,
            ge=0,
            le=10,
            description="两次 API 调用之间的最小间隔秒数，用于自我限流",
        )
        max_retries: int = Field(
            default=3,
            ge=1,
            le=10,
            description="单个请求的最大尝试次数",
        )

    @config_section("download")
    class DownloadSection(SectionBase):
        """下载与缓存。"""

        cache_dir: str = Field(
            default="./data/send-pixiv/cache",
            description="图片缓存目录（相对路径基于 bot 工作目录）",
        )
        max_cache_files: int = Field(
            default=500,
            ge=0,
            description="缓存保留的最大文件数，0 表示不限制",
        )
        image_quality: str = Field(
            default="large",
            description="发送质量：large（长边约 1200px，推荐）/ original（原图，体积大）",
        )
        max_images_per_request: int = Field(
            default=2,
            ge=1,
            le=9,
            description="单次请求最多发送几张图",
        )
        max_image_mb: float = Field(
            default=8.0,
            ge=0,
            description="单张图片的体积上限（MB），0 表示不限制；超限的图会被跳过",
        )

    @config_section("search")
    class SearchSection(SectionBase):
        """搜索与筛选。"""

        default_count: int = Field(
            default=2,
            ge=1,
            le=9,
            description="用户没指定张数时默认发几张",
        )
        sort: str = Field(
            default="date_desc",
            description="搜索结果排序：date_desc（最新）/ date_asc（最旧）/ popular_desc（最热，需会员）",
        )
        search_target: str = Field(
            default="partial_match_for_tags",
            description="搜索范围：partial_match_for_tags / exact_match_for_tags / title_and_caption",
        )
        ranking_mode: str = Field(
            default="day",
            description="排行榜模式：day / week / month / day_male / day_female / week_rookie / day_r18 等",
        )
        exclude_ai: bool = Field(
            default=False,
            description="是否过滤掉 AI 生成的作品",
        )
        exclude_ugoira: bool = Field(
            default=True,
            description="是否跳过动图作品（当前版本不会合成 GIF）",
        )

    @config_section("links")
    class LinksSection(SectionBase):
        """聊天里出现 pixiv 链接时的自动行为。"""

        auto_parse: bool = Field(
            default=True,
            description="是否自动识别聊天里的 pixiv 链接并发送作品",
        )
        debounce_seconds: float = Field(
            default=300.0,
            ge=0,
            description="同一条作品在同一个会话里的去重窗口秒数，0 表示不去重",
        )
        max_per_message: int = Field(
            default=1,
            ge=1,
            le=5,
            description="单条消息里最多处理几个 pixiv 链接",
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
    pixiv: PixivSection = Field(default_factory=PixivSection)
    download: DownloadSection = Field(default_factory=DownloadSection)
    search: SearchSection = Field(default_factory=SearchSection)
    links: LinksSection = Field(default_factory=LinksSection)
