"""把插件目录以合成包名加载进来，供单元测试使用。

插件目录名 `send-pixiv` 带连字符，不能直接 `import`；这里按框架加载器同样的做法
（`spec_from_file_location` + `submodule_search_locations`）手工建立包，
这样测试里就能正常写 `from <pkg>.core.links import ...`。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "send_pixiv_under_test"

__all__ = ["PLUGIN_ROOT", "PACKAGE_NAME", "load_package"]


def load_package():
    """加载插件包并返回模块对象（重复调用只加载一次）。

    Returns:
        插件包的模块对象。
    """
    if PACKAGE_NAME in sys.modules:
        return sys.modules[PACKAGE_NAME]

    spec = importlib.util.spec_from_file_location(
        PACKAGE_NAME,
        PLUGIN_ROOT / "__init__.py",
        submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载插件包: {PLUGIN_ROOT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE_NAME] = module
    spec.loader.exec_module(module)
    return module
