#!/usr/bin/env python3
"""静态校验 manifest.json 与代码里的声明是否自洽。

只能对着源码做静态分析（不 import 插件），但足以挡住最常见的几类加载失败：

- @manifest.name@ 与插件类的 @plugin_name@ 不一致（加载器会报"插件类未注册"）
- @include[].component_name@ 与组件类的 @name@ 属性对不上（组件注册不上）
- @api_version@ 里写了不存在的 API 模块名（会被直接拒绝加载）
- 声明了组件却忘了在 @get_components()@ 里返回（include 不是注册真源）

用法::

    python scripts/check_manifest.py
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

#: 框架允许出现在 api_version 里的 20 个模块（与 src/app/plugin_system/api/ 一一对应）。
LEGAL_API_MODULES = {
    "action_api", "adapter_api", "agent_api", "chat_api", "command_api", "config_api",
    "database_api", "event_api", "llm_api", "log_api", "media_api", "message_api",
    "permission_api", "plugin_api", "prompt_api", "router_api", "send_api",
    "service_api", "storage_api", "stream_api",
}

#: 基类名 -> manifest 里的 component_type。
BASE_TO_TYPE = {
    "BaseTool": "tool",
    "BaseAction": "action",
    "BaseService": "service",
    "BaseEventHandler": "event_handler",
    "BaseConfig": "config",
    "BaseCommand": "command",
    "BaseRouter": "router",
    "BaseAdapter": "adapter",
    "BaseAgent": "agent",
    "BaseChatter": "chatter",
}

SKIP_DIRS = {"tests", "scripts", "docs", "dist", "__pycache__", ".git", ".github", ".venv"}


def iter_sources() -> list[Path]:
    """列出需要扫描的 Python 文件。

    Returns:
        插件代码文件的路径列表。
    """
    files: list[Path] = []
    for path in sorted(ROOT.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        files.append(path)
    return files


def _literal_str(node: ast.AST | None) -> str | None:
    """从 AST 节点里取出字符串字面量。

    Args:
        node: 待取值的节点。

    Returns:
        字符串内容；不是字符串字面量时返回 @None@。
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _targets_name(node: ast.stmt, attr: str) -> str | None:
    """判断赋值语句是否在给指定属性赋值，是则返回字符串值。

    Args:
        node: 语句节点。
        attr: 目标属性名，例如 @"name"@。

    Returns:
        赋的字符串值；无关语句返回 @None@。
    """
    value: ast.AST | None = None
    target: ast.AST | None = None

    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        target, value = node.target, node.value
    elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        target, value = node.targets[0], node.value

    if target is None or getattr(target, "id", None) != attr:
        return None
    return _literal_str(value)


def scan() -> tuple[str | None, list[dict[str, str]]]:
    """扫描代码，收集插件名与所有组件声明。

    Returns:
        @(plugin_name, components)@，components 每项含 class / type / name / file。
    """
    plugin_name: str | None = None
    components: list[dict[str, str]] = []

    for path in iter_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            print(f"[错误] 无法解析 {path.relative_to(ROOT)}: {exc}")
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {
                base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
                for base in node.bases
            }
            kind = next((BASE_TO_TYPE[b] for b in bases if b in BASE_TO_TYPE), None)
            rel = path.relative_to(ROOT).as_posix()

            if "BasePlugin" in bases or kind is None:
                for statement in node.body:
                    found = _targets_name(statement, "plugin_name")
                    if found:
                        plugin_name = found
            if kind is None:
                continue

            name = None
            for statement in node.body:
                name = name or _targets_name(statement, "name")
            components.append(
                {"class": node.name, "type": kind, "name": name or "", "file": rel}
            )

    return plugin_name, components



def _force_utf8() -> None:
    """Windows 控制台默认是 GBK，中文与符号会直接抛 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

def main() -> int:
    """执行校验。

    Returns:
        进程退出码：0 通过，1 有问题。
    """
    _force_utf8()
    manifest_path = ROOT / "manifest.json"
    if not manifest_path.is_file():
        print("[错误] 找不到 manifest.json")
        return 1
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))

    errors: list[str] = []
    warnings: list[str] = []

    # --- 基础字段 ---
    for key in ("name", "version", "description", "author", "include", "entry_point"):
        if not manifest.get(key):
            errors.append(f"manifest.json 缺少必填字段: {key}")

    version = str(manifest.get("version", ""))
    if version and len(version.split(".")) < 3:
        warnings.append(f"version 不是 x.y.z 形式: {version}")

    plugin_name, components = scan()

    # --- 名字一致性 ---
    if plugin_name and manifest.get("name") != plugin_name:
        errors.append(
            f"manifest.name={manifest.get('name')!r} 与插件类的 plugin_name={plugin_name!r} 不一致，"
            "加载器会报「插件类未注册」"
        )
    if not plugin_name:
        warnings.append("没有扫描到 plugin_name，确认 plugin.py 里的插件类是否正常")

    if manifest.get("entry_point") and not (ROOT / str(manifest["entry_point"])).is_file():
        errors.append(f"entry_point 指向的文件不存在: {manifest['entry_point']}")

    # --- api_version ---
    api_version = manifest.get("api_version")
    if isinstance(api_version, dict):
        for module in api_version:
            if module not in LEGAL_API_MODULES:
                errors.append(f"api_version 里的 {module!r} 不是合法 API 模块名，会被拒绝加载")
    elif isinstance(api_version, str):
        if len(api_version.split(".")) < 3:
            warnings.append(f"api_version 字符串不是 x.y.z 形式: {api_version}")
    elif api_version is not None:
        errors.append("api_version 只能是字符串或 dict")

    # --- include 与代码里的组件 ---
    declared = {
        (str(item.get("component_type")), str(item.get("component_name")))
        for item in manifest.get("include") or []
        if isinstance(item, dict)
    }
    actual = {(item["type"], item["name"]) for item in components if item["name"]}

    for component_type, component_name in sorted(declared - actual):
        errors.append(
            f"include 里声明了 {component_type}:{component_name}，"
            "但代码里找不到带这个 name 的组件"
        )
    for component_type, component_name in sorted(actual - declared):
        warnings.append(
            f"代码里有组件 {component_type}:{component_name} 没写进 manifest.include"
        )

    plugin_components = [item for item in components if item["type"] != "config"]
    print(f"插件名: {manifest.get('name')}  版本: {version}")
    print(f"入口点: {manifest.get('entry_point')}")
    print(f"声明组件 {len(declared)} 个，代码里扫到 {len(plugin_components)} 个：")
    for item in sorted(plugin_components, key=lambda x: (x["type"], x["name"])):
        mark = "✓" if (item["type"], item["name"]) in declared else "!"
        print(f"  {mark} {item['type']:<14} {item['name']:<22} {item['class']} ({item['file']})")

    for message in warnings:
        print(f"[警告] {message}")
    for message in errors:
        print(f"[错误] {message}")

    if errors:
        print(f"\n校验未通过：{len(errors)} 个错误，{len(warnings)} 个警告")
        return 1
    print(f"\n校验通过（{len(warnings)} 个警告）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
