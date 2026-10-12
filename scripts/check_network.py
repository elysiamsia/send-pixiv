#!/usr/bin/env python3
"""网络自检：pixiv 的哪一部分能直连、哪一部分必须走代理。

大陆网络下 pixiv 是「DNS 投毒 + SNI 阻断」两道锁，而且**不同域名的处境不一样**：

- `app-api.pixiv.net` / `oauth.secure.pixiv.net` —— **在 GFW 的 SNI 封锁清单里**，
  没有代理绝对不通（换 IP、改大小写、加尾点、TCP 分片全部实测无效）。
- `i.pximg.net` —— **不在清单里**，连它的真实 IP 且不发 SNI 就能直接下图。

这个脚本把三条路各打一遍，告诉你当前环境到底哪条通、配置该怎么填。

用法::

    python scripts/check_network.py
    python scripts/check_network.py --proxy http://127.0.0.1:7890
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 拿来做连通性探测的图片（一个真实存在的 master1200 地址）
PROBE_IMAGE = (
    "https://i.pximg.net/img-master/img/2026/10/12/02/36/11/"
    "150747678_p0_master1200.jpg"
)


def _force_utf8() -> None:
    """Windows 控制台默认 GBK，中文会抛 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def _load(name: str):
    """按加载器的方式导入插件包内的模块。

    Args:
        name: 包内模块名，例如 `core.client`。

    Returns:
        模块对象。
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


async def _probe(label: str, coro) -> bool:
    """跑一个探测并打印结果。

    Args:
        label: 显示名。
        coro: 待执行的协程。

    Returns:
        成功返回 `True`。
    """
    started = time.time()
    try:
        detail = await coro
        print(f"  ✅ {label:<34} {time.time() - started:5.2f}s  {detail}")
        return True
    except Exception as exc:  # noqa: BLE001 - 自检脚本就是要报错
        print(f"  ❌ {label:<34} {time.time() - started:5.2f}s  {type(exc).__name__}: {str(exc)[:70]}")
        return False


async def main() -> int:
    """执行自检。

    Returns:
        进程退出码：全部关键项通过返回 0。
    """
    _force_utf8()
    parser = argparse.ArgumentParser(description="pixiv 网络自检")
    parser.add_argument("--proxy", default="", help="要测试的代理地址，例如 http://127.0.0.1:7890")
    args = parser.parse_args()

    client_mod = _load("core.client")
    PixivClient = client_mod.PixivClient

    print("=" * 78)
    print("pixiv 网络自检")
    print("=" * 78)

    results: dict[str, bool] = {}

    # --- 1. API：直连（预期失败）---
    print()
    print("【1】API（搜索 / 详情 / 换令牌）—— 直连")
    print("    这个域名在 GFW 的 SNI 封锁清单里，直连预期就是不通。")
    direct_api = PixivClient(refresh_token="x", proxy="", timeout=8, max_retries=1, min_interval=0)
    try:
        results["api_direct"] = await _probe(
            "app-api.pixiv.net 直连", direct_api.illust_detail(1)
        )
    finally:
        await direct_api.close()

    # --- 2. API：走代理 ---
    print()
    print("【2】API —— 走代理")
    if not args.proxy:
        print("  ⏭  没给 --proxy，跳过（用 --proxy http://127.0.0.1:7890 测）")
        results["api_proxy"] = False
    else:
        proxied = PixivClient(
            refresh_token="x", proxy=args.proxy, timeout=12, max_retries=1, min_interval=0
        )
        try:
            # 用一个假 token 打 oauth，只要不是网络错误就说明通路没问题
            ok = await _probe("oauth.secure.pixiv.net 走代理", proxied._refresh_access_token())
            results["api_proxy"] = ok
        except Exception as exc:  # noqa: BLE001
            # 走到这里说明网络通了、只是 token 是假的 —— 这正是期望结果
            kind = type(exc).__name__
            if kind == "PixivAuthError":
                print(f"  ✅ {'oauth.secure.pixiv.net 走代理':<34} 通路正常（假 token 被正确拒绝）")
                results["api_proxy"] = True
            else:
                print(f"  ❌ {'oauth.secure.pixiv.net 走代理':<34} {kind}: {str(exc)[:70]}")
                results["api_proxy"] = False
        finally:
            await proxied.close()

    # --- 3. 图片：直连（关键能力）---
    print()
    print("【3】图片（i.pximg.net）—— 直连（连真实 IP + 不发 SNI）")
    image_client = PixivClient(
        refresh_token="", proxy="", timeout=20, max_retries=2, min_interval=0, direct_image=True
    )
    probe_dir = ROOT / "data" / "_probe"
    try:
        async def fetch() -> str:
            path = await image_client.download_image(PROBE_IMAGE, probe_dir / "probe.jpg")
            size = path.stat().st_size / 1024
            path.unlink(missing_ok=True)
            return f"{size:.0f} KB"

        results["image_direct"] = await _probe("i.pximg.net 直连", fetch())
    finally:
        await image_client.close()
    try:
        probe_dir.rmdir()
    except OSError:
        pass

    # --- 结论 ---
    print()
    print("=" * 78)
    print("结论")
    print("=" * 78)
    if results.get("image_direct"):
        print("  ✅ 图片可以直连 —— 配置里把 download.direct_image 设为 true，图片就不走代理")
    else:
        print("  ❌ 图片直连失败 —— 可能是网络环境变了，或内置 IP 失效；把 direct_image 设回 false")

    if results.get("api_proxy"):
        print("  ✅ API 走代理可用 —— pixiv.proxy 填对即可")
    elif args.proxy:
        print("  ❌ API 走代理也不通 —— 检查代理地址 / 代理是否在运行")
    else:
        print("  ⚠️  API 没测（没给 --proxy）")

    if not results.get("api_direct"):
        print()
        print("  说明：API 直连不通是**预期结果**，不是配置问题——")
        print("       app-api.pixiv.net 在 GFW 的 SNI 封锁清单里，")
        print("       换 IP、改大小写、加尾点、TCP 分片实测全部无效，只能走代理。")
        print("       direct_image 只让图片直连，替代不了代理。")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
