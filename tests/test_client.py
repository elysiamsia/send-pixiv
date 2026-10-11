"""pixiv 客户端的单元测试。

用 `httpx.MockTransport` 顶掉真实网络，专门验证那些容易写错、又只有联网才暴露的细节：

- OAuth 刷新时 `X-Client-Time` / `X-Client-Hash` 是否按约定拼出来
- 轮换到的新 refresh_token 有没有落盘（不落盘下一次刷新就 401）
- `include_r18` 到底有没有正确控制 `filter=for_ios`
- 401 之后会不会换令牌重试、404 会不会直接抛出
"""

from __future__ import annotations

import hashlib
import re
import tempfile
import unittest
from pathlib import Path

import httpx

from plugin_loader import load_package

load_package()

from send_pixiv_under_test.core.client import (  # noqa: E402
    APP_HEADERS,
    CLIENT_HASH_SALT,
    CLIENT_SECRET,
    PixivClient,
    PixivNotFoundError,
    _client_time,
)
from send_pixiv_under_test.core.token import TokenStore  # noqa: E402

ILLUSTR = {
    "id": "100",
    "title": "标题",
    "type": "illust",
    "user": {"id": "9", "name": "画师"},
    "x_restrict": 0,
    "sanity_level": 2,
    "page_count": 1,
    "image_urls": {"large": "https://i.pximg.net/l.jpg"},
    "meta_single_page": {"original_image_url": "https://i.pximg.net/o.jpg"},
}


class _Recorder:
    """记录 MockTransport 收到的请求，供断言使用。"""

    def __init__(self) -> None:
        self.oauth: list[httpx.Request] = []
        self.api: list[httpx.Request] = []
        self.binary: list[httpx.Request] = []

    def handler_factory(self, *, api_status: int = 200, oauth_rotation: str = "R2"):
        """构造一个可用的 transport handler。

        Args:
            api_status: App API 返回的状态码。
            oauth_rotation: 认证端点下发的新 refresh_token。

        Returns:
            `(request) -> httpx.Response`。
        """

        def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            if host == "oauth.secure.pixiv.net":
                self.oauth.append(request)
                return httpx.Response(
                    200,
                    json={
                        "access_token": "AT1",
                        "expires_in": 3600,
                        "refresh_token": oauth_rotation,
                        "token_type": "bearer",
                    },
                )
            if host == "i.pximg.net":
                self.binary.append(request)
                return httpx.Response(200, content=b"\xff\xd8\xff\xe0jpegbytes")
            self.api.append(request)
            if api_status != 200:
                return httpx.Response(api_status, json={"error": "boom"})
            return httpx.Response(200, json={"illust": ILLUSTR, "illusts": [ILLUSTR]})

        return handler

    def attach(self, client: PixivClient, **kwargs) -> None:
        """把 MockTransport 装到客户端上。

        Args:
            client: 待接管的客户端。
            **kwargs: 透传给 `handler_factory`。
        """
        client._client = httpx.AsyncClient(  # noqa: SLF001 - 测试需要替换内部客户端
            transport=httpx.MockTransport(self.handler_factory(**kwargs)),
            headers={"User-Agent": client.user_agent},
        )


