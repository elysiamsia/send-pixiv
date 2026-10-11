"""组件拿"自己所属插件实例"的统一入口。

**为什么不能直接用 `self.plugin`：**

框架在实例化 LLM 组件时，会按"组件签名 → 归属插件"去解析（见
`src/core/utils/llm_tool_call.py` 与 `BaseChatter._resolve_component_plugin`），
**解析不到就兜底成当前 chatter 的插件**。

实测踩过一次：插件因为 `associated_types` 为空而加载失败、没进插件注册表，
于是 Tool 里的 `self.plugin` 变成了 `NeoChatterPlugin`，报出一个
`'NeoChatterPlugin' object has no attribute 'rating_mode_for'` —— 完全看不出根因。
所以这里统一按插件名解析，并且失败时给出能看懂的错误。
"""

from __future__ import annotations

from typing import Any

__all__ = ["PLUGIN_NAME", "resolve_own_plugin"]

#: 本插件的名字，必须与 manifest.name / 插件类 plugin_name 一致。
PLUGIN_NAME = "send-pixiv"


def resolve_own_plugin(component: Any) -> Any | None:
    """解析组件所属的 send-pixiv 插件实例。

    先按名字从框架注册表取（最可靠），取不到再退回框架注入的 `component.plugin`。
    两者都不是本插件时返回 `None`，由调用方给出可读的报错。

    Args:
        component: 组件实例（Tool / Action / EventHandler / Service 均可）。

    Returns:
        本插件实例；解析不到时返回 `None`。
    """
    candidates: list[Any] = []

    try:
        from src.app.plugin_system.api.plugin_api import get_plugin

        candidates.append(get_plugin(PLUGIN_NAME))
    except Exception:  # noqa: BLE001 - 框架 API 不可用时退回注入值
        pass

    candidates.append(getattr(component, "plugin", None))

    for candidate in candidates:
        if candidate is not None and getattr(candidate, "plugin_name", "") == PLUGIN_NAME:
            return candidate
    return None
