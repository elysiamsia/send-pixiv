#!/usr/bin/env python3
"""把插件打包成 .mfp。

@.mfp@ 就是一个 zip：解压后 @manifest.json@ 与 @plugin.py@ 位于压缩包根目录。
所以"打包"这件事本身没有魔法，麻烦的是**别把开发用的东西打进去**。

用法::

    python scripts/build_mfp.py            # 产物: dist/send-pixiv-<version>.mfp
    python scripts/build_mfp.py --list     # 顺便打印包内清单
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_manifest  # noqa: E402 - 需要先把它所在目录加进 sys.path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"

#: 不打包的目录（开发用）。
EXCLUDE_DIRS = {
    "docs", "tests", "scripts", "dist", "__pycache__",
    ".git", ".github", ".venv", "venv", ".pytest_cache", ".ruff_cache", ".mypy_cache",
}
#: 不打包的文件。
EXCLUDE_FILES = {".gitignore", ".gitattributes", ".DS_Store"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".part", ".mfp"}


def collect_files() -> list[Path]:
    """收集需要打进包里的文件。

    Returns:
        文件路径列表（相对于仓库根目录），已排序。
    """
    files: list[Path] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if rel.name in EXCLUDE_FILES or rel.suffix in EXCLUDE_SUFFIXES:
            continue
        files.append(rel)
    return files


def main() -> int:
    """执行打包。

    Returns:
        进程退出码。
    """
    check_manifest._force_utf8()  # noqa: SLF001 - 复用同一个控制台编码修正
    parser = argparse.ArgumentParser(description="打包 send-pixiv 为 .mfp")
    parser.add_argument("--list", action="store_true", help="打印包内文件清单")
    parser.add_argument("--skip-check", action="store_true", help="跳过 manifest 校验")
    args = parser.parse_args()

    if not args.skip_check:
        code = check_manifest.main()
        if code != 0:
            print("\nmanifest 校验未通过，已中止打包")
            return code

    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    name = str(manifest["name"])
    version = str(manifest["version"])

    DIST.mkdir(exist_ok=True)
    target = DIST / f"{name}-{version}.mfp"
    files = collect_files()

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for rel in files:
            archive.write(ROOT / rel, arcname=rel.as_posix())

    size_kb = target.stat().st_size / 1024
    print(f"\n已生成 {target.relative_to(ROOT)}（{len(files)} 个文件，{size_kb:.1f} KB）")
    if args.list:
        for rel in files:
            print(f"  {rel.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
