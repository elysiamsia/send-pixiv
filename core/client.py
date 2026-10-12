"""pixiv App API 异步客户端。

只用两个端点族：

- `https://oauth.secure.pixiv.net/auth/token`：拿 refresh_token 换 access_token
- `https://app-api.pixiv.net`：作品详情 / 搜索 / 排行 / 作者作品 / 图片下载

设计取舍：

- **不依赖第三方 pixiv 库**。App API 的调用面很窄，自己实现可以保持全异步、
  零额外依赖，也不必跟着一个可能停更的库走。
- **refresh_token 会轮换**。pixiv 在刷新时可能下发新的 refresh_token，旧的同时失效；
  拿到新值必须落盘（见 :mod:`.token`），否则下一次刷新就 401。
- **R-18 由 `filter` 参数控制**。App API 默认用 `filter=for_ios` 把 R-18 挡在服务端；
  需要放开时必须不带这个参数，然后在本地按分级再过滤一遍。
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

import httpx

from .models import Illust
from .no_sni import make_no_sni_context
from .token import TokenStore

__all__ = [
    "PixivClient",
    "PixivError",
    "PixivAuthError",
    "PixivNotFoundError",
    "PixivRateLimitError",
    "PixivNetworkError",
]


# --------------------------------------------------------------------------
# 常量：pixiv 官方 Android 客户端的公开 OAuth 凭据，各开源库通用
# --------------------------------------------------------------------------

CLIENT_ID: Final = "MOBrBDS8blbauoSck0ZfDbtuzpyT"
CLIENT_SECRET: Final = "lsACyCD94FhDUtGTXi3QzcFE2uU1hqtDaKeqrdwj"
CLIENT_HASH_SALT: Final = "28c1fdd170a5204386cb1313c7077b32"

#: 图片站的直连 IP。GFW 对 pixiv 是按域名清单拦 SNI 的，
#: `app-api.pixiv.net` / `oauth.secure.pixiv.net` 在清单里，**`i.pximg.net` 不在**。
#: 所以「连 IP + 不发 SNI + 带 Host 头」就能直接下图，不必走代理。
#: 实测（2026-10-12）：下面十个地址全部可用。
DIRECT_IMAGE_IPS: Final = (
    "210.140.139.129",
    "210.140.139.130",
    "210.140.139.131",
    "210.140.139.132",
    "210.140.139.133",
    "210.140.139.134",
    "210.140.139.135",
    "210.140.139.136",
    "210.140.139.137",
    "210.140.139.138",
)

OAUTH_URL: Final = "https://oauth.secure.pixiv.net/auth/token"
APP_API_BASE: Final = "https://app-api.pixiv.net"

#: 图片站防盗链：下载 `i.pximg.net` 必须带这个 Referer。
IMAGE_REFERER: Final = "https://app-api.pixiv.net/"

DEFAULT_USER_AGENT: Final = "PixivAndroidApp/5.0.234 (Android 11; Pixel 6)"

#: 与 pixiv 客户端版本对齐的请求头，缺了它 App API 会返回 403。
APP_HEADERS: Final = {
    "App-OS": "ios",
    "App-OS-Version": "14.6",
    "App-Version": "7.13.3",
    "Referer": IMAGE_REFERER,
}


class PixivError(Exception):
    """pixiv 相关错误基类。"""


class PixivAuthError(PixivError):
    """凭据无效或过期，需要重新登录拿 refresh_token。"""


class PixivNotFoundError(PixivError):
    """作品不存在、已删除或不可见。"""


class PixivRateLimitError(PixivError):
    """被 pixiv 限流（HTTP 429）。"""


class PixivNetworkError(PixivError):
    """网络层错误（超时、连接失败、代理不可用）。"""


class PixivClient:
    """一个尽量小的 pixiv App API 客户端。

    Attributes:
        user_agent: 请求使用的 User-Agent。
        proxy: 代理地址，形如 `http://127.0.0.1:7890`；为空表示直连。
        timeout: 单次请求超时秒数。
        min_interval: 两次 API 调用之间的最小间隔（秒），用于自我限流。
        max_retries: 单个请求的最大尝试次数。
    """

    def __init__(
        self,
        *,
        refresh_token: str = "",
        access_token: str = "",
        user_agent: str = "",
        proxy: str = "",
        timeout: float = 20.0,
        min_interval: float = 1.2,
        max_retries: int = 3,
        token_store: TokenStore | None = None,
        direct_image: bool = False,
        direct_image_ips: list[str] | None = None,
    ) -> None:
        """初始化客户端。

        Args:
            refresh_token: 配置里给的 refresh_token（bootstrap 用）。
            access_token: 已有的 access_token，可直接使用、跳过首次刷新。
            user_agent: 覆盖默认 User-Agent。
            proxy: HTTP(S) 代理地址。
            timeout: 单次请求超时秒数。
            min_interval: API 调用最小间隔秒数。
            max_retries: 最大尝试次数，至少为 1。
            token_store: token 落盘位置；提供后轮换的 refresh_token 才会被持久化。
        """
        self.user_agent = user_agent or DEFAULT_USER_AGENT
        self.proxy = proxy.strip()
        self.timeout = max(1.0, float(timeout))
        self.min_interval = max(0.0, float(min_interval))
        self.max_retries = max(1, int(max_retries))
        self._token_store = token_store
        self.direct_image = bool(direct_image)
        self.direct_image_ips = [ip for ip in (direct_image_ips or []) if ip] or list(DIRECT_IMAGE_IPS)

        self._client: httpx.AsyncClient | None = None
        self._image_client: httpx.AsyncClient | None = None
        self._no_sni_contexts: dict[str, Any] = {}
        self._token_lock = asyncio.Lock()
        self._gate_lock = asyncio.Lock()
        self._last_call = 0.0

        # 配置里的值只是 bootstrap：轮换后的新 token 落在 token_store 里，默认以它为准。
        self._configured_refresh_token = refresh_token.strip()
        self._refresh_token = (
            token_store.resolve_refresh_token(self._configured_refresh_token)
            if token_store
            else self._configured_refresh_token
        )
        self._access_token = access_token.strip() or (
            token_store.access_token() if token_store else ""
        )
        # 未知过期时间：交给 401 分支去强制刷新。
        self._access_token_expires = time.time() + 3600 if self._access_token else 0.0

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    @property
    def client(self) -> httpx.AsyncClient:
        """惰性创建的 httpx 客户端（必须在事件循环内首次访问）。"""
        if self._client is None:
            kwargs: dict[str, Any] = {
                "timeout": httpx.Timeout(self.timeout),
                "follow_redirects": True,
                "headers": {"User-Agent": self.user_agent},
            }
            if self.proxy:
                kwargs["proxy"] = self.proxy
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    def _image_http_client(self, host: str) -> httpx.AsyncClient:
        """给直连图片用的 httpx 客户端（不发 SNI）。

        Args:
            host: 图片站主机名，用于校验证书。

        Returns:
            复用同一个连接的客户端。
        """
        if self._image_client is None:
            context = self._no_sni_contexts.get(host)
            if context is None:
                context = make_no_sni_context(host)
                self._no_sni_contexts[host] = context
            self._image_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                follow_redirects=True,
                headers={"User-Agent": self.user_agent},
                verify=context,
            )
        return self._image_client

    async def close(self) -> None:
        """关闭底层连接池。"""
        for client in (self._client, self._image_client):
            if client is not None:
                await client.aclose()
        self._client = None
        self._image_client = None

    @property
    def has_credential(self) -> bool:
        """是否具备可用凭据（access_token 或 refresh_token）。"""
        return bool(self._access_token or self._refresh_token)

    # ------------------------------------------------------------------
    # 认证
    # ------------------------------------------------------------------

    async def _ensure_access_token(self, force: bool = False) -> str:
        """返回可用的 access_token，必要时先刷新。

        Args:
            force: 为真时忽略缓存，强制刷新一次。

        Returns:
            可用于 `Authorization: Bearer` 的令牌。

        Raises:
            PixivAuthError: 既没有 refresh_token，也没有可用的 access_token。
        """
        async with self._token_lock:
            if not force and self._access_token and self._access_token_expires > time.time() + 60:
                return self._access_token

            if not self._refresh_token:
                if self._access_token:
                    # 只给了 access_token：无从刷新，用到 401 为止。
                    return self._access_token
                raise PixivAuthError(
                    "未配置 pixiv 凭据：请先跑 python scripts/pixiv_login.py 完成一次浏览器授权，"
                    "或把 refresh_token 填进插件配置"
                )

            return await self._refresh_access_token()

    async def _refresh_access_token(self) -> str:
        """用 refresh_token 换一个 access_token，必要时持久化轮换结果。"""
        client_time = _client_time()
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Client-Time": client_time,
            "X-Client-Hash": hashlib.md5(
                (client_time + CLIENT_HASH_SALT).encode("utf-8")
            ).hexdigest(),
        }
        payload = {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "include_policy": "true",
        }

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = await self.client.post(OAUTH_URL, data=payload, headers=headers)
            except httpx.HTTPError as exc:
                last_error = PixivNetworkError(f"连接 pixiv 认证端点失败: {exc}（直连不通时请配置代理）")
                await self._backoff(attempt, base=1.0)
                continue

            if response.status_code in (400, 401, 403):
                raise PixivAuthError(
                    f"refresh_token 无效或已过期（HTTP {response.status_code}），请重新登录 pixiv 获取新的 token"
                )
            if response.status_code != 200:
                last_error = PixivError(
                    f"刷新令牌失败：HTTP {response.status_code} {response.text[:200]}"
                )
                await self._backoff(attempt, base=1.0)
                continue

            try:
                data = response.json()
            except ValueError as exc:
                last_error = PixivError(f"认证端点返回了非 JSON 内容: {exc}")
                await self._backoff(attempt, base=1.0)
                continue

            access_token = str(data.get("access_token") or "")
            if not access_token:
                raise PixivAuthError("认证端点未返回 access_token")

            expires_in = float(data.get("expires_in") or 3600)
            rotated = str(data.get("refresh_token") or "")
            self._access_token = access_token
            self._access_token_expires = time.time() + expires_in

            if rotated and rotated != self._refresh_token:
                self._refresh_token = rotated

            if self._token_store is not None:
                self._token_store.save(
                    refresh_token=self._refresh_token,
                    access_token=self._access_token,
                    expires_at=self._access_token_expires,
                    config_refresh_token=self._configured_refresh_token,
                )
            return self._access_token

        raise last_error or PixivAuthError("刷新 access_token 失败")

    # ------------------------------------------------------------------
    # 请求基础设施
    # ------------------------------------------------------------------

    async def _throttle(self) -> None:
        """保证两次 API 调用之间有最小间隔，避免触发 pixiv 限流。"""
        if self.min_interval <= 0:
            return
        async with self._gate_lock:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self.min_interval:
                await asyncio.sleep(self.min_interval - elapsed)
            self._last_call = time.monotonic()

    async def _backoff(self, attempt: int, base: float = 0.5) -> None:
        """指数退避。

        Args:
            attempt: 当前尝试序号，从 0 开始。
            base: 退避基数秒数。
        """
        await asyncio.sleep(min(base * (2**attempt), 8.0))

    async def _api_get(self, path_or_url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """带鉴权、限流与重试的 GET。

        Args:
            path_or_url: 以 `/` 开头的 App API 路径，或完整 URL（分页 `next_url`）。
            params: 查询参数。

        Returns:
            解析后的 JSON 字典。

        Raises:
            PixivAuthError: 凭据问题（不重试之外的错误）。
            PixivNotFoundError: 目标资源不存在（HTTP 404）。
            PixivError: 其他失败。
        """
        url = path_or_url if path_or_url.startswith("http") else f"{APP_API_BASE}{path_or_url}"
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                token = await self._ensure_access_token(force=attempt > 0)
                await self._throttle()
                response = await self.client.get(
                    url,
                    params=params or {},
                    # App-OS / App-Version 这些头缺了会被 App API 直接 403。
                    headers={**APP_HEADERS, "Authorization": f"Bearer {token}"},
                )
            except PixivAuthError:
                raise
            except httpx.HTTPError as exc:
                last_error = PixivNetworkError(
                    f"请求 pixiv 失败: {exc}（直连不通时请配置代理）"
                )
                await self._backoff(attempt)
                continue

            if response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise PixivError(f"pixiv 返回了非 JSON 内容: {exc}") from exc
                if not isinstance(payload, dict):
                    raise PixivError("pixiv 返回了非预期的数据结构")
                return payload

            if response.status_code == 404:
                raise PixivNotFoundError("作品不存在、已删除或不可见（HTTP 404）")

            if response.status_code in (401, 403):
                if attempt < self.max_retries - 1:
                    # 令牌可能刚失效：清空并强制刷新后重试。
                    self._access_token = ""
                    self._access_token_expires = 0.0
                    last_error = PixivAuthError(f"pixiv 拒绝了请求（HTTP {response.status_code}）")
                    await self._backoff(attempt)
                    continue
                raise PixivAuthError(
                    f"pixiv 拒绝访问（HTTP {response.status_code}），令牌可能已失效"
                )

            if response.status_code == 429:
                last_error = PixivRateLimitError("请求过于频繁，已被 pixiv 限流（HTTP 429）")
                await self._backoff(attempt, base=2.0)
                continue

            last_error = PixivError(
                f"pixiv 接口异常：HTTP {response.status_code} {response.text[:200]}"
            )
            await self._backoff(attempt)

        raise last_error or PixivError("请求 pixiv 失败")

    async def _collect(
        self,
        path: str,
        params: dict[str, Any],
        limit: int,
    ) -> list[Illust]:
        """翻页收集作品，直到达到数量上限或没有下一页。

        Args:
            path: App API 路径。
            params: 首页查询参数。
            limit: 最多收集多少个作品。

        Returns:
            作品列表。
        """
        collected: list[Illust] = []
        payload = await self._api_get(path, params)

        while True:
            for item in payload.get("illusts") or []:
                if isinstance(item, dict):
                    try:
                        collected.append(Illust.from_api(item))
                    except Exception:  # noqa: BLE001 - 单条脏数据不应影响整批
                        continue
            next_url = payload.get("next_url")
            if not next_url or len(collected) >= limit:
                break
            payload = await self._api_get(str(next_url))

        return collected[:limit]

    # ------------------------------------------------------------------
    # 业务端点
    # ------------------------------------------------------------------

    async def illust_detail(self, illust_id: int) -> Illust:
        """获取单个作品详情（含多图作品的每一页）。

        Args:
            illust_id: 作品 ID。

        Returns:
            作品对象。

        Raises:
            PixivNotFoundError: 作品不存在。
        """
        payload = await self._api_get("/v1/illust/detail", {"illust_id": int(illust_id)})
        illust = payload.get("illust")
        if not isinstance(illust, dict):
            raise PixivNotFoundError(f"作品 {illust_id} 不存在或不可见")
        return Illust.from_api(illust)

    async def search_illust(
        self,
        word: str,
        *,
        limit: int = 20,
        sort: str = "date_desc",
        search_target: str = "partial_match_for_tags",
        include_r18: bool = False,
    ) -> list[Illust]:
        """按标签/关键词搜索作品。

        Args:
            word: 搜索词。
            limit: 最多返回多少个作品。
            sort: `date_desc` / `date_asc` / `popular_desc`。
            search_target: `partial_match_for_tags` / `exact_match_for_tags` / `title_and_caption`。
            include_r18: 为真时不带 `filter=for_ios`，让服务端也返回 R-18 作品。

        Returns:
            作品列表。
        """
        params: dict[str, Any] = {
            "word": word,
            "search_target": search_target,
            "sort": sort,
        }
        if not include_r18:
            params["filter"] = "for_ios"
        return await self._collect("/v1/search/illust", params, max(1, limit))

    async def illust_ranking(
        self,
        mode: str = "day",
        *,
        date: str = "",
        limit: int = 20,
        include_r18: bool = False,
    ) -> list[Illust]:
        """获取排行榜。

        Args:
            mode: `day` / `week` / `month` / `day_male` / `day_female` / `week_original` / `week_rookie` / `day_r18` / `day_male_r18` / `day_female_r18` / `week_r18`。
            date: 日期 `YYYY-MM-DD`，留空为最近一期。
            limit: 最多返回多少个作品。
            include_r18: 是否放开服务端 R-18 过滤。

        Returns:
            作品列表。
        """
        params: dict[str, Any] = {"mode": mode}
        if not include_r18:
            params["filter"] = "for_ios"
        if date:
            params["date"] = date
        return await self._collect("/v1/illust/ranking", params, max(1, limit))

    async def user_illusts(
        self,
        user_id: int,
        *,
        limit: int = 20,
        include_r18: bool = False,
    ) -> list[Illust]:
        """获取某位作者的插画作品。

        Args:
            user_id: 作者 ID。
            limit: 最多返回多少个作品。
            include_r18: 是否放开服务端 R-18 过滤。

        Returns:
            作品列表。
        """
        params: dict[str, Any] = {"user_id": int(user_id), "type": "illust"}
        if not include_r18:
            params["filter"] = "for_ios"
        return await self._collect("/v1/user/illusts", params, max(1, limit))

    # ------------------------------------------------------------------
    # 图片下载
    # ------------------------------------------------------------------

    async def download_image(self, url: str, dest: Path) -> Path:
        """下载单张图片到本地。

        开了 `direct_image` 时先试直连（连 IP + 不发 SNI，见 :mod:`.no_sni`），
        失败再退回常规路径（该走代理走代理）。

        Args:
            url: `i.pximg.net` 图片地址。
            dest: 目标文件路径，父目录会自动创建。

        Returns:
            写入完成的文件路径。

        Raises:
            PixivError: 两种方式都失败。
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)

        last_error: Exception | None = None
        for direct in self._download_modes(url):
            try:
                return await self._download_once(url, dest, direct=direct)
            except Exception as exc:  # noqa: BLE001 - 换下一种方式再试
                last_error = exc

        raise last_error or PixivError("下载图片失败")

    def _download_modes(self, url: str) -> list[bool]:
        """给出这次下载要尝试的方式（直连在前）。

        Args:
            url: 图片地址。

        Returns:
            `[True, False]` 或 `[False]`。
        """
        if self.direct_image and "pximg.net" in url and self.direct_image_ips:
            return [True, False]
        return [False]

    async def _download_once(self, url: str, dest: Path, *, direct: bool) -> Path:
        """按指定方式下载一次。

        Args:
            url: 图片地址。
            dest: 目标路径。
            direct: 是否走直连（连 IP、不发 SNI）。

        Returns:
            写入完成的文件路径。
        """
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        headers = {"Referer": IMAGE_REFERER}
        target = url
        client = self.client

        if direct and host:
            ip = self.direct_image_ips[0]
            target = f"https://{ip}{parsed.path}"
            if parsed.query:
                target = f"{target}?{parsed.query}"
            # 连的是 IP，路由得靠 Host 头，否则 nginx 会 421
            headers["Host"] = host
            client = self._image_http_client(host)

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = await client.get(target, headers=headers)
            except httpx.HTTPError as exc:
                last_error = PixivNetworkError(f"下载图片失败: {exc}")
                await self._backoff(attempt)
                continue

            if response.status_code == 200 and response.content:
                temp = dest.with_name(dest.name + ".part")
                temp.write_bytes(response.content)
                temp.replace(dest)
                return dest

            if response.status_code in (403, 404):
                raise PixivError(f"图片不可访问（HTTP {response.status_code}）")

            last_error = PixivError(f"下载图片失败：HTTP {response.status_code}")
            await self._backoff(attempt)

        raise last_error or PixivError("下载图片失败")


def _client_time() -> str:
    """生成与 JS `new Date().toISOString()` 同格式的 UTC 时间戳。

    pixiv 的 `X-Client-Hash` 是对这个字符串加盐求 md5，格式必须一致。
    """
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
