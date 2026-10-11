# 设计文档

记录这个插件"为什么长这样"。结论都来自对 Neo-MoFox 源码与本机已装插件的实际核对，
不是照文档抄的。

## 1. 定位

一句话：**把 pixiv 的插画搬进聊天**，而不是把 PixivFlow 搬进 bot。

PixivFlow 是一个很完整的 Node 命令行工具：登录、批量收集、cron 调度、去重、投递、
WebUI。把它整个塞进 Neo-MoFox（Python）有三条路：

| 方案 | 做法 | 代价 |
| --- | --- | --- |
| A 纯 Python 自研 ✅ | 插件自己调 pixiv App API | 要自己实现认证/搜索/下载/过滤 |
| B 只做接收端 | 实现 PixivFlow 的 Gateway Contract v1，接它投递过来的作品 | 装机必须常驻跑 Node 版 PixivFlow，且群里点播不了 |
| C 混合 | 点播走 PixivFlow REST API，结果走 Gateway 回传 | 复用度最高，但依赖最重、两套系统都得会排障 |

**选了 A**：插件要能被别人一键装走，就不该要求对方先跑一个 Node 服务。
PixivFlow 的价值在于"归档与调度"，那是它自己的场景；聊天里要的是"现在就发一张"。

B/C 没有被否掉，只是推迟：`PixivFlow → POST /deliver` 那套契约如果要接，
可以后续单独加一个 Router 组件，不需要动现有的检索/发送链路。

## 2. 组件划分

```text
manifest.json          声明（注意：include 只是文档，注册真源是 get_components()）
plugin.py              `register_plugin + 运行期状态容器
config.py              Pydantic 配置模型 → config/plugins/send-pixiv/config.toml
core/                  不依赖框架的纯逻辑（可单独测试）
  models.py            App API 字典 → 轻量数据类
  links.py             链接识别（先校验主机名，再匹配路径）
  rating.py            分级判定与过滤
  client.py            OAuth + App API 异步客户端
  cache.py             图片落盘缓存
  token.py             轮换后的 refresh_token 持久化
  policy.py            白名单 / 冷却 / 配额 / 去重器
services/              PixivService：检索 → 过滤 → 下载 → 发送
tools/                 pixiv_search（查，不发）
actions/               send_pixiv_illust（发，副作用）
handlers/              pixiv_link_watcher（消息事件 → 自动发图）
```

为什么 Action 和 Tool 要分开：Neo-MoFox 的约定是 **Tool 偏查询、Action 偏副作用**，
"发消息"属于副作用。所以"找图并发送"是 Action，"看看有哪些图"是 Tool。

## 3. 关键取舍

### 3.1 不用第三方 pixiv 库

`PixivPy` 是同步的、依赖 `requests`，且停更风险高。App API 的调用面其实很窄
（认证 + 4 个端点 + 图片下载），自己写不到 400 行，换来：全异步、零依赖、
限流与重试策略自己说了算。

### 3.2 refresh_token 必须落盘

pixiv 的 `grant_type=refresh_token` 会**轮换** refresh_token：新的一发下来，
旧的立刻失效。所以：

- 配置文件里的 `refresh_token` 只是初始值（bootstrap）；
- 轮换结果写到 `data/send-pixiv/token.json`，运行期优先用它；
- 只给 `access_token` 也能跑，但过期后无从刷新。

这一点如果做错，表现是"用一段时间突然 401，重启也没用"。

### 3.3 图片用 base64 发，不用本地路径

框架的 `send_api.send_media()` 只对 `http(s)://` 与 `file://` 前缀的入参做
"读字节 → 转 base64" 处理，**裸路径会被当成 base64 正文**（本机 `jmcomic_plugin`
的 `send_image(str(image_path))` 就是这种写法）。而 `file://` 在 Windows 上还要过
`url2pathname`，行为不稳。所以这里自己读文件、自己 base64，路径问题彻底消失。

### 3.4 耗时操作丢后台

