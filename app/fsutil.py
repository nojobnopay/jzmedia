"""文件原子写小工具（评审 B5a-6）。

临时文件与目标同目录 + os.replace：目标要么保持旧内容、要么替换为新内容，
绝不出现半成品（崩溃/磁盘满/中断都不会污染缓存与 NFO）。
"""
import os


def atomic_write_bytes(dest: str, data: bytes) -> None:
    tmp = dest + ".tmp"
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, dest)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(dest: str, text: str, encoding: str = "utf-8") -> None:
    atomic_write_bytes(dest, text.encode(encoding))
