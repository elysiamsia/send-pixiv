"""`resolve_own_plugin` 的单元测试。

这个函数存在的理由就是一次真实事故：插件加载失败后，框架会把
"当前 chatter 的插件"兜底注入给 Tool，于是 `self.plugin` 变成了
`NeoChatterPlugin`，报出完全看不出根因的 AttributeError。
所以这里锁死"拿到不属于本插件的实例时必须返回 None"。
"""

from __future__ import annotations

import unittest

from plugin_loader import load_package

load_package()

from send_pixiv_under_test.core.runtime import PLUGIN_NAME, resolve_own_plugin  # noqa: E402


class _FakePlugin:
    """冒充一个插件实例。"""

    def __init__(self, name: str) -> None:
        self.plugin_name = name


class _FakeComponent:
    """冒充一个组件实例。"""

    def __init__(self, plugin: object | None) -> None:
        if plugin is not None:
            self.plugin = plugin


class ResolveOwnPluginTest(unittest.TestCase):
    """`resolve_own_plugin` 的用例。"""

    def test_plugin_name_constant(self) -> None:
        self.assertEqual(PLUGIN_NAME, "send-pixiv")

    def test_accepts_matching_injected_plugin(self) -> None:
        "框架注入的就是本插件时，正常返回。"
        component = _FakeComponent(_FakePlugin(PLUGIN_NAME))
        self.assertIs(resolve_own_plugin(component).plugin_name, PLUGIN_NAME)

    def test_rejects_foreign_injected_plugin(self) -> None:
        "关键用例：注入的是别人的插件时，必须返回 None 而不是硬用。"
        component = _FakeComponent(_FakePlugin("neo_default_chatter"))
        self.assertIsNone(resolve_own_plugin(component))

    def test_handles_missing_plugin_attribute(self) -> None:
        self.assertIsNone(resolve_own_plugin(_FakeComponent(None)))

    def test_handles_none_component(self) -> None:
        self.assertIsNone(resolve_own_plugin(None))

    def test_rejects_object_without_plugin_name(self) -> None:
        class _Nameless:
            pass

        self.assertIsNone(resolve_own_plugin(_FakeComponent(_Nameless())))


if __name__ == "__main__":
    unittest.main()