事件总线对处理器有时限，Action 也可能被上层超时打断。因此：

- `PixivLinkWatcher` 只做"快速判断"，命中就 `task_manager` 起后台任务，
  立刻返回 `PASS`（不是 `STOP`，不拦别人的消息）。
- `SendPixivIllustAction` 把"解析"留在前台（这样链接不对、没结果、
  被冷却都能马上回一句人话），"下载 + 发送"丢后台。

后台任务统一走 `task_manager.create_task(..., daemon=True, group_name="send-pixiv")`，
不用 `asyncio.create_task`（框架规范要求）。

### 3.5 Service 里不存状态

`service_api.get_service()` **每次都新建实例**，把 HTTP 客户端或缓存放在 Service
实例字段里会随取随丢。所以运行期状态全部挂在插件实例上，Service 只是个无状态门面。

### 3.6 分级判定取更严格的一方

`x_restrict`（0/1/2）与 `sanity_level`（2/4/6）偶尔不同步，判定时取更严格的那个：

```text
r18g  ← x_restrict >= 2 或 sanity_level >= 6
r18   ← x_restrict >= 1 或 sanity_level >= 4
safe  ← 其余
```

配置值写错（比如 `default_rating = "随便写的"`）一律按 `safe` 处理——失败要往安全侧倒。

### 3.7 分级的双重过滤

服务端过滤与本地过滤都要做：

- `include_r18=False` 时请求带 `filter=for_ios`，让 pixiv 直接不给 R-18；
- 无论服务端给不给，本地一律再按分级过滤一次（账号设置、接口行为都可能变）。

### 3.8 自动发图的回环防护

插件发出的说明文字里带作品链接。万一 bot 自己发出的消息又回流成入站消息，
就会形成"链接 → 发图 → 链接"的死循环。防护有两层：

1. 发送成功后主动把该作品写进去重器（`mark_sent`），同一窗口内再遇到即跳过；
2. 发送失败时**撤销**去重记录（`forget`），避免用户白贴一次链接还看不到反应。

## 4. 已知限制

- 动图（ugoira）不合成 GIF，默认跳过；要支持得下载 zip 帧再调 ffmpeg，属于另一个话题。
- 小说（novel）识别得到但明确提示"暂不支持"。
- 排行榜的 `popular_desc` 排序与部分会员专属榜单需要会员账号，普通账号会返回空。
- 只给了 `access_token` 时无法自动续期。
- 不支持从 `i.pximg.net` 直链发图（需要额外处理 Referer 与账号可见性）。

## 5. 与框架版本的关系

本插件在 `CORE_VERSION = 1.3.0-alpha.1` 的 bot 上开发与验证，
`manifest.json` 声明：

```json
"api_version": { "log_api": "1.0.0", "send_api": "1.1.0" }
```

只声明真正 import 的两个模块。规则是"主版本必须相等、核心次版本不得低于插件要求"，
声明多了只会把兼容范围白白收窄。

## 6. 测试策略

`core/` 目录被刻意设计成**不 import 任何框架模块**，所以这部分的单元测试用纯标准库
（`unittest`）就能跑，不需要 bot 环境、也不碰网络：

- `tests/test_links.py` —— 各种链接形态，含 `evil-pixiv.net` 这类域名陷阱
- `tests/test_rating.py` —— 分级判定矩阵与"配置写错要往安全侧倒"
- `tests/test_policy.py` —— 白黑名单优先级、冷却、配额、去重器
- `tests/test_models.py` —— App API 字典解析（多图/单图/缺字段/AI/动图）
- `tests/test_client.py` —— 用 `httpx.MockTransport` 顶掉网络，验证认证头、
  token 轮换落盘、`filter=for_ios` 开关、401 重试、404 抛出、图片下载的 Referer

组件的装配正确性由 `scripts/check_manifest.py` 静态校验（manifest 声明与代码里的
`name` 属性是否对得上），运行期行为则在 bot 里实测。
