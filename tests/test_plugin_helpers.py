"""`plugin.py` 里几个小工具的单元测试。

这些函数看着不起眼，但直接决定用户看到什么日志、以及插件会不会
"自己接上一个用户没打算用的代理"。
"""

from __future__ import annotations

import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import framework_stub  # noqa: E402

framework_stub.install()

from plugin_loader import load_package  # noqa: E402

load_package()

import send_pixiv_under_test.plugin as plugin_module  # noqa: E402
from send_pixiv_under_test.config import SendPixivConfig  # noqa: E402
from send_pixiv_under_test.plugin import (  # noqa: E402
    PROXY_SOURCE_CONFIG,
    SendPixivPlugin,
    proxy_label,
)


class ProxyLabelTest(unittest.TestCase):
    """日志措辞：是用户配的，还是插件探测的。"""

    def test_configured(self) -> None:
        self.assertEqual(proxy_label(PROXY_SOURCE_CONFIG), "配置的代理")

    def test_detected_from_registry(self) -> None:
        self.assertEqual(proxy_label("Windows 系统代理"), "自动探测到的代理")

    def test_detected_from_env(self) -> None:
        self.assertEqual(proxy_label("环境变量 HTTPS_PROXY"), "自动探测到的代理")

    def test_empty_source_treated_as_detected(self) -> None:
        """来源不明时不该说成「用户配置的」，那会让人以为自己配过。"""
        self.assertEqual(proxy_label(""), "自动探测到的代理")


class ResolveProxyTest(unittest.TestCase):
    """代理决策：配置优先，留空才探测。"""

    def _plugin(self, proxy: str = "") -> SendPixivPlugin:
        """造一个带指定代理配置的插件实例。

        Args:
            proxy: `pixiv.proxy` 的值。

        Returns:
            插件实例。
        """
        cfg = SendPixivConfig()
        cfg.pixiv.proxy = proxy
        return SendPixivPlugin(config=cfg)

    def test_configured_wins_without_detection(self) -> None:
        """配置里写了就不该再去探测，避免踩掉用户的明确选择。"""
        plugin = self._plugin("http://127.0.0.1:7890")
        with unittest.mock.patch.object(plugin_module, "detect_system_proxy") as spy:
            proxy, source = plugin._resolve_proxy(plugin.cfg)
        spy.assert_not_called()
        self.assertEqual(proxy, "http://127.0.0.1:7890")
        self.assertEqual(source, PROXY_SOURCE_CONFIG)

    def test_detects_when_config_empty(self) -> None:
        plugin = self._plugin("")
        with unittest.mock.patch.object(
            plugin_module,
            "detect_system_proxy",
            return_value=("http://127.0.0.1:7890", "Windows 系统代理"),
        ):
            proxy, source = plugin._resolve_proxy(plugin.cfg)
        self.assertEqual(proxy, "http://127.0.0.1:7890")
        self.assertEqual(source, "Windows 系统代理")

    def test_nothing_detected_means_direct(self) -> None:
        plugin = self._plugin("")
        with unittest.mock.patch.object(plugin_module, "detect_system_proxy", return_value=("", "")):
            self.assertEqual(plugin._resolve_proxy(plugin.cfg), ("", ""))

    def test_detected_socks_is_refused(self) -> None:
        """httpx 不支持 socks——宁可不接，也不要拿着必错的地址去连。"""
        plugin = self._plugin("")
        with unittest.mock.patch.object(
            plugin_module,
            "detect_system_proxy",
            return_value=("socks5://127.0.0.1:7890", "Windows 系统代理"),
        ):
            self.assertEqual(plugin._resolve_proxy(plugin.cfg), ("", ""))

    def test_effective_proxy_property(self) -> None:
        """话术层靠它判断「去配代理」还是「去查代理」。"""
        plugin = self._plugin("http://127.0.0.1:7890")
        self.assertEqual(plugin.effective_proxy, "http://127.0.0.1:7890")


if __name__ == "__main__":
    unittest.main()
