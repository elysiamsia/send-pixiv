"""图片落盘缓存。

同一个作品被反复请求时不必重复下载：按 `<illust_id>/<page>.<ext>` 存盘，
命中就直接复用。缓存目录由配置决定，超量时按修改时间淘汰最旧的文件。
"""

from __future__ import annotations

import time
from pathlib import Path

__all__ = ["ImageCache"]


class ImageCache:
    """一个非常朴素的图片文件缓存。

    Attributes:
        root: 缓存根目录。
        max_files: 保留的最大文件数，<= 0 表示不限制。
    """

    SUFFIX_FALLBACK = ".jpg"

    def __init__(self, root: Path, max_files: int = 500) -> None:
        """初始化。

        Args:
            root: 缓存根目录。
            max_files: 最多保留多少个文件，超出后按最旧优先删除。
        """
        self.root = Path(root)
        self.max_files = max_files

    def ensure_root(self) -> None:
        """确保缓存目录存在。"""
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, illust_id: int, page: int = 0, suffix: str = "") -> Path:
        """计算某个作品某一页的缓存路径。

        Args:
            illust_id: 作品 ID。
            page: 页码，从 0 开始。
            suffix: 文件后缀，需以点开头；留空时用 `SUFFIX_FALLBACK`。

        Returns:
            目标文件路径。
        """
        safe_suffix = suffix if suffix.startswith(".") and len(suffix) <= 5 else self.SUFFIX_FALLBACK
        return self.root / str(illust_id) / f"{page}{safe_suffix}"

    def find(self, illust_id: int, page: int = 0) -> Path | None:
        """查找已经下载好的某一页。

        Args:
            illust_id: 作品 ID。
            page: 页码。

        Returns:
            命中的文件路径；未命中返回 `None`。
        """
        directory = self.root / str(illust_id)
        if not directory.is_dir():
            return None
        for candidate in sorted(directory.glob(f"{page}.*")):
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
        return None

    def touch(self, path: Path) -> None:
        """更新文件的访问时间，避免热点文件被淘汰。

        Args:
            path: 已存在的文件。
        """
        try:
            path.touch()
        except OSError:
            pass

    def prune(self) -> int:
        """按最旧优先删除，把文件数压到 `max_files` 以内。

        Returns:
            实际删除的文件数。
        """
        if self.max_files <= 0 or not self.root.is_dir():
            return 0

        files = [item for item in self.root.rglob("*") if item.is_file()]
        if len(files) <= self.max_files:
            return 0

        files.sort(key=lambda item: _mtime(item))
        removed = 0
        for item in files[: len(files) - self.max_files]:
            try:
                item.unlink()
                removed += 1
            except OSError:
                continue
        return removed

    def size_bytes(self) -> int:
        """缓存占用的总字节数。"""
        if not self.root.is_dir():
            return 0
        return sum(_size(item) for item in self.root.rglob("*") if item.is_file())


def _mtime(path: Path) -> float:
    """安全地读取 mtime，失败时排到最后。"""
    try:
        return path.stat().st_mtime
    except OSError:
        return time.time()


def _size(path: Path) -> int:
    """安全地读取文件大小。"""
    try:
        return path.stat().st_size
    except OSError:
        return 0
