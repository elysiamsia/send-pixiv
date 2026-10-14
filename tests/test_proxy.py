"""代理探测的单元测试。

这块的动机很实在：Windows 用户开代理软件的「系统代理」开关写的是注册表，
而 httpx 只认环境变量——不探测的话，他们明明挂着代理也会连不上。
"""

from __future__ import annotations

import os
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from plugin_loader import load_package  # noqa: E402

load_package()

import send_pixiv_under_test.core.proxy as proxy_module  # noqa: E402
from send_pixiv_under_test.core.proxy import (  # noqa: E402
    _ENV_KEYS,
    detect_system_proxy,
    is_supported,
    normalize,
)

_NO_ENV = {key: "" for key in _ENV_KEYS}


class NormalizeTest(unittest.TestCase):
    """`normalize` 的用例。"""

    def test_bare_host_port_gets_scheme(self) -> None:
        "用户手填时经常漏协议头。"
        self.assertEqual(normalize("127.0.0.1:7890"), "http://127.0.0.1:7890")

    def test_keeps_existing_scheme(self) -> None:
        self.assertEqual(normalize("http://127.0.0.1:7890"), "http://127.0.0.1:7890")
        self.assertEqual(normalize("https://proxy.local:8443"), "https://proxy.local:8443")

    def test_registry_multi_protocol_prefers_https(self) -> None:
        "注册表里常写成 http=...;https=... 的形式。"
        self.assertEqual(
            normalize("http=127.0.0.1:7890;https=127.0.0.1:7891"),
            "http://127.0.0.1:7891",
        )

    def test_registry_single_segment(self) -> None:
        self.assertEqual(normalize("http=127.0.0.1:7890"), "http://127.0.0.1:7890")

    def test_socks_kept_for_support_check(self) -> None:
        self.assertEqual(normalize("socks5://127.0.0.1:7890"), "socks5://127.0.0.1:7890")

    def test_empty(self) -> None:
        self.assertEqual(normalize(""), "")
        self.assertEqual(normalize("   "), "")

    def test_strips_quotes(self) -> None:
        self.assertEqual(normalize('"127.0.0.1:7890"'), "http://127.0.0.1:7890")


class IsSupportedTest(unittest.TestCase):
    """`is_supported` 的用例（httpx 不带 SOCKS 支持）。"""

    def test_http_family(self) -> None:
        self.assertTrue(is_supported("http://127.0.0.1:7890"))
        self.assertTrue(is_supported("https://proxy.local:8443"))

    def test_socks_rejected(self) -> None:
        for value in ("socks5://127.0.0.1:7890", "socks4://127.0.0.1:1080", "SOCKS5://x:1"):
            self.assertFalse(is_supported(value), value)

    def test_empty_is_supported(self) -> None:
        self.assertTrue(is_supported(""))


class DetectTest(unittest.TestCase):
    """`detect_system_proxy` 的用例。"""

    def test_env_var_wins(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"HTTPS_PROXY": "127.0.0.1:7890"}, clear=False):
            detected, source = detect_system_proxy()
        self.assertEqual(detected, "http://127.0.0.1:7890")
        self.assertIn("HTTPS_PROXY", source)

    def test_registry_used_when_no_env(self) -> None:
        with unittest.mock.patch.dict(os.environ, _NO_ENV, clear=False):
            with unittest.mock.patch.object(
                proxy_module, "_windows_system_proxy", return_value="http://127.0.0.1:7890"
            ):
                detected, source = detect_system_proxy()
        self.assertEqual(detected, "http://127.0.0.1:7890")
        self.assertEqual(source, "Windows 系统代理")

    def test_nothing_found(self) -> None:
        with unittest.mock.patch.dict(os.environ, _NO_ENV, clear=False):
            with unittest.mock.patch.object(proxy_module, "_windows_system_proxy", return_value=""):
                self.assertEqual(detect_system_proxy(), ("", ""))

    def test_non_windows_skips_registry(self) -> None:
        "非 Windows 上不该去碰 winreg。"
        with unittest.mock.patch.object(os, "name", "posix"):
            self.assertEqual(proxy_module._windows_system_proxy(), "")


if __name__ == "__main__":
    unittest.main()
