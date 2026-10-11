"""send-pixiv 核心实现。

这里放不依赖 Neo-MoFox 运行时的纯逻辑与网络客户端，便于单独测试：

- `models`     —— Pixiv App API 返回值的轻量数据模型
- `links`      —— pixiv 链接识别与解析
- `rating`     —— 作品分级判定与过滤
- `client`     —— App API 异步客户端（OAuth + 各端点）
- `cache`      —— 图片落盘缓存
- `policy`     —— 群白名单、冷却与配额
"""

from __future__ import annotations

__all__: list[str] = []
