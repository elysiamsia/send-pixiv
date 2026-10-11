# send-pixiv

> Neo-MoFox 的 Pixiv 发图插件：聊天里贴个链接、或者直接跟 bot 说"来点猫娘的图"，
> 它就把插画下载好发到会话里。内置分级过滤与会话白名单。

[![License](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![Neo-MoFox](https://img.shields.io/badge/Neo--MoFox-plugin-green.svg)](https://github.com/MoFox-Studio/Neo-MoFox)

## 它是什么

一个纯 Python 的 Neo-MoFox 插件（不是 PixivFlow 的包装）。它自己调用 pixiv 的
App API 完成 **搜索 → 分级过滤 → 下载 → 发送**，不依赖 Node、不需要额外的常驻进程。

和 [PixivFlow](https://github.com/redtidev1918/PixivFlow) 的关系：PixivFlow 是一套
Node 的命令行下载器/调度器（MIT 协议），本项目在设计时参考了它的认证流程与
App API 用法，但代码是各写各的。想按计划批量归档作品请用 PixivFlow；
想在聊天里即时发图，用这个插件。

## 功能

| 能力 | 触发方式 | 说明 |
| --- | --- | --- |
| 链接发图 | 聊天里贴 pixiv 链接 | 自动识别作品页 / 作者主页 / App 深链，下载并发出来 |
| 让 bot 找图 | 对 bot 说"来点 XX 的图" | LLM 调用 `send_pixiv_illust` 动作：按标签搜索、排行榜取图后发送 |
| 查作品信息 | 让 bot 先看看有哪些 | LLM 调用 `pixiv_search` 工具，只返回标题/作者/标签/链接，不发图 |
| 分级控制 | 配置 | `safe` / `r18` / `all` 三档；可按会话单独授权 |
| 会话白名单 | 配置 | 哪些群能用、哪些群禁用、每个会话的冷却与每日配额 |

发图链路里所有"可能失败"的地方都会转成一句人能看懂的话回给模型或日志，
不会因为一张图 403 就把整轮对话搞崩。

## 安装

### 方式一：插件市场（推荐）

在插件市场里搜索 `send-pixiv` 安装 `.mfp` 包。装完记得**重启 bot**——
Neo-MoFox 的插件与配置都不热加载。

### 方式二：手动放入

把本仓库整个目录放进 bot 的 `plugins/` 下，目录名保持 `send-pixiv`：

```text
<bot>/plugins/send-pixiv/
```

`manifest.json` 里的 `name`、插件类的 `plugin_name` 与目录名三者必须一致，
否则加载器找不到插件类。

### 依赖

**没有第三方 Python 依赖**，只用标准库 + bot 自带的 `httpx`。
`manifest.json` 的 `python_dependencies` 因此是空的。

## 配置

首次启动会生成 `config/plugins/send-pixiv/config.toml`。最少只要填两项：

```toml
[pixiv]
refresh_token = "你的 refresh_token"
proxy = "http://127.0.0.1:7890"   # 能直连 pixiv 就留空
```

### 怎么拿 refresh_token

仓库自带一个登录助手，跑一次浏览器授权就行：

```bash
python scripts/pixiv_login.py --proxy http://127.0.0.1:7890
```

它会打印一个 pixiv 登录地址 → 你在浏览器里登录（已登录的话点一下就行）→
页面最后会跳到 `pixiv://account/login?code=...`，浏览器打不开这种协议没关系，
**把地址栏那一整条 URL 复制回终端**即可。脚本会拿 code 换令牌，并写进
`data/send-pixiv/token.json`（插件运行期直接用它）。

也可以分两步走：`--url-only` 只生成地址，拿到 code 后再 `--code "<粘贴内容>"`。

> **注意**：refresh_token 等同于账号凭据。它既在 `token.json` 里，也可能被你填进
> `config.toml`——这两个文件都别提交进仓库、别发到群里。

完整配置项见 [docs/config.md](docs/config.md)。

## 用法

**贴链接就发图**：消息里出现 `https://www.pixiv.net/artworks/12345678` 就会自动发送，
同一个作品在同一会话里 5 分钟内不会重复发（可配置）。不想让插件插手就关掉
`links.auto_parse`。

**让 bot 自己找**：

> 用户：来点猫娘的图
> bot：（调用 `send_pixiv_illust` 动作，关键词=猫娘）→ 发图 + 一行作品信息

**先查后发**：

> 用户：pixiv 上有什么好看的风景图
> bot：（调用 `pixiv_search`）返回候选清单 → 用户挑一个 → bot 再发

## 工作原理

```text
聊天消息 ──► PixivLinkWatcher (EventHandler)  ─┐
                                               ├─► PixivService.resolve()  pixiv App API
LLM ──► send_pixiv_illust (Action)  ──────────┤      ├─ 链接 → /v1/illust/detail
                                               │      ├─ 关键词 → /v1/search/illust
LLM ──► pixiv_search (Tool，只查不发)  ────────┘      └─ 榜单 → /v1/illust/ranking
                                                            │
                                       分级过滤 + 体积/张数限制 │
                                                            ▼
                                             ImageCache（落盘去重）──► send_api.send_image()
```

细节与取舍记在 [docs/design.md](docs/design.md)。

## 开发

```bash
# 单元测试（纯标准库，不碰网络）
cd tests && python -m unittest discover -s . -p "test_*.py"

# 校验 manifest 与组件声明是否对得上
python scripts/check_manifest.py

# 打包 .mfp
python scripts/build_mfp.py          # 产物在 dist/send-pixiv-<version>.mfp
```

仓库根目录就是插件根目录：`manifest.json` 与 `plugin.py` 必须在最外层，
`docs/`、`tests/`、`scripts/` 只在开发时存在，不会打进 `.mfp`。

## 免责声明

本项目与 pixiv Inc. 无任何关联。请遵守 pixiv 的服务条款，只下载你有权访问的内容；
不要在公开场合分享 R-18 内容。使用者需自行承担账号与内容合规风险。

## 许可证

[GPL-3.0](LICENSE)
