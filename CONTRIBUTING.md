# 贡献指南

感谢愿意花时间改进 send-pixiv！这份文档说明怎么把改动提上来最省事。

## 环境准备

```bash
git clone https://github.com/elysiamsia/send-pixiv.git
cd send-pixiv
```

插件本身**零第三方依赖**，测试也只用标准库，所以不需要装任何东西——
用任意 Python 3.11+ 就能跑全套测试。

## 提交前请自检

```bash
python scripts/check_manifest.py                              # 静态校验，必须 0 错误
cd tests && python -m unittest discover -s . -p "test_*.py"   # 单元测试，必须全绿
```

静态校验覆盖的规则都来自真实事故，别跳过：

| 规则 | 对应的坑 |
| --- | --- |
| 组件必须写进 `get_components()` | 光有类文件不会注册，**日志里还不报错** |
| `logger.xxx()` 只能传一个位置参数 | 框架的 Logger 不吃 printf 风格，加载时抛 TypeError |
| `@config_section` 的类必须有同名字段 | 否则该节不进 TOML、`cfg.xxx` 也不存在 |
| Action / Agent 必须有非空 `associated_types` | 否则**整个插件加载失败** |

## 写代码的几条约定

- **注释解释"为什么"**，不解释"是什么"。踩过的坑一定要写下来，那是最有价值的部分。
- **新组件**：在 `plugin.py` 里 import，并加进 `get_components()` 的返回列表。
- **配置项变更**：同步更新 `docs/config.md`，并在提交信息里说明（schema 变了的话
  已装用户的 `config.toml` 需要重新生成——`extra="forbid"` 会让旧键直接报错）。
- **新增纯逻辑**优先放进 `core/`，那层不依赖框架、最好测。
- **组件里的流程**用 `tests/framework_stub.py` 驱动着测（早期就是漏了这一层，
  让"防抖判断写反"这种 bug 溜到了用户手上）。

## 提交信息

用 [Conventional Commits](https://www.conventionalcommits.org/) 风格：

```text
feat: 新增 xxx
fix: 修复 xxx
docs: 更新 xxx
refactor: 重构 xxx
test: 补充 xxx
chore: 杂项
```

正文里请写清**根因**和**验证方式**，尤其是修 bug 时——"改了什么"看 diff 就知道，
"为什么这么改"才是后人需要的。

## 分支与 PR

- 从 `main` 开分支，名字随意但要能看懂（`fix/rotate-repeat`、`feat/ugoira`）。
- PR 描述里贴一下自检结果。
- 涉及行为变更的，说明一下**用户侧会看到什么不同**。

## 可以做的方向

- 🎞️ **ugoira 动图支持**（合成 GIF / MP4）
- 🔍 搜索结果的排序策略（收藏数加权、按作者去重等）
- 🌐 英文文档补全（`README.en.md` 目前只是概览）
- 🧪 提高 `services/` 层的测试覆盖（目前主要覆盖 `core/` 与组件流程）

有想法但不确定怎么落地的话，先开个 Issue 聊聊也行。
