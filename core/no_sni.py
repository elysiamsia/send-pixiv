"""不发 SNI 的 TLS 上下文——用于直连 pixiv 的图片站。

## 为什么需要这个

GFW 对 pixiv 是「DNS 投毒 + SNI 阻断」两道锁。实测结论（2026-10-12）：

- **API 域名（`app-api.pixiv.net` / `oauth.secure.pixiv.net`）在封锁清单里**：
  只要 TLS ClientHello 里带上这些名字，无论连哪个 IP、大小写怎么变、加不加尾点，
  都会在 0.2 秒内被 RST。没有代理就到不了。
- **图片站 `i.pximg.net` 不在清单里**：它的 IP（`210.140.139.129-138`）本身可达，
  **只要不发 SNI 就能正常握手**，服务器返回的是 `*.pximg.net` 的证书。

所以图片可以直连，省掉代理流量、也更快。

## 安全性

不发 SNI 的代价是：TLS 层没法再拿名字去校验主机（没有名字可比）。
这里补上，做法是**证书链仍然交给 OpenSSL 正常验证**（`CERT_REQUIRED`），
再在握手完成的瞬间按证书的 `subjectAltName` 手工比对一次目标域名。
不通过就直接抛 `SSLCertVerificationError`，不会带着错的证书继续用。

参考实现来自 PixEz（`lib/network/pixez_network_settings.dart`）的思路，
但它那边是 `verifyCertificates: false`（完全不验证），这里做得更严一些。
"""

from __future__ import annotations

import ssl
from typing import Any

__all__ = ["make_no_sni_context", "host_matches_cert", "certificate_hosts"]


def certificate_hosts(cert: dict[str, Any]) -> list[str]:
    """取出证书声明的所有 DNS 名。

    Args:
        cert: `ssl.SSLSocket.getpeercert()` 的返回值。

    Returns:
        SAN 里的 DNS 名；没有 SAN 时回退到 CN。
    """
    hosts = [str(value) for key, value in cert.get("subjectAltName", ()) if key == "DNS"]
    if hosts:
        return hosts
    for rdn in cert.get("subject", ()):
        for key, value in rdn:
            if key == "commonName":
                hosts.append(str(value))
    return hosts


def host_matches_cert(host: str, cert: dict[str, Any]) -> bool:
    """判断证书是否覆盖某个主机名。

    按 RFC 6125 的宽松口径：通配符只允许出现在最左侧标签，
    且只匹配一个标签（`*.a.com` 匹配 `b.a.com`，不匹配 `b.c.a.com`）。

    Args:
        host: 目标主机名。
        cert: `getpeercert()` 的返回值。

    Returns:
        覆盖返回 `True`。
    """
    name = (host or "").strip().lower().rstrip(".")
    if not name:
        return False

    for pattern in certificate_hosts(cert):
        candidate = pattern.strip().lower().rstrip(".")
        if not candidate:
            continue
        if candidate == name:
            return True
        if not candidate.startswith("*."):
            continue
        suffix = candidate[2:]
        prefix = name[: -len(suffix) - 1] if name.endswith("." + suffix) else ""
        if prefix and "." not in prefix:
            return True
    return False


class _VerifyingTLSObject:
    """把 SSL 对象包一层，握手一完成就补校验主机名。"""

    __slots__ = ("_inner", "_host")

    def __init__(self, inner: Any, host: str) -> None:
        """初始化。

        Args:
            inner: 真正的 SSL 对象（socket 或 BIO 版都行）。
            host: 期望证书覆盖的主机名。
        """
        self._inner = inner
        self._host = host

    def do_handshake(self, *args: Any, **kwargs: Any) -> Any:
        """握手并在成功后校验证书主机名。

        Returns:
            内层握手的返回值。
        """
        result = self._inner.do_handshake(*args, **kwargs)
        cert = self._inner.getpeercert()
        if not host_matches_cert(self._host, cert):
            raise ssl.SSLCertVerificationError(
                f"证书不覆盖 {self._host}（证书声明: {certificate_hosts(cert)}）"
            )
        return result

    def __getattr__(self, name: str) -> Any:
        """其余属性透传给内层对象。"""
        return getattr(self._inner, name)


def make_no_sni_context(expected_host: str) -> ssl.SSLContext:
    """造一个"不发 SNI、但照样校验证书"的 SSL 上下文。

    Args:
        expected_host: 期望证书覆盖的主机名，例如 `i.pximg.net`。

    Returns:
        可直接交给 httpx 的 `verify=` 使用的上下文。
    """
    context = ssl.create_default_context()
    # 主机名由我们自己按 SAN 校验，所以关掉 TLS 层的（没有 SNI 就没法比）
    context.check_hostname = False
    # 证书链仍然必须通过验证
    context.verify_mode = ssl.CERT_REQUIRED

    original_wrap_bio = context.wrap_bio
    original_wrap_socket = context.wrap_socket

    def wrap_bio(incoming: Any, outgoing: Any, server_hostname: str | None = None, **kwargs: Any) -> Any:
        # 关键：强制 server_hostname=None —— 这是"不发 SNI"的实现方式
        return _VerifyingTLSObject(
            original_wrap_bio(incoming, outgoing, server_hostname=None, **kwargs), expected_host
        )

    def wrap_socket(sock: Any, server_hostname: str | None = None, **kwargs: Any) -> Any:
        return _VerifyingTLSObject(
            original_wrap_socket(sock, server_hostname=None, **kwargs), expected_host
        )

    context.wrap_bio = wrap_bio  # type: ignore[method-assign]
    context.wrap_socket = wrap_socket  # type: ignore[method-assign]
    return context
