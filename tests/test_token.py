"""token 落盘与取值优先级的单元测试。

这一组盯的是一个很容易踩、且踩了之后表现很迷惑的坑：
pixiv 刷新时会**轮换** refresh_token，轮换结果落在 token.json，
而配置文件里那个立刻变成废票。如果代码无脑信配置，第二次启动就 401。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from plugin_loader import load_package

load_package()

from send_pixiv_under_test.core.token import TokenStore  # noqa: E402


class TokenStoreTest(unittest.TestCase):
    """`TokenStore` 的用例。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.store = TokenStore(self.tmp / "token.json")

    def test_missing_file_is_empty(self) -> None:
        self.assertEqual(self.store.refresh_token(), "")
        self.assertEqual(self.store.access_token(), "")
        self.assertEqual(self.store.load(), {})

    def test_broken_file_does_not_crash(self) -> None:
        self.store.path.write_text("{ 这不是 json", encoding="utf-8")
        self.assertEqual(self.store.load(), {})

    def test_access_token_expiry(self) -> None:
        self.store.save(access_token="A", expires_at=0)
        self.assertEqual(self.store.access_token(), "", "已过期的 access_token 不该被使用")
        self.store.save(access_token="A", expires_at=__import__("time").time() + 3600)
        self.assertEqual(self.store.access_token(), "A")


class ResolveRefreshTokenTest(unittest.TestCase):
    """配置值与落盘值的取舍。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.store = TokenStore(self.tmp / "token.json")

    def test_config_only(self) -> None:
        self.assertEqual(self.store.resolve_refresh_token("FROM_CONFIG"), "FROM_CONFIG")

    def test_store_only(self) -> None:
        self.store.save(refresh_token="FROM_STORE")
        self.assertEqual(self.store.resolve_refresh_token(""), "FROM_STORE")

    def test_rotated_token_wins_over_stale_config(self) -> None:
        "配置没动过，但 token 轮换了 → 必须用轮换后的。"
        self.store.save(refresh_token="ROTATED", config_refresh_token="ORIGINAL")
        self.assertEqual(self.store.resolve_refresh_token("ORIGINAL"), "ROTATED")

    def test_changed_config_wins_over_store(self) -> None:
        "用户改了配置（换账号）→ 以配置为准。"
        self.store.save(refresh_token="OLD_ACCOUNT", config_refresh_token="ORIGINAL")
        self.assertEqual(self.store.resolve_refresh_token("NEW_ACCOUNT"), "NEW_ACCOUNT")

    def test_config_wins_when_store_has_no_record_of_it(self) -> None:
        "老版本留下的 token.json 没有 config_refresh_token 记录，也不该把配置顶掉。"
        self.store.save(refresh_token="LEGACY")
        self.assertEqual(self.store.resolve_refresh_token("FROM_CONFIG"), "FROM_CONFIG")

    def test_empty_everywhere(self) -> None:
        self.assertEqual(self.store.resolve_refresh_token(""), "")


class RotationIntegrationTest(unittest.IsolatedAsyncioTestCase):
    """走一遍"轮换 → 重启 → 继续可用"。"""

    async def test_survives_restart_after_rotation(self) -> None:
        import httpx

        from send_pixiv_under_test.core.client import PixivClient

        tmp = Path(tempfile.mkdtemp())
        store = TokenStore(tmp / "token.json")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "oauth.secure.pixiv.net":
                body = dict(httpx.QueryParams(request.content.decode()))
                # 服务端认 ROTATED 和 ORIGINAL 都行，但会一直轮换
                self.assertIn(body["refresh_token"], ("ORIGINAL", "ROTATED-2"))
                return httpx.Response(
                    200,
                    json={"access_token": "AT", "expires_in": 3600, "refresh_token": "ROTATED-2"},
                )
            return httpx.Response(200, json={"illust": {"id": "1"}})

        # 第一次：配置里是 ORIGINAL，刷新后落盘 ROTATED-2
        first = PixivClient(refresh_token="ORIGINAL", token_store=store, min_interval=0)
        first._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # noqa: SLF001
        await first.illust_detail(1)
        await first.close()
        self.assertEqual(store.refresh_token(), "ROTATED-2")

        # "重启"：配置还是老的 ORIGINAL，但必须用上轮换后的 ROTATED-2
        second = PixivClient(refresh_token="ORIGINAL", token_store=store, min_interval=0)
        second._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # noqa: SLF001
        await second.illust_detail(1)
        await second.close()


if __name__ == "__main__":
    unittest.main()
