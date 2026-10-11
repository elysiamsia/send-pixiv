# 配置参考

配置文件：`config/plugins/send-pixiv/config.toml`
（路径规则由框架决定：`config/plugins/{plugin_name}/{config_name}.toml`）

改完 **必须重启 bot**——Neo-MoFox 的插件与配置都不热加载。

## [plugin] 开关、白名单与分级

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `enabled` | bool | `true` | 插件总开关 |
| `default_rating` | str | `"safe"` | 默认分级：`safe` 仅全年龄 / `r18` 允许 R-18 / `all` 不限。写错按 `safe` 处理 |
| `allowed_streams` | list[str] | `[]` | 允许使用的会话白名单，留空＝不限制 |
| `blocked_streams` | list[str] | `[]` | 禁用会话黑名单，优先级高于白名单 |
| `r18_streams` | list[str] | `[]` | 这些会话按 `r18` 处理 |
| `r18g_streams` | list[str] | `[]` | 这些会话按 `all` 处理（慎用） |
| `cooldown_seconds` | float | `30.0` | 同一会话两次请求的冷却秒数，0＝不限 |
| `daily_quota` | int | `0` | 同一会话每天最多成功次数，0＝不限 |

> **stream_id 怎么拿？** 它是框架对会话做的 SHA-256 哈希（`ChatStream.generate_stream_id`），
> 不是群号。开 debug 日志发一次图，就能在日志里看到实际值。

## [pixiv] 凭据与网络

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `refresh_token` | str | `""` | **必填**。pixiv 账号的 refresh_token |
| `access_token` | str | `""` | 可选，直接指定 access_token |
| `token_path` | str | `"./data/send-pixiv/token.json"` | 轮换后 refresh_token 的落盘位置 |
| `proxy` | str | `""` | 例如 `http://127.0.0.1:7890`；能直连就留空 |
| `user_agent` | str | `PixivAndroidApp/5.0.234 (Android 11; Pixel 6)` | 请求头，别乱改 |
| `timeout_seconds` | float | `20.0` | 单次请求超时 |
| `min_interval_seconds` | float | `1.2` | 两次 API 调用的最小间隔，自我限流 |
| `max_retries` | int | `3` | 单请求最大尝试次数 |

**关于凭据安全**：`refresh_token` 等同于账号凭据。它既会出现在 `config.toml`，
也会被轮换副本写进 `data/send-pixiv/token.json`。这两个文件都不要进版本库
（本仓库的 `.gitignore` 已经排除 `data/`，但 bot 那边是你自己的仓库，要自己盯）。

## [download] 下载与缓存

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `cache_dir` | str | `"./data/send-pixiv/cache"` | 图片缓存目录，按 `<作品ID>/<页码>.<后缀>` 组织 |
| `max_cache_files` | int | `500` | 缓存文件数上限，超出按最旧淘汰，0＝不限 |
| `image_quality` | str | `"large"` | `large` 长边约 1200px（推荐）/ `original` 原图 |
| `max_images_per_request` | int | `2` | 单次请求最多发几张，1-9 |
| `max_image_mb` | float | `8.0` | 单张体积上限（MB），超限跳过，0＝不限 |

## [search] 搜索与筛选

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `default_count` | int | `2` | 用户没指定张数时发几张 |
| `sort` | str | `"date_desc"` | `date_desc` / `date_asc` / `popular_desc`（需会员） |
| `search_target` | str | `"partial_match_for_tags"` | 另可选 `exact_match_for_tags` / `title_and_caption` |
| `ranking_mode` | str | `"day"` | `day` / `week` / `month` / `day_male` / `day_female` / `week_rookie` / `day_r18` 等 |
| `exclude_ai` | bool | `false` | 过滤 AI 生成作品 |
| `exclude_ugoira` | bool | `true` | 跳过动图（当前不会合成 GIF） |

## [links] 链接自动解析

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `auto_parse` | bool | `true` | 聊天里出现 pixiv 链接时自动发图 |
| `debounce_seconds` | float | `300.0` | 同一作品在同一会话的去重窗口，0＝不去重 |
| `max_per_message` | int | `1` | 单条消息最多处理几个链接 |

## 一个完整的例子

```toml
[plugin]
enabled = true
default_rating = "safe"
cooldown_seconds = 20
daily_quota = 50

[pixiv]
refresh_token = "xxxxx-xxxxx"
proxy = "http://127.0.0.1:7890"

[download]
image_quality = "large"
max_images_per_request = 3

[search]
default_count = 2
exclude_ai = true

[links]
auto_parse = true
```

## 排障

| 现象 | 多半是因为 |
| --- | --- |
| 日志里说"未配置 pixiv 凭据" | `refresh_token` 没填 |
| 提示"refresh_token 无效或已过期" | token 过期或被别处刷新顶掉了，重新做一次 OAuth 授权 |
| 一直超时、报连接失败 | 本机直连不通 pixiv，需要在 `proxy` 里填代理 |
| 搜不到图但没报错 | `default_rating = "safe"` 把结果都过滤了，或该标签确实没有内容 |
| 贴了链接没反应 | `links.auto_parse` 关了 / 该会话被黑名单挡住 / 在去重窗口内 |
| 生成了 `config.toml` 但插件没生效 | 忘了重启 bot |
