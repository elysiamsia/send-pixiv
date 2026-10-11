#!/usr/bin/env python3
"""把插件同步到本机 bot 的 plugins 目录，方便本地联调。

这只是开发便利脚本：正式分发走 .mfp + 插件市场。
同步完**必须重启 bot**——Neo-MoFox 的插件与配置都不热加载。

用法::

    python scripts/install_to_bot.py "D:/Neo-MoFox-bot/bot-xxx/neo-mofox"
    python scripts/install_to_bot.py <bot> --clean    # 先清掉目标目录再复制
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}


def main() -> int:
    """执行同步。

    Returns:
        进程退出码。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    parser = argparse.ArgumentParser(description="把插件复制到 bot 的 plugins 目录")
    parser.add_argument("bot_dir", help="bot 根目录（含 plugins/ 的那一层）")
    parser.add_argument("--clean", action="store_true", help="先删除目标目录再复制")
    parser.add_argument("--name", default=None, help="目标目录名，默认用 manifest.name")
    args = parser.parse_args()

    bot_dir = Path(args.bot_dir).expanduser().resolve()
    plugins_dir = bot_dir / "plugins"
    if not plugins_dir.is_dir():
        print(f"[错误] {plugins_dir} 不存在，确认一下 bot 目录")
        return 1

    import json

    name = args.name or json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["name"]
    target = plugins_dir / name

    if args.clean and target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    copied = 0
    for path in sorted(ROOT.rglob("*")):
        rel = path.relative_to(ROOT)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        # 开发用的目录不同步过去，保持 bot 那边干净
        if rel.parts and rel.parts[0] in {"docs", "scripts", ".git"}:
            continue
        destination = target / rel
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            copied += 1

    print(f"已同步 {copied} 个文件到 {target}")
    print("记得重启 bot：Neo-MoFox 的插件与配置都不热加载。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
