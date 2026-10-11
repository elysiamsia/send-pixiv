#!/usr/bin/env python3
"""pixiv OAuth 登录助手：用一次浏览器授权换到 refresh_token。

pixiv 的 App API 只认 OAuth 令牌，而拿令牌必须走一次浏览器登录（PKCE 流程）。
这个脚本把整件事收敛成三步：

1. 生成 `code_verifier` / `code_challenge`，打印一个登录地址；
2. 你在浏览器里登录（已经登录过的话点一下就行），页面最后会跳到
   `pixiv://account/login?code=...` —— 浏览器打不开这个协议，
   **把地址栏里那一整条 URL 复制下来**（或者只复制 `code=` 后面那串）；
3. 脚本拿 code 去换令牌，把结果写进插件的数据目录。

用法::

    # 交互式（推荐）
    python scripts/pixiv_login.py --proxy http://127.0.0.1:7890

    # 分两步走（适合在别处登录、只把 code 拿回来）
    python scripts/pixiv_login.py --url-only
    python scripts/pixiv_login.py --code "pixiv://account/login?code=xxxx"

换到的 `refresh_token` 会同时：

- 写进 `<data-dir>/token.json`（插件运行期直接用它，刷新轮换也落在这里）；
- 打印在屏幕上，方便你手动填进 `config.toml` 的 `pixiv.refresh_token`。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import secrets
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]

LOGIN_URL = (
    "https://app-api.pixiv.net/web/v1/login"
    "?code_challenge={challenge}&code_challenge_method=S256&client=pixiv-android"
)
REDIRECT_URI = "https://app-api.pixiv.net/web/v1/users/auth/pixiv/callback"
DEFAULT_DATA_DIR = Path("./data/send-pixiv")


def _force_utf8() -> None:
    """Windows 控制台默认 GBK，中文会直接抛 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def _load_plugin_module(name: str):
    """按加载器的方式把插件包导进来（目录名带连字符，不能直接 import）。

    Args:
        name: 插件包内的模块名，例如 `core.client`。

    Returns:
        对应的模块对象。
    """
    package = "send_pixiv_scripts"
    if package not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            package, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法加载插件包: {ROOT}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[package] = module
        spec.loader.exec_module(module)
    return importlib.import_module(f"{package}.{name}")


def make_pkce() -> tuple[str, str]:
    """生成 PKCE 的 verifier 与 challenge。

    Returns:
        `(code_verifier, code_challenge)`。
    """
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def extract_code(raw: str) -> str:
    """从用户粘贴的内容里抠出授权码。

    用户可能粘贴整条 `pixiv://...` URL，也可能只粘贴 code 本身。

    Args:
        raw: 粘贴的内容。

    Returns:
        授权码。

    Raises:
        ValueError: 里面找不到 code。
    """
    text = (raw or "").strip().strip('"').strip("'")
    if not text:
        raise ValueError("没有输入任何内容")

    if text.startswith("pixiv://") or "code=" in text:
        # pixiv://account/login?code=xxx&via=login
        query = urlparse(text).query or text.split("?", 1)[-1]
        found = parse_qs(query).get("code")
        if found and found[0]:
            return found[0]
        raise ValueError("这段内容里没有 code 参数")
    return text


def state_path(data_dir: Path) -> Path:
    """登录过程的临时状态文件路径。

    Args:
        data_dir: 插件数据目录。

    Returns:
        状态文件路径。
    """
    return data_dir / "login_state.json"


