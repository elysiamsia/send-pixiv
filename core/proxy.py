"""代理发现：帮"开了代理软件但没改配置文件"的用户把代理接上。

**这是大陆用户最容易踩的坑**：他们装好 Clash / v2rayN / sing-box 之后，
通常只会打开客户端的「系统代理」开关——那写的是注册表
`HKCU\\...\\Internet Settings`，而 httpx 的 `trust_env` **只认环境变量**，
完全读不到。于是用户明明挂着代理，插件却仍然直连、必然失败，
最后表现为"这插件没用"。

所以 `pixiv.proxy` 留空时先探测一次：环境变量 → Windows 系统代理，
都没有才认为是真直连。
"""

from __future__ import annotations

import os
from typing import Final

__all__ = ["detect_system_proxy", "is_supported", "normalize"]

#: 按优先级检查的环境变量。大小写两种都看——Windows 上两种写法都常见。
_ENV_KEYS: Final = (
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "ALL_PROXY",
    "all_proxy",
)

#: 注册表里 Internet 设置的位置。
_IE_SETTINGS: Final = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"

#: 明确不支持的代理协议前缀（httpx 不带 socks 支持）。
_SOCKS_PREFIXES: Final = ("socks4://", "socks5://", "socks://")


def normalize(value: str) -> str:
    """把各种写法的代理地址收敛成 httpx 能吃的形式。

    注册表里常见的写法有 `127.0.0.1:7890`、`http=127.0.0.1:7890;https=...`，
    用户手填时还可能写成 `127.0.0.1:7890`（漏了协议）。

    Args:
        value: 原始代理字符串。

    Returns:
        形如 `http://127.0.0.1:7890` 的地址；无法识别时返回空串。
    """
    text = (value or "").strip().strip('"')
    if not text:
        return ""

    # 注册表的多协议写法：取 https= 优先，其次第一个分段
    if "=" in text:
        parts: dict[str, str] = {}
        for segment in text.split(";"):
            name, _, addr = segment.partition("=")
            if addr.strip():
                parts[name.strip().lower()] = addr.strip()
        text = parts.get("https") or parts.get("http") or next(iter(parts.values()), "")

    if not text:
        return ""

    lowered = text.lower()
    if lowered.startswith(_SOCKS_PREFIXES):
        return text  # 原样返回，由 is_supported() 判定并给出提示
    if "://" not in text:
        text = f"http://{text}"
    return text


def is_supported(proxy: str) -> bool:
    """这个代理地址 httpx 能不能用。

    httpx 不带 SOCKS 支持（要装 `httpx[socks]`），而 Clash 一类客户端
    经常同时开 HTTP 与 SOCKS 端口，用户很容易抄成 `socks5://`。

    Args:
        proxy: 代理地址。

    Returns:
        可用返回 `True`。
    """
    return not (proxy or "").strip().lower().startswith(_SOCKS_PREFIXES)


def _windows_system_proxy() -> str:
    """读取 Windows 的「系统代理」设置。

    Returns:
        代理地址；未启用或读不到时返回空串。
    """
    if os.name != "nt":
        return ""
    try:
        import winreg  # noqa: PLC0415 - 仅 Windows 需要
    except ImportError:
        return ""

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _IE_SETTINGS) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not int(enabled):
                return ""
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except (OSError, ValueError, TypeError):
        return ""
    return normalize(str(server))


def detect_system_proxy() -> tuple[str, str]:
    """探测本机可用的代理。

    Returns:
        `(代理地址, 来源说明)`；没探测到返回 `("", "")`。
    """
    for key in _ENV_KEYS:
        value = (os.environ.get(key) or "").strip()
        if value:
            return normalize(value), f"环境变量 {key}"

    value = _windows_system_proxy()
    if value:
        return value, "Windows 系统代理"

    return "", ""
