"""`check_manifest` 里两条静态规则的单元测试。

这两条规则都来自真实事故：
- Action 缺 `associated_types` → 整个插件加载失败（第 8 节事故）
- `logger.info()` 写 printf 风格 → 运行期 TypeError（第 10 节）
静态校验器必须能拦住它们，否则等于没有。
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import check_manifest  # noqa: E402


class LoggerCallCheckTest(unittest.TestCase):
    """`check_logger_calls` 的用例。"""

    def _check(self, source: str) -> list[str]:
        return check_manifest.check_logger_calls(ast.parse(source), "demo.py")

    def test_single_argument_is_fine(self) -> None:
        self.assertEqual(self._check('logger.info("一条消息")'), [])
        self.assertEqual(self._check('logger.warning(f"a={1} b={2}")'), [])

    def test_printf_style_is_rejected(self) -> None:
        "真实事故：on_plugin_loaded 里写成 printf 风格，加载时才炸。"
        problems = self._check('logger.info("分级=%s 张数=%s", rating, count)')
        self.assertEqual(len(problems), 1)
        self.assertIn("logger.info()", problems[0])
        # message + rating + count = 3 个位置参数
        self.assertIn("3 个位置参数", problems[0])

    def test_kwargs_are_allowed(self) -> None:
        "框架签名是 info(message, **kwargs)，关键字参数没问题。"
        self.assertEqual(self._check('logger.info("消息", extra={"a": 1})'), [])

    def test_other_loggers_ignored(self) -> None:
        "只盯插件里那个 logger，别误伤标准 logging。"
        self.assertEqual(self._check('logging.getLogger("x").info("a=%s", b)'), [])
        self.assertEqual(self._check('self.logger.info("a=%s", b)'), [])

    def test_other_methods_ignored(self) -> None:
        self.assertEqual(self._check('logger.bind("a", "b")'), [])


class ConfigSectionCheckTest(unittest.TestCase):
    """配置节必须"类 + 字段"两件套。

    真实踩坑：只写了 `@config_section("trigger")` 的类，忘了声明
    `trigger: TriggerSection = Field(...)`，结果该节不进 TOML、
    `cfg.trigger` 也不存在，而且**一点报错都没有**。
    """

    def _check(self, source: str) -> list[str]:
        return check_manifest.check_config_sections(ast.parse(source), "config.py")

    def test_section_without_field_is_reported(self) -> None:
        source = (
            "class Cfg(BaseConfig):\n"
            "    @config_section('trigger')\n"
            "    class TriggerSection(SectionBase):\n"
            "        enabled: bool = True\n"
        )
        problems = self._check(source)
        self.assertEqual(len(problems), 1)
        self.assertIn("TriggerSection", problems[0])
        self.assertIn("trigger: TriggerSection = Field(default_factory=TriggerSection)", problems[0])

    def test_section_with_field_is_fine(self) -> None:
        source = (
            "class Cfg(BaseConfig):\n"
            "    @config_section('trigger')\n"
            "    class TriggerSection(SectionBase):\n"
            "        enabled: bool = True\n"
            "\n"
            "    trigger: TriggerSection = Field(default_factory=TriggerSection)\n"
        )
        self.assertEqual(self._check(source), [])

    def test_field_name_derivation(self) -> None:
        self.assertEqual(check_manifest._section_field_name("TriggerSection"), "trigger")
        self.assertEqual(check_manifest._section_field_name("DownloadSection"), "download")
        self.assertEqual(check_manifest._section_field_name("MyLongNameSection"), "my_long_name")
        self.assertEqual(check_manifest._section_field_name("Plain"), "plain")


class AssociatedTypesCheckTest(unittest.TestCase):
    """`scan` 对 associated_types 的采集。"""

    def test_action_without_associated_types_is_reported(self) -> None:
        "这条规则对应的就是那次「整个插件加载失败」的事故。"
        source = (
            "class MyAction(BaseAction):\n"
            "    name = 'demo'\n"
            "    async def execute(self):\n"
            "        return True, ''\n"
        )
        tree = ast.parse(source)
        problems = check_manifest.check_logger_calls(tree, "demo.py")
        self.assertEqual(problems, [])
        # 采集逻辑：没有 associated_types 时取到 None，main() 会据此报错
        self.assertIsNone(check_manifest._list_literal(None))

    def test_list_literal_extraction(self) -> None:
        node = ast.parse('associated_types = ["image"]').body[0]
        self.assertEqual(check_manifest._list_literal(check_manifest._assigned_value(node, "associated_types")), ["image"])

    def test_empty_list_is_detected_as_missing(self) -> None:
        "空列表在框架那边等同于没写，必须被判为缺失。"
        node = ast.parse("associated_types = []").body[0]
        self.assertEqual(check_manifest._list_literal(check_manifest._assigned_value(node, "associated_types")), [])


if __name__ == "__main__":
    unittest.main()
