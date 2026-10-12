"""指令 handler 的流程测试（用框架替身驱动）。

这是**回归测试**：`Debouncer.hit()` 返回 True 表示"应当跳过"，之前写成了

    if not plugin.debouncer.hit(...): return

语义正好反了——每条**新**指令都被静默丢弃，只有 30 秒内重复发的才会执行。
用户看到的现象就是"发了 #r18 没反应，还是不行"。

这类 bug 之前测不到，是因为组件模块导入时就依赖框架。`framework_stub`
注入了一层替身，于是 handler 的流程可以被真正驱动。
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import framework_stub  # noqa: E402

framework_stub.install()

from plugin_loader import load_package  # noqa: E402

load_package()

from send_pixiv_under_test.core.policy import Debouncer, Policy  # noqa: E402
from send_pixiv_under_test.config import SendPixivConfig  # noqa: E402

import send_pixiv_under_test.handlers.pixiv_command_handler as handler_module  # noqa: E402
from send_pixiv_under_test.handlers.pixiv_command_handler import PixivCommandWatcher  # noqa: E402

GROUP = "123456789"


class FakePlugin:
    """够用的插件替身：只实现 handler 会碰的东西。"""

    plugin_name = "send-pixiv"

    def __init__(
        self,
        *,
        rating: str = "r18",
        config: SendPixivConfig | None = None,
        keep_coroutines: bool = False,
    ) -> None:
        """初始化。

        Args:
            rating: `rating_mode_for` 的返回值。
            config: 插件配置，缺省用真实默认配置。
            keep_coroutines: 为真时不关闭协程，留给测试自己 await。
        """
        self.keep_coroutines = keep_coroutines
        self.cfg = config or SendPixivConfig()
        self.policy = Policy()
        self.debouncer = Debouncer(ttl_seconds=30.0)
        self._rating = rating
        self.spawned: list[str] = []
        self.coroutines: list[object] = []

    def rating_mode_for(self, stream_id: str, platform: str = "") -> str:
        """返回预设分级。"""
        return self._rating

    def spawn(self, coro, name: str = "") -> None:
        """记录后台任务，并把协程关掉（避免 never awaited 警告）。"""
        self.spawned.append(name)
        self.coroutines.append(coro)
        if not self.keep_coroutines:
            close = getattr(coro, "close", None)
            if close is not None:
                close()

    def mark_sent(self, *_args) -> None:
        """无需实现。"""

    @property
    def delivered(self) -> list[str]:
        """真正进入"搜索发送"的那几次。"""
        return [n for n in self.spawned if "reply" not in n]

    @property
    def replies(self) -> list[str]:
        """回执（拒绝 / 用法提示）的那几次。"""
        return [n for n in self.spawned if "reply" in n]


def dispatch(handler: PixivCommandWatcher, plugin: FakePlugin, text: str) -> None:
    """驱动一次 handler。

    Args:
        handler: handler 实例。
        plugin: 插件替身。
        text: 消息文本。
    """
    handler_module.resolve_own_plugin = lambda _component: plugin  # type: ignore[assignment]
    handler._dispatch({"message": framework_stub.FakeMessage(text, stream_id="a" * 64)})


class CommandHandlerFlowTest(unittest.TestCase):
    """指令 handler 的流程。"""

    def setUp(self) -> None:
        """每个用例一套干净的替身。"""
        self.plugin = FakePlugin()
        self.handler = PixivCommandWatcher()

    def test_first_command_is_handled(self) -> None:
        "回归点：**第一次**发的指令必须被处理（写反时这条会失败）。"
        dispatch(self.handler, self.plugin, "#r18 猫")
        self.assertEqual(len(self.plugin.delivered), 1, f"没有进入发送流程: {self.plugin.spawned}")

    def test_repeat_within_window_is_ignored(self) -> None:
        "防抖仍然要生效：窗口内重复发同一条不再触发。"
        dispatch(self.handler, self.plugin, "#r18 猫")
        dispatch(self.handler, self.plugin, "#r18 猫")
        self.assertEqual(len(self.plugin.delivered), 1)

    def test_different_keyword_is_handled(self) -> None:
        "换个关键词不算重复。"
        dispatch(self.handler, self.plugin, "#r18 猫")
        dispatch(self.handler, self.plugin, "#r18 狗")
        self.assertEqual(len(self.plugin.delivered), 2)

    def test_normal_command(self) -> None:
        dispatch(self.handler, self.plugin, "#pixiv 风景")
        self.assertEqual(len(self.plugin.delivered), 1)

    def test_plain_text_ignored(self) -> None:
        dispatch(self.handler, self.plugin, "今天天气不错")
        self.assertEqual(self.plugin.spawned, [])

    def test_prefix_only_gets_usage(self) -> None:
        "只发 #r18 时回一句用法，而不是去搜空关键词。"
        dispatch(self.handler, self.plugin, "#r18")
        self.assertEqual(self.plugin.delivered, [])
        self.assertEqual(len(self.plugin.replies), 1)

    def test_r18_refused_when_stream_is_safe(self) -> None:
        "会话上限是 safe 时明确拒绝，不静默降级。"
        self.plugin._rating = "safe"
        dispatch(self.handler, self.plugin, "#r18 猫")
        self.assertEqual(self.plugin.delivered, [])
        self.assertEqual(len(self.plugin.replies), 1)

    def test_r18_allowed_when_stream_is_all(self) -> None:
        self.plugin._rating = "all"
        dispatch(self.handler, self.plugin, "#r18 猫")
        self.assertEqual(len(self.plugin.delivered), 1)

    def test_disabled_trigger_does_nothing(self) -> None:
        self.plugin.cfg.trigger.enabled = False
        dispatch(self.handler, self.plugin, "#r18 猫")
        self.assertEqual(self.plugin.spawned, [])

    def test_policy_rejection_replies(self) -> None:
        "白名单外的会话会被拒，并且回一句原因。"
        self.plugin.policy = Policy(allowed_streams=["999999999"])
        dispatch(self.handler, self.plugin, "#r18 猫")
        self.assertEqual(self.plugin.delivered, [])
        self.assertEqual(len(self.plugin.replies), 1)

    def test_reply_clears_debounce_so_user_can_retry(self) -> None:
        "被拒后解除防抖记录——否则用户重发也会被静默吞掉。"
        self.plugin._rating = "safe"
        dispatch(self.handler, self.plugin, "#r18 猫")
        dispatch(self.handler, self.plugin, "#r18 猫")
        self.assertEqual(len(self.plugin.replies), 2)

    def test_execute_returns_pass(self) -> None:
        "handler 必须始终 PASS，不能拦住消息。"
        result = asyncio.run(self.handler.execute("on_message_received", {"message": framework_stub.FakeMessage("#r18 猫")}))
        self.assertEqual(result[0], "PASS")


class ReplyDeliveryTest(unittest.TestCase):
    """回执与发送的落地（真正 await 后台协程）。"""

    def setUp(self) -> None:
        """准备。"""
        framework_stub.clear_sent()
        self.plugin = FakePlugin(keep_coroutines=True)
        self.handler = PixivCommandWatcher()
        handler_module.resolve_own_plugin = lambda _c: self.plugin  # type: ignore[assignment]

    def test_usage_reply_is_sent(self) -> None:
        dispatch(self.handler, self.plugin, "#r18")
        coro = self.plugin.coroutines[0]
        asyncio.run(coro)  # type: ignore[arg-type]
        sent = framework_stub.sent_messages()
        self.assertEqual(len(sent), 1)
        self.assertIn("用法", sent[0].payload)
        self.assertFalse(sent[0].payload.startswith("#r18"), "回执不能以指令前缀开头，否则会自己触发自己")


if __name__ == "__main__":
    unittest.main()
