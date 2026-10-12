"""离线跑组件用的「框架替身」。

## 为什么需要它

MoFox 的组件模块在**导入时**就 `from src.app.plugin_system... import ...`，
没有框架就 import 不了。后果是 handler / tool / action 里那些**流程型**的
bug（早退分支写错、判断写反、状态机不对）在单元测试里根本碰不到——
实测已经因此漏过两次：

- `logger.info()` 写了 printf 风格 → 只在 `on_plugin_loaded` 时炸；
- 防抖判断写成 `if not debouncer.hit(...): return`（语义反了）
  → 每条新指令都被静默丢弃，用户连发两次"还是不行"。

这里注入一层最小的替身，让组件能被 import、能被真正驱动。
**只替身不模拟**：不做事件总线，测试自己调 `_dispatch` / `execute`。

用法::

    from framework_stub import install
    install()                      # 必须在导入组件之前
    from <pkg>.handlers.pixiv_command_handler import PixivCommandWatcher
"""

from __future__ import annotations

import sys
import types
from typing import Any

__all__ = ["install", "FakeMessage", "SentMessage", "sent_messages", "clear_sent"]

#: 记录 `send_text` / `send_image` 的调用，供断言。
_SENT: list["SentMessage"] = []


class SentMessage:
    """一次发送调用的记录。"""

    __slots__ = ("kind", "payload", "stream_id")

    def __init__(self, kind: str, payload: str, stream_id: str) -> None:
        """初始化。

        Args:
            kind: `text` 或 `image`。
            payload: 文本内容或 base64 数据。
            stream_id: 目标聊天流。
        """
        self.kind = kind
        self.payload = payload
        self.stream_id = stream_id

    def __repr__(self) -> str:
        """便于断言失败时阅读。"""
        return f"SentMessage({self.kind}, {self.payload[:30]!r}, {self.stream_id[:8]})"


def sent_messages() -> list["SentMessage"]:
    """返回所有发送记录。

    Returns:
        发送记录列表。
    """
    return _SENT


def clear_sent() -> None:
    """清空发送记录。"""
    _SENT.clear()


class FakeMessage:
    """替身版 `Message`，属性随便挂。"""

    def __init__(self, text: str = "", *, stream_id: str = "s" * 8, platform: str = "qq") -> None:
        """初始化。

        Args:
            text: 消息纯文本。
            stream_id: 聊天流 ID。
            platform: 平台标识。
        """
        self.processed_plain_text = text
        self.content = text
        self.stream_id = stream_id
        self.platform = platform
        self.sender_id = "10000"
        self.message_id = "m1"


