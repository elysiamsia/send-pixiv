"""`#r18 关键词` 指令组件的流程测试。

用 `framework_stub` 驱动，覆盖"用户发了指令之后到底发生了什么"。

**为什么是 Command 而不是 EventHandler**：框架的 distributor 在
`ON_MESSAGE_RECEIVED` 里对已注册命令会直接 `execute_command` 然后返回
`EventDecision.SUCCESS`，**不把消息注入聊天流**。所以模型看不到指令消息，
也就不会在插件发完图之后再插一句"这类内容我不查哦"。
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

from send_pixiv_under_test.commands.pixiv_command import (  # noqa: E402
    PixivR18Command,
    PixivSearchCommand,
)
from send_pixiv_under_test.config import SendPixivConfig  # noqa: E402
from send_pixiv_under_test.core.policy import Policy  # noqa: E402

import send_pixiv_under_test.commands.pixiv_command as command_module  # noqa: E402

STREAM = "a" * 64


class FakePlugin:
    """够用的插件替身。"""

    plugin_name = "send-pixiv"

    def __init__(self, *, rating: str = "r18", config: SendPixivConfig | None = None) -> None:
        """初始化。

        Args:
            rating: `rating_mode_for` 的返回值。
            config: 插件配置。
        """
        self.cfg = config or SendPixivConfig()
        self.policy = Policy()
        self._rating = rating
        self.spawned: list[str] = []
        self.coroutines: list[object] = []

    def rating_mode_for(self, stream_id: str, platform: str = "") -> str:
        """返回预设分级。"""
        return self._rating

    #: 为真时保留协程（留给测试自己 await），否则立刻关掉避免 never awaited 警告
    keep_coroutines: bool = False

    def spawn(self, coro, name: str = "") -> None:
        """记录后台任务。"""
        self.spawned.append(name)
        self.coroutines.append(coro)
        if not self.keep_coroutines:
            close = getattr(coro, "close", None)
            if close is not None:
                close()

    def mark_sent(self, *_args) -> None:
        """无需实现。"""


def run_command(cls: type, plugin: FakePlugin, keyword: str) -> tuple[bool, str]:
    """跑一条指令。

    Args:
        cls: 命令类。
        plugin: 插件替身。
        keyword: name 之后的文本。

    Returns:
        `(是否受理, 说明)`。
    """
    command_module.resolve_own_plugin = lambda _c: plugin  # type: ignore[assignment]
    command = cls(plugin=plugin, stream_id=STREAM, message=framework_stub.FakeMessage(""))
    return asyncio.run(command.execute(keyword))


class R18CommandTest(unittest.TestCase):
    """`#r18` 的行为。"""

    def test_keyword_triggers_search(self) -> None:
        plugin = FakePlugin()
        ok, _ = run_command(PixivR18Command, plugin, "猫")
        self.assertTrue(ok)
        self.assertEqual(len(plugin.spawned), 1)

    def test_whole_rest_is_the_keyword(self) -> None:
        "多词关键词要整段保留——框架默认路由只会传第一个词，所以重写了 execute。"

        class SpyCommand(PixivR18Command):
            """把 `_deliver` 换成间谍，看真正传进去的关键词。"""

            captured: list[tuple[str, str]] = []

            async def _deliver(self, plugin, stream_id, keyword, mode):  # type: ignore[override]
                """只记录，不真发。"""
                SpyCommand.captured.append((keyword, mode))

        SpyCommand.captured = []
        plugin = FakePlugin()
        plugin.keep_coroutines = True
        ok, _ = run_command(SpyCommand, plugin, "猫 少女")
        self.assertTrue(ok)
        asyncio.run(plugin.coroutines[0])  # type: ignore[arg-type]
        self.assertEqual(SpyCommand.captured, [("猫 少女", "r18")])

    def test_keyword_is_passed_to_delivery(self) -> None:
        "单关键词也要原样传下去。"

        class SpyCommand(PixivR18Command):
            """记录关键词。"""

            captured: list[tuple[str, str]] = []

            async def _deliver(self, plugin, stream_id, keyword, mode):  # type: ignore[override]
                """只记录。"""
                SpyCommand.captured.append((keyword, mode))

        SpyCommand.captured = []
        plugin = FakePlugin()
        plugin.keep_coroutines = True
        run_command(SpyCommand, plugin, "洛茜")
        asyncio.run(plugin.coroutines[0])  # type: ignore[arg-type]
        self.assertEqual(SpyCommand.captured, [("洛茜", "r18")])

    def test_empty_keyword_replies_usage(self) -> None:
        framework_stub.clear_sent()
        plugin = FakePlugin()
        ok, reason = run_command(PixivR18Command, plugin, "")
        self.assertFalse(ok)
        self.assertEqual(plugin.spawned, [])
        self.assertEqual(len(framework_stub.sent_messages()), 1)
        self.assertIn("用法", framework_stub.sent_messages()[0].payload)

    def test_refused_when_stream_is_safe(self) -> None:
        "会话上限 safe 时明确拒绝，不静默降级成全年龄。"
        framework_stub.clear_sent()
        plugin = FakePlugin(rating="safe")
        ok, reason = run_command(PixivR18Command, plugin, "猫")
        self.assertFalse(ok)
        self.assertEqual(plugin.spawned, [])
        self.assertIn("safe", reason)
        self.assertEqual(len(framework_stub.sent_messages()), 1)

    def test_allowed_when_stream_is_all(self) -> None:
        plugin = FakePlugin(rating="all")
        ok, _ = run_command(PixivR18Command, plugin, "猫")
        self.assertTrue(ok)

    def test_policy_rejection_replies(self) -> None:
        framework_stub.clear_sent()
        plugin = FakePlugin()
        plugin.policy = Policy(allowed_streams=["999999999"])
        ok, _ = run_command(PixivR18Command, plugin, "猫")
        self.assertFalse(ok)
        self.assertEqual(plugin.spawned, [])
        self.assertEqual(len(framework_stub.sent_messages()), 1)

    def test_disabled_does_nothing(self) -> None:
        framework_stub.clear_sent()
        plugin = FakePlugin()
        plugin.cfg.trigger.enabled = False
        ok, _ = run_command(PixivR18Command, plugin, "猫")
        self.assertFalse(ok)
        self.assertEqual(plugin.spawned, [])
        self.assertEqual(framework_stub.sent_messages(), [])

    def test_name_is_r18(self) -> None:
        "命令名决定它响应哪个词（#r18 / /r18）。"
        self.assertEqual(PixivR18Command.name, "r18")
        self.assertEqual(PixivSearchCommand.name, "pixiv")


class SearchCommandTest(unittest.TestCase):
    """`#pixiv` 的行为。"""

    def test_uses_stream_default_rating(self) -> None:
        "普通指令不要求分级，用会话默认——safe 会话也放行。"
        plugin = FakePlugin(rating="safe")
        ok, _ = run_command(PixivSearchCommand, plugin, "风景")
        self.assertTrue(ok)

    def test_allows_r18_stream_too(self) -> None:
        plugin = FakePlugin(rating="r18")
        ok, _ = run_command(PixivSearchCommand, plugin, "风景")
        self.assertTrue(ok)


class UsageSafetyTest(unittest.TestCase):
    """回执不能自己触发自己。"""

    def test_usage_does_not_start_with_prefix(self) -> None:
        "机器人自己发的消息也会走一遍事件链，文案以 # 开头就会形成回环。"
        from send_pixiv_under_test.core.commands import USAGE  # noqa: PLC0415

        self.assertFalse(USAGE.lstrip().startswith("#"))
        self.assertFalse(USAGE.lstrip().startswith("/"))


if __name__ == "__main__":
    unittest.main()
