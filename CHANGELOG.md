# 更新日志

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与
[语义化版本](https://semver.org/lang/zh-CN/)。

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