class ClientTimeTest(unittest.TestCase):
    """`X-Client-Time` 的格式。"""

    def test_matches_javascript_to_iso_string(self) -> None:
        self.assertRegex(_client_time(), re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"))


class PixivClientTest(unittest.IsolatedAsyncioTestCase):
    """客户端行为用例。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.store = TokenStore(self.tmp / "token.json")
        self.recorder = _Recorder()

    async def _make_client(self, **kwargs) -> PixivClient:
        """构造一个接了 MockTransport 的客户端。

        Args:
            **kwargs: 覆盖客户端的默认参数。

        Returns:
            客户端实例。
        """
        options = {
            "refresh_token": "R1",
            "token_store": self.store,
            "min_interval": 0,
        }
        options.update(kwargs)
        client = PixivClient(**options)  # type: ignore[arg-type]
        self.recorder.attach(client)
        self.addAsyncCleanup(client.close)
        return client

    async def test_refresh_headers_and_rotation(self) -> None:
        "认证请求要带齐头部，并把轮换后的 refresh_token 落盘。"
        client = await self._make_client()
        illust = await client.illust_detail(100)

        self.assertEqual(illust.id, 100)
        self.assertEqual(len(self.recorder.oauth), 1)

        oauth = self.recorder.oauth[0]
        body = dict(httpx.QueryParams(oauth.content.decode()))
        self.assertEqual(body["grant_type"], "refresh_token")
        self.assertEqual(body["refresh_token"], "R1")
        self.assertTrue(body["client_id"])
        self.assertEqual(body["client_secret"], CLIENT_SECRET)
        # X-Client-Hash = md5(X-Client-Time + 固定盐)
        expected_hash = hashlib.md5(
            (oauth.headers["X-Client-Time"] + CLIENT_HASH_SALT).encode("utf-8")
        ).hexdigest()
        self.assertEqual(oauth.headers["X-Client-Hash"], expected_hash)

        # 轮换后的 token 必须落盘，否则第二次刷新会用失效的旧 token
        self.assertEqual(self.store.refresh_token(), "R2")
        self.assertEqual(self.store.access_token(), "AT1")

    async def test_app_api_headers_and_token_reuse(self) -> None:
        "App API 请求要带应用头与 Bearer 令牌，且第二次调用复用缓存令牌。"
        client = await self._make_client()
        await client.illust_detail(100)
        await client.illust_detail(101)

        self.assertEqual(len(self.recorder.oauth), 1, "access_token 未过期时不该重复刷新")
        self.assertEqual(len(self.recorder.api), 2)

        request = self.recorder.api[0]
        self.assertEqual(request.headers["Authorization"], "Bearer AT1")
        for key, value in APP_HEADERS.items():
            self.assertEqual(request.headers[key], value)
        self.assertEqual(request.url.params["illust_id"], "100")

    async def test_search_filter_flag(self) -> None:
        "`include_r18` 决定要不要带 filter=for_ios。"
        client = await self._make_client()
        await client.search_illust("猫", limit=2, include_r18=False)
        await client.search_illust("猫", limit=2, include_r18=True)

        safe = self.recorder.api[0].url.params
        r18 = self.recorder.api[1].url.params
        self.assertEqual(safe["filter"], "for_ios")
        self.assertNotIn("filter", r18)
        self.assertIn("for_ios", str(safe))
        self.assertEqual(safe["word"], "猫")
        self.assertEqual(safe["search_target"], "partial_match_for_tags")

    async def test_ranking_and_user_params(self) -> None:
        "排行榜与作者作品端点的参数。"
        client = await self._make_client()
        await client.illust_ranking("week", date="2024-05-01", limit=3, include_r18=False)
        await client.user_illusts(42, limit=3, include_r18=True)

        ranking = self.recorder.api[0].url
        user = self.recorder.api[1].url
        self.assertEqual(ranking.path, "/v1/illust/ranking")
        self.assertEqual(ranking.params["mode"], "week")
        self.assertEqual(ranking.params["date"], "2024-05-01")
        self.assertEqual(user.path, "/v1/user/illusts")
        self.assertEqual(user.params["user_id"], "42")

    async def test_404_raises_not_found(self) -> None:
        "作品不存在时抛 PixivNotFoundError。"
        client = await self._make_client()
        self.recorder.attach(client, api_status=404)
        with self.assertRaises(PixivNotFoundError):
            await client.illust_detail(1)

    async def test_401_triggers_refresh_retry(self) -> None:
        "401 之后要换新令牌重试，而不是直接失败。"

        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "oauth.secure.pixiv.net":
                self.recorder.oauth.append(request)
                return httpx.Response(
                    200,
                    json={"access_token": "AT-new", "expires_in": 3600, "refresh_token": "R3"},
                )
            self.recorder.api.append(request)
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(401, json={"error": "invalid token"})
            return httpx.Response(200, json={"illust": ILLUSTR})

        client = await self._make_client()
        client._client = httpx.AsyncClient(  # noqa: SLF001
            transport=httpx.MockTransport(handler),
            headers={"User-Agent": client.user_agent},
        )
        illust = await client.illust_detail(100)

        self.assertEqual(illust.id, 100)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(len(self.recorder.oauth), 2, "第一次刷新 + 401 后强制刷新")
        self.assertEqual(self.recorder.api[-1].headers["Authorization"], "Bearer AT-new")

    async def test_download_image_writes_file_with_referer(self) -> None:
        "下载图片要带 Referer，并原子落盘。"
        client = await self._make_client()
        target = self.tmp / "cache" / "100" / "0.jpg"
        path = await client.download_image("https://i.pximg.net/l.jpg", target)

        self.assertTrue(path.exists())
        self.assertEqual(path.read_bytes(), b"\xff\xd8\xff\xe0jpegbytes")
        self.assertEqual(self.recorder.binary[0].headers["Referer"], "https://app-api.pixiv.net/")
        self.assertFalse(list(target.parent.glob("*.part")), "不应残留临时文件")

    async def test_no_credential_raises_auth_error(self) -> None:
        "既没有 refresh_token 也没有 access_token 时要报可读的错。"
        from send_pixiv_under_test.core.client import PixivAuthError

        client = PixivClient(refresh_token="", token_store=TokenStore(self.tmp / "none.json"))
        self.recorder.attach(client)
        self.addAsyncCleanup(client.close)
        with self.assertRaises(PixivAuthError):
            await client.illust_detail(1)

    async def test_token_store_is_preferred_when_config_empty(self) -> None:
        "配置里没填 refresh_token 时，应回退到落盘的令牌。"
        self.store.save(refresh_token="R-stored", access_token="", expires_at=0)
        client = await self._make_client(refresh_token="")
        self.assertTrue(client.has_credential)
        await client.illust_detail(100)
        body = dict(httpx.QueryParams(self.recorder.oauth[0].content.decode()))
        self.assertEqual(body["refresh_token"], "R-stored")


if __name__ == "__main__":
    unittest.main()
