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


## 7. 联网实测修正（2026-10-12）

拿到真账号跑通之后，实测推翻了几个"照着文档写"的假设。记在这里，免得以后有人再踩。

### 7.1 `sanity_level` 是噪音，分级只能看 `x_restrict`

原实现按文档写的 `sanity_level`（2 一般 / 4 R-18 / 6 R-18G）参与判定。用 60 条真实样本、
拿 pixiv 自己的 `R-18` / `R-18G` 标签当真相去对：

| 判定依据 | 与标签真相不一致 |
| --- | --- |
| 含 `sanity_level` 的旧规则 | **33 / 60** |
| 只用 `x_restrict` | **0 / 60** |

App API 返回的 `sanity_level` 会给出 0 / 2 / 4 / 6，**但和作品实际分级对不上**：
正常风景图也会是 4 或 6，而真正的 R-18 作品 `x_restrict` 一定是 1。

现在的规则是"主看 `x_restrict`，标签兜底"：

```text
r18g ← x_restrict >= 2 或标签含 R-18G
r18  ← x_restrict >= 1 或标签含 R-18
safe ← 其余
```

### 7.2 `filter=for_ios` 挡不住 R-18，本地过滤才是真防线

原以为服务端参数 `filter=for_ios` 能直接过滤掉 R-18。实测：**带不带它返回的东西一样**，
20 条里照样有 13 条 R-18。所以本地那一道过滤不是"双保险"，而是**唯一保险**。
（这也反过来说明：`safe` 会话能挡住 R-18，靠的是我们自己的 `filter_by_mode`。）

### 7.3 三个图片档位的真实面目

| 档位 | 实测 | 结论 |
| --- | --- | --- |
| `large` | `c/600x1200_90_webp/...` 的 **WebP 缩略图**，16~150 KB | 太小，只作回退 |
| `master1200` | 约 1200px 的 **JPEG**，780 KB~1.1 MB | **默认档位** |
| `original` | PNG 原图，2~10 MB | 聊天太重，只作回退 |

推导 master1200 的关键细节：**后缀永远是 `.jpg`**。
`img-original/..._p0.png` → `img-master/..._p0_master1200.jpg`，
写成 `_master1200.png` 一律 404——**哪怕原图是 PNG**。

而且它并非对所有作品都存在，所以下载走的是回退链 `master1200 → large → original`。

### 7.4 缓存必须按档位分区

改完 `image_quality` 却发现发出来的还是旧档位——因为缓存命中不看档位。
现在缓存路径是 `<作品ID>/<档位>/<页码><后缀>`，
并且**后缀按 URL 推断**（`_webp/` 路径存成 `.webp`），不再出现"文件名说 .jpg、内容却是 WebP"。

### 7.5 顺带确认的两件事

- **凭据轮换**：`refresh_token` 确实会轮换，落盘逻辑按预期工作。
- **失败隔离**：说明文字发送失败时只记一条 debug 日志，图片照发——实测中意外验证过一次。



## 8. 加载期硬约束：Action 的 `associated_types`（2026-10-12 事故）

第一次装进 bot 实测时炸了，报错长这样：

```text
AttributeError: 'NeoChatterPlugin' object has no attribute 'rating_mode_for'
  File ".../plugins/send-pixiv/tools/pixiv_search_tool.py", line 54
```

**这个报错和根因隔了三层**。真正的根因在 bot 日志里：

```text
[02:41:14] 控制台 | ERROR | 插件 'send-pixiv' 加载失败:
           Action 'send_pixiv_illust' 的 associated_types 必须是非空 list
```

### 事故链条

1. `send_pixiv_illust` 没有声明 `associated_types`（默认空列表）。
2. `plugin_manager._register_components` 在注册 Action 前会调用
   `component_cls.validate_associated_types()`，**这个调用在 try 块之外**——
   抛出的异常直接冒泡出去，**整个插件的组件注册流程中断**。
3. 后果是**连锁的**：Action 没注册、排在它后面的 EventHandler 也没注册，
   插件实例也没进 `_loaded_plugins`。
   （bot 自己的思考日志里都写了"那个动作不在我的工具列表里"——它是对的。）
4. 框架实例化 LLM 组件时按"组件签名 → 归属插件"解析，
   而 `BaseChatter._resolve_component_plugin` 在**解析不到时会兜底成当前 chatter 的插件**。
   于是 Tool 里的 `self.plugin` 变成了 `NeoChatterPlugin`，
   调用 `plugin.rating_mode_for(...)` 就报出了那个莫名其妙的 AttributeError。

### 两条修复

**① 补上 `associated_types = ["image"]`。**
取值没有固定词表（校验器只要求非空字符串列表），惯例是
`["image"]` / `["text"]` / `["emoji"]` / `["file", "media"]`。
运行时它还会被 `chat_context.check_types()` 用来过滤：
当前聊天流不支持的类型会把 Action 剔除，所以别乱写。

**② 组件不再直接信 `self.plugin`。**
统一走 `core/runtime.py` 的 `resolve_own_plugin()`：先按插件名从注册表取，
取不到再退回注入值，**两者都不是本插件就返回 None 并给出可读报错**。
这样下次再有加载失败，看到的是一句人话，而不是隔了三层的 AttributeError。

### 教训

- **静态校验器要覆盖框架的加载期校验。** 这次 `scripts/check_manifest.py`
  放过了它，现在补了"Action / Agent 必须有非空 associated_types"这条规则。
- **排查插件问题先翻日志。** 报错位置（Tool 第 54 行）和根因（Action 少个字段）
  隔了十万八千里，只有 `logs/` 里那句"插件加载失败"才是真线索。
- 判断插件到底加载没有，看两处：`_loaded_plugins` 里有没有它、
  `logs/` 里有没有 `加载失败`。

