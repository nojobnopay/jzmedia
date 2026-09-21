"""存储元数据短 TTL 缓存（直读远程库降往返；进程内存，不落盘）。

- 只缓存 `stat`/`list` 这类元数据，绝不缓存文件内容。
- 键 = `(op, path)`（path 为库内相对路径）；写路径调用 `invalidate_path*` 精确失效。
- 负缓存：`StorageNotFound` 也缓存（更短 TTL），避免反复探测不存在的 poster/NFO/字幕目录。
- TTL 由 env `SMB_META_TTL`（秒，默认 3；`0` 关闭）控制，见 `smb.META_TTL`。
- 线程安全；超过 `max_entries` 先清过期，仍满则整体清空（防无界增长）。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

__all__ = ['TTLCache']

_MISS = object()


class TTLCache:
    """极简 TTL 字典；`get` 未命中返回 `TTLCache.MISS`。"""

    MISS = _MISS

    def __init__(self, ttl: float = 3.0, max_entries: int = 4096,
                 clock: Callable[[], float] = time.monotonic):
        self.ttl = max(0.0, float(ttl or 0))
        self.neg_ttl = min(self.ttl, 2.0) if self.ttl else 0.0
        self.max_entries = max(16, int(max_entries or 16))
        self._clock = clock
        self._lock = threading.Lock()
        self._data: dict[Any, tuple[float, Any]] = {}

    @property
    def enabled(self) -> bool:
        return self.ttl > 0

    def get(self, key: Any) -> Any:
        if not self.enabled:
            return _MISS
        with self._lock:
            hit = self._data.get(key)
            if hit is None:
                return _MISS
            if hit[0] <= self._clock():
                self._data.pop(key, None)
                return _MISS
            return hit[1]

    def put(self, key: Any, value: Any, negative: bool = False) -> None:
        if not self.enabled:
            return
        ttl = self.neg_ttl if negative else self.ttl
        if ttl <= 0:
            return
        now = self._clock()
        with self._lock:
            if key not in self._data and len(self._data) >= self.max_entries:
                for k in [k for k, (exp, _v) in self._data.items() if exp <= now]:
                    self._data.pop(k, None)
                if len(self._data) >= self.max_entries:
                    self._data.clear()
            self._data[key] = (now + ttl, value)

    def drop(self, *keys: Any) -> None:
        if not keys:
            return
        with self._lock:
            for k in keys:
                self._data.pop(k, None)

    def drop_path(self, path: str, ops=("stat", "list")) -> None:
        """失效某路径及其子树的所有缓存键（删除/改名/递归清理用）。"""
        if not self.enabled:
            return
        prefix = (path or "") + "/"
        with self._lock:
            for k in [k for k in self._data
                      if (not ops or k[0] in ops)
                      and (k[1] == path or str(k[1]).startswith(prefix))]:
                self._data.pop(k, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
