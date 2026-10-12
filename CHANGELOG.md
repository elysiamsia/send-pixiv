# 更新日志

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与
[语义化版本](https://semver.org/lang/zh-CN/)。

## [0.2.0] - 2026-10-12

### 新增

- **聊天指令**：`#r18 猫` / `#pixiv 风景` 直接搜图。实现为框架原生的
  **Command 组件**——命令消息不会进聊天流，模型看不到它，因此既不会被
  "我不方便查找这类内容"挡掉，也不会在插件发完图之后再插一句拒绝。
  插件加载时会把 `#` **追加**进框架的命令前缀（默认只有 `/`），可配置关闭。
- **自动换图**：同一个关键词每次发不同的图。pixiv 的搜索接口对同一个词
  每次返回同一批结果，插件把候选重排成「没发过的优先、发过的按最早发送在后」，
  一轮发完之后自动从头轮换，既不重复也不会卡住。
  配置项 `search.rotate` / `rotate_pool` / `rotate_window_seconds`。
- **图片直连**（`download.direct_image`）：大陆网络下图片可以绕过 SNI 封锁
  直接下载，省代理流量。做法是连真实 IP 且**不发 SNI**，同时仍然验证证书链、
  并按 `subjectAltName` 手工校验主机名。
- **网络自检脚本** `scripts/check_network.py`：分别探测「API 直连 / API 走代理 /
  图片直连」三条路，并给出配置建议。
- **白名单直接填群号 / QQ 号**：框架的 `stream_id` 是 SHA-256 哈希，人眼没法认。
  插件按 `sha256(f"{platform}_{group_id}")` 反推，所以四种名单都能直接写群号。
- **离线框架替身** `tests/framework_stub.py`：让依赖框架的组件代码也能被单元测试
  真正驱动。

### 修复

- **Action 缺 `associated_types` 导致整个插件加载失败**：框架的
  `validate_associated_types()` 在 try 块外调用，一个组件不合规就会让排在它后面的
  组件全部不注册。后遗症极具迷惑性（报错指向 chatter 而不是插件）。
- **refresh_token 轮换后重启即失效**：构造时优先用了配置里的旧值，改为落盘的为准。
- **`logger.info()` 用了 printf 风格**：框架的 Logger 只接受一个位置参数，
  会抛 `TypeError`；且这种代码通常写在 `on_plugin_loaded` 里，最容易漏。
- **指令被静默丢弃**：防抖判断写成 `if not debouncer.hit(...)`，语义正好反了，
  导致每条**新**指令都被丢掉、只有重复发的才执行。
- **`PixivService.resolve()` 传了空 stream_id 去问分级**：会话级的
  `r18_streams` 配了也不生效。
- **`large` 档其实是 600x1200 的 webp 缩略图**（十几 KB），默认改为 `master1200`，
  并按 `master1200 → large → original` 回退。
- **缓存键没带画质档**：换档位后仍命中旧文件。

### 工程

- `scripts/check_manifest.py` 增加四条静态规则，全部来自真实事故：
  组件必须写进 `get_components()`、`logger.xxx()` 只能传一个位置参数、
  `@config_section` 的类必须有同名字段、Action/Agent 必须有非空
  `associated_types`。组件扫描支持间接继承，并跳过下划线开头的内部基类。
- 单元测试 87 → 161。

## [0.1.0] - 2026-10-12

首个版本。

### 新增

- **链接发图**：`PixivLinkWatcher` 订阅 `ON_MESSAGE_RECEIVED`，
  自动识别作品页、`/en/` 语言前缀、`#页码` 锚点、旧版 `member_illust.php`、
  App 深链 `pixiv://illusts/`、作者主页与少写协议头的 `pixiv.net/...`。
- **LLM 发图**：`send_pixiv_illust` 动作，支持链接 / 关键词 / 排行榜三种入参。
- **LLM 查询**：`pixiv_search` 工具，只返回作品摘要不发送。
- **分级控制**：按 `x_restrict` + `sanity_level` 判定 safe / r18 / r18g，
  支持全局默认档位与按会话授权。
- **会话策略**：白名单、黑名单、冷却、每日配额。
- **图片缓存**：按 `<作品 ID>/<页码>` 落盘去重，超量按最旧淘汰。
- **凭据维护**：自动刷新 access_token，并把 pixiv 轮换后的 refresh_token 落盘。
- **纯标准库实现**：无第三方依赖，`httpx` 用 bot 自带的那个。

### 说明

- 动图（ugoira）默认跳过，当前版本不会合成 GIF。
- 小说作品暂不支持发送，识别到会明确提示。
