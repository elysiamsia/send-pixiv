"""refresh_token 的持久化。

pixiv 的 OAuth 端点会在刷新时**轮换** refresh_token：旧的那个随即失效。
所以必须把新 token 落盘，否则第二次刷新就会认证失败。

配置里的 `refresh_token` 只作为初始值（bootstrap）；一旦运行期拿到轮换后的
新 token，就以本模块落盘的文件为准。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

__all__ = ["TokenStore"]


class TokenStore:
    """把 access_token / refresh_token 存在插件数据目录里的一个小 JSON 文件。

    Attributes:
        path: 落盘位置，形如 `data/send-pixiv/token.json`。
    """

    def __init__(self, path: Path) -> None:
        """初始化。

        Args:
            path: JSON 文件路径，父目录不存在时会自动创建。
        """
        self.path = Path(path)
        self._cache: dict[str, Any] | None = None

    def load(self) -> dict[str, Any]:
        """读取缓存内容。

        Returns:
            字典；文件不存在或损坏时返回空字典。
        """
        if self._cache is not None:
            return self._cache
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._cache = raw if isinstance(raw, dict) else {}
        except (OSError, ValueError):
            self._cache = {}
        return self._cache

    def save(
        self,
        *,
        refresh_token: str = "",
        access_token: str = "",
        expires_at: float = 0.0,
        config_refresh_token: str | None = None,
    ) -> None:
        """写回文件。

        Args:
            refresh_token: 当前有效的 refresh_token。
            access_token: 当前 access_token。
            expires_at: access_token 的过期时间戳（秒）。
            config_refresh_token: 配置文件里当时填的值，用于判断用户有没有换过账号。
        """
        data = dict(self.load())
        if refresh_token:
            data["refresh_token"] = refresh_token
        if access_token:
            data["access_token"] = access_token
        if config_refresh_token is not None:
            data["config_refresh_token"] = config_refresh_token
        data["expires_at"] = expires_at
        data["updated_at"] = time.time()
        self._cache = data
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            # 落盘失败不致命：本次进程内仍有内存副本可用。
            pass

    def refresh_token(self) -> str:
        """已落盘的 refresh_token。"""
        value = self.load().get("refresh_token")
        return str(value) if value else ""

    def resolve_refresh_token(self, configured: str = "") -> str:
        """决定这次该用哪个 refresh_token。

        这里有个真实的坑：pixiv 刷新时会**轮换** refresh_token，轮换结果落在本文件里，
        而配置文件里那个很快就变成废票。所以默认要**以落盘的为准**，
        否则第二次启动就会拿旧 token 去刷新，直接 401。

        但反过来，如果用户改了配置文件（换账号、重新授权），配置必须能覆盖旧记录——
        所以额外记下"上次见到的配置值"，两者不一致就说明用户动过手，以配置为准。

        Args:
            configured: 配置文件里填的 refresh_token。

        Returns:
            本次应当使用的 refresh_token。
        """
        data = self.load()
        stored = str(data.get("refresh_token") or "")
        last_configured = str(data.get("config_refresh_token") or "")
        configured = (configured or "").strip()

        if configured and configured != last_configured:
            return configured
        return stored or configured

    def access_token(self) -> str:
        """尚未过期的 access_token（过期或缺失时返回空串）。"""
        data = self.load()
        token = str(data.get("access_token") or "")
        expires_at = float(data.get("expires_at") or 0.0)
        if token and expires_at > time.time() + 60:
            return token
        return ""
