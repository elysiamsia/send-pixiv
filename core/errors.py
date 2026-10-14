"""错误话术层：把技术异常翻译成用户能看懂、且知道下一步该做什么的话。

为什么要单独一层：

- 群里出现的应该是「连不上 pixiv，可能没配代理」，
  而不是 `[WinError 10061] 由于目标计算机积极拒绝，无法连接`；
- 技术原文留在日志里，人话进聊天流。

设计原则：**每句话都尽量给出下一步动作**，而不只是描述现象。
措辞还会随上下文变化——同样是"连不上"，没配代理和配了代理但代理挂了，
用户该做的事完全不同（见 :func:`network_hint`）。

异常类也定义在这里（而不是 `client`），这样话术层不必反向依赖网络层。
`core.client` 会原样再导出它们，旧引用不受影响。
"""

from __future__ import annotations

__all__ = [
    "PixivError",
    "PixivAuthError",
    "PixivNotFoundError",
    "PixivRateLimitError",
    "PixivNetworkError",
    "friendly",
    "network_hint",
]


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


#: 回执里保留的最大字数。再长的技术细节对用户没有意义，留给日志。
_MAX_LEN = 160

#: 重新授权该去哪找方法。**不能写 scripts/ 路径**——市场装 .mfp 的用户
#: 手里根本没有 scripts 目录，照着做会扑空（实测反馈过）。
REAUTH_HINT = "获取方法见项目 README 的「配置凭据」一节"


def network_hint(proxy: str = "") -> str:
    """针对"连不上 pixiv"给出下一步动作。

    Args:
        proxy: 当前**实际生效**的代理地址；空串表示没有代理。

    Returns:
        一句可操作的建议。
    """
    if proxy:
        return (
            f"已配置的代理 {proxy} 连不上 pixiv，"
            "请确认代理软件正在运行、地址与端口没写错"
        )
    return (
        "当前没有配置代理，而 pixiv 的 API 域名在大陆网络下无法直连——"
        "请把代理地址填进 config/plugins/send-pixiv/config.toml 的 pixiv.proxy"
        "（例如 http://127.0.0.1:7890）"
    )


def friendly(exc: BaseException, *, proxy: str = "") -> str:
    """把异常翻译成用户能看懂的一句话。

    Args:
        exc: 捕获到的异常。
        proxy: 当前实际生效的代理，用于给出对症的建议。

    Returns:
        可以直接发进聊天流的文案。
    """
    if isinstance(exc, PixivAuthError):
        return f"pixiv 登录凭据失效了，需要重新授权（{REAUTH_HINT}）"
    if isinstance(exc, PixivNetworkError):
        return f"连不上 pixiv：{network_hint(proxy)}"
    if isinstance(exc, PixivRateLimitError):
        return "pixiv 提示请求太频繁了，过一会儿再试"
    if isinstance(exc, PixivNotFoundError):
        return "这个作品不存在、已删除或不可见"

    # 其他错误（含"图片体积超限"这类本身就很人话的 ValueError）保留原文，
    # 只做长度截断——丢掉反而会让用户失去唯一的线索。
    text = str(exc).strip() or type(exc).__name__
    return text[:_MAX_LEN]