class _Logger:
    """替身版日志器：只记录，不输出。"""

    def __init__(self, name: str) -> None:
        """初始化。

        Args:
            name: 日志器名字。
        """
        self.name = name
        self.records: list[tuple[str, str]] = []

    def _log(self, level: str, message: str, **_kwargs: Any) -> None:
        """记录一条。"""
        self.records.append((level, message))

    def debug(self, message: str, **kwargs: Any) -> None:
        """DEBUG。"""
        self._log("DEBUG", message, **kwargs)

    def info(self, message: str, **kwargs: Any) -> None:
        """INFO。"""
        self._log("INFO", message, **kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        """WARNING。"""
        self._log("WARNING", message, **kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        """ERROR。"""
        self._log("ERROR", message, **kwargs)

    def critical(self, message: str, **kwargs: Any) -> None:
        """CRITICAL。"""
        self._log("CRITICAL", message, **kwargs)


def _module(name: str) -> types.ModuleType:
    """建（或取）一个替身模块。

    Args:
        name: 模块全名。

    Returns:
        模块对象。
    """
    if name in sys.modules:
        return sys.modules[name]
    module = types.ModuleType(name)
    sys.modules[name] = module
    # 让 `import src.app` 这类父包导入也成立
    if "." in name:
        parent_name, _, child = name.rpartition(".")
        parent = _module(parent_name)
        setattr(parent, child, module)
    return module


def install() -> None:
    """装上框架替身（幂等）。

    必须在导入任何组件模块之前调用。
    """
    if sys.modules.get("src", None) is not None and getattr(sys.modules["src"], "_is_stub", False):
        return

    src = _module("src")
    src._is_stub = True  # type: ignore[attr-defined]

    # --- 日志 ---
    log_api = _module("src.app.plugin_system.api.log_api")
    log_api.get_logger = lambda name, **kw: _Logger(name)  # type: ignore[attr-defined]

    # --- 发送 ---
    send_api = _module("src.app.plugin_system.api.send_api")

    async def send_text(content: str, *, stream_id: str = "", **kwargs: Any) -> bool:
        """替身：记录一次文本发送。"""
        _SENT.append(SentMessage("text", content, stream_id))
        return True

    async def send_image(data: str, *, stream_id: str = "", **kwargs: Any) -> bool:
        """替身：记录一次图片发送。"""
        _SENT.append(SentMessage("image", data, stream_id))
        return True

    send_api.send_text = send_text  # type: ignore[attr-defined]
    send_api.send_image = send_image  # type: ignore[attr-defined]

    # --- 组件基类 ---
    import pydantic

    base = _module("src.app.plugin_system.base")

    class SectionBase(pydantic.BaseModel):
        """替身版配置节基类。"""

        model_config = pydantic.ConfigDict(extra="forbid")

    class BaseConfig(pydantic.BaseModel):
        """替身版配置基类。"""

        model_config = pydantic.ConfigDict(extra="forbid")

    def config_section(name: str, **_kwargs: Any):
        """替身版配置节装饰器。

        Args:
            name: 节名。

        Returns:
            装饰器。
        """

        def decorator(cls):
            cls.__config_section_name__ = name
            return cls

        return decorator

    class _Component:
        """替身版组件基类：只保留框架会读的属性。"""

        name = ""
        description = ""
        weight = 0
        init_subscribe: list[Any] = []

        def __init__(self, plugin: Any = None) -> None:
            """初始化。

            Args:
                plugin: 所属插件。
            """
            self.plugin = plugin

    class BaseEventHandler(_Component):
        """替身版 EventHandler 基类。"""

    class BaseService(_Component):
        """替身版 Service 基类。"""

    class BaseTool(_Component):
        """替身版 Tool 基类。"""

    class BaseAction(_Component):
        """替身版 Action 基类。"""

    class BasePlugin:
        """替身版插件基类。"""

        def __init__(self) -> None:
            """初始化。"""
            self.config: Any = None

    def register_plugin(cls):
        """替身版注册装饰器，原样返回。

        Args:
            cls: 插件类。

        Returns:
            同一个类。
        """
        return cls

    base.SectionBase = SectionBase  # type: ignore[attr-defined]
    base.BaseConfig = BaseConfig  # type: ignore[attr-defined]
    base.config_section = config_section  # type: ignore[attr-defined]
    base.Field = pydantic.Field  # type: ignore[attr-defined]
    base.BaseEventHandler = BaseEventHandler  # type: ignore[attr-defined]
    base.BaseService = BaseService  # type: ignore[attr-defined]
    base.BaseTool = BaseTool  # type: ignore[attr-defined]
    base.BaseAction = BaseAction  # type: ignore[attr-defined]
    base.BasePlugin = BasePlugin  # type: ignore[attr-defined]
    base.register_plugin = register_plugin  # type: ignore[attr-defined]

    # --- 事件类型与决策 ---
    types_mod = _module("src.core.components.types")

    class EventType:
        """替身版事件类型常量。"""

        ON_MESSAGE_RECEIVED = "on_message_received"
        BEFORE_MESSAGE_RECEIVED = "before_message_received"

    types_mod.EventType = EventType  # type: ignore[attr-defined]

    event_mod = _module("src.kernel.event")

    class EventDecision:
        """替身版事件决策。"""

        PASS = "PASS"
        SUCCESS = "SUCCESS"
        STOP = "STOP"

    event_mod.EventDecision = EventDecision  # type: ignore[attr-defined]

    # --- 消息 ---
    message_mod = _module("src.core.models.message")
    message_mod.Message = FakeMessage  # type: ignore[attr-defined]

    # --- 任务管理器 ---
    concurrency = _module("src.kernel.concurrency")

    class _TaskManager:
        """替身版任务管理器：把协程记下来，由测试决定何时跑。"""

        def __init__(self) -> None:
            """初始化。"""
            self.created: list[tuple[Any, dict[str, Any]]] = []

        def create_task(self, coro: Any, **kwargs: Any) -> Any:
            """记录一个待跑协程。

            Args:
                coro: 协程对象。
                **kwargs: 框架参数。

            Returns:
                `None`。
            """
            self.created.append((coro, kwargs))
            return None

    _TASKS = _TaskManager()
    concurrency.get_task_manager = lambda: _TASKS  # type: ignore[attr-defined]
    concurrency._stub_tasks = _TASKS  # type: ignore[attr-defined]