def save_state(data_dir: Path, verifier: str) -> None:
    """保存 verifier，供分两步走时使用。

    Args:
        data_dir: 插件数据目录。
        verifier: PKCE verifier。
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    state_path(data_dir).write_text(
        json.dumps({"code_verifier": verifier, "created_at": time.time()}, indent=2),
        encoding="utf-8",
    )


def load_state(data_dir: Path) -> str:
    """读回 verifier。

    Args:
        data_dir: 插件数据目录。

    Returns:
        PKCE verifier。

    Raises:
        SystemExit: 状态文件不存在或已过期。
    """
    path = state_path(data_dir)
    if not path.is_file():
        raise SystemExit("找不到登录状态，请先运行 --url-only 生成登录地址")
    data = json.loads(path.read_text(encoding="utf-8"))
    verifier = str(data.get("code_verifier") or "")
    if not verifier:
        raise SystemExit("登录状态里没有 code_verifier，请重新生成登录地址")
    age = time.time() - float(data.get("created_at") or 0)
    if age > 3600:
        print(f"[提示] 这个 verifier 是 {age / 60:.0f} 分钟前生成的，建议重新走一遍")
    return verifier


def exchange_code(code: str, verifier: str, proxy: str) -> dict:
    """拿授权码换令牌。

    Args:
        code: 授权码。
        verifier: PKCE verifier。
        proxy: 代理地址，留空表示直连。

    Returns:
        pixiv 认证端点的响应字典。

    Raises:
        SystemExit: 换令牌失败。
    """
    import httpx

    client_mod = _load_plugin_module("core.client")
    now = client_mod._client_time()  # noqa: SLF001 - 复用同一套时间/哈希拼法
    headers = {
        "User-Agent": client_mod.DEFAULT_USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Client-Time": now,
        "X-Client-Hash": hashlib.md5(
            (now + client_mod.CLIENT_HASH_SALT).encode("utf-8")
        ).hexdigest(),
    }
    payload = {
        "client_id": client_mod.CLIENT_ID,
        "client_secret": client_mod.CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    }

    kwargs: dict = {"timeout": 20.0}
    if proxy:
        kwargs["proxy"] = proxy

    try:
        with httpx.Client(**kwargs) as client:
            response = client.post(client_mod.OAUTH_URL, data=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise SystemExit(f"连接 pixiv 失败：{exc}\n（直连不通时请用 --proxy 指定代理）") from exc

    if response.status_code != 200:
        raise SystemExit(
            f"换令牌失败：HTTP {response.status_code}\n{response.text[:400]}\n"
            "最常见的原因是 code 已经用过或过期——它是一次性的，请重新生成登录地址。"
        )
    return response.json()


def main() -> int:
    """执行登录流程。

    Returns:
        进程退出码。
    """
    _force_utf8()

    parser = argparse.ArgumentParser(description="pixiv OAuth 登录助手")
    parser.add_argument("--proxy", default=os.environ.get("HTTPS_PROXY") or os.environ.get("ALL_PROXY") or "",
                        help="HTTP(S) 代理，例如 http://127.0.0.1:7890")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR),
                        help="插件数据目录，默认 ./data/send-pixiv")
    parser.add_argument("--url-only", action="store_true", help="只生成登录地址，不等待输入")
    parser.add_argument("--code", default="", help="直接给出授权码或 pixiv:// 回调地址")
    parser.add_argument("--no-browser", action="store_true", help="不要自动打开浏览器")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).expanduser()

    # --- 第二步：有 code，直接换 ---
    if args.code:
        try:
            code = extract_code(args.code)
        except ValueError as exc:
            print(f"[错误] {exc}")
            return 1
        verifier = load_state(data_dir)
        print("正在用授权码换取令牌…")
        data = exchange_code(code, verifier, args.proxy.strip())
    else:
        # --- 第一步：生成地址 ---
        verifier, challenge = make_pkce()
        save_state(data_dir, verifier)
        url = LOGIN_URL.format(challenge=challenge)

        print("=" * 72)
        print("第 1 步：在浏览器里打开下面这个地址并登录 pixiv")
        print("=" * 72)
        print(url)
        print()
        print("登录成功后页面会跳到 pixiv://account/login?code=... —— 浏览器打不开这种链接，")
        print("没关系，把地址栏里那一整条 URL 复制下来就行。")
        print("=" * 72)

        if args.url_only:
            print()
            print("拿到 code 后运行：")
            print(f'  python scripts/pixiv_login.py --code "<粘贴的内容>" --proxy {args.proxy or "http://127.0.0.1:7890"}')
            return 0

        if not args.no_browser:
            try:
                webbrowser.open(url)
            except Exception:  # noqa: BLE001 - 打不开就算了，地址已经打印出来了
                pass

        print()
        try:
            raw = input("把回调地址（或 code）粘贴到这里，回车确认：")
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            return 1
        try:
            code = extract_code(raw)
        except ValueError as exc:
            print(f"[错误] {exc}")
            return 1
        print("正在用授权码换取令牌…")
        data = exchange_code(code, verifier, args.proxy.strip())

    # --- 落盘 ---
    refresh_token = str(data.get("refresh_token") or "")
    access_token = str(data.get("access_token") or "")
    expires_in = float(data.get("expires_in") or 3600)
    user = data.get("user") or {}

    if not refresh_token:
        print("[错误] 认证端点没有返回 refresh_token，无法继续")
        return 1

    store_mod = _load_plugin_module("core.token")
    store = store_mod.TokenStore(data_dir / "token.json")
    store.save(
        refresh_token=refresh_token,
        access_token=access_token,
        expires_at=time.time() + expires_in,
        config_refresh_token="",
    )
    try:
        state_path(data_dir).unlink(missing_ok=True)
    except OSError:
        pass

    print()
    print("=" * 72)
    print("登录成功 ✅")
    if user:
        print(f"  账号：{user.get('name', '?')}（id {user.get('id', '?')}）")
    print(f"  令牌已写入：{(data_dir / 'token.json').resolve()}")
    print("=" * 72)
    print()
    print("refresh_token（也可以手动填进 config.toml 的 [pixiv] refresh_token）：")
    print()
    print(f"  {refresh_token}")
    print()
    print("注意：这个 token 等同于账号凭据，别贴到群里或提交进仓库。")
    print("插件会优先使用 token.json 里轮换后的新 token，一般不需要再手动维护它。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
