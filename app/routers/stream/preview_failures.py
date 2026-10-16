"""Latest unresolved preview failures, with safe messages and atomic persistence.

The caller serializes access with the preview queue lock. Pending changes survive
temporary write failures in this process; failed writes are retried at most once
every thirty seconds, independently of the automatic generation settings.
"""
import json
from pathlib import Path
import time

from fastapi import HTTPException

from ... import storage
from .preview_log import ExtractionError


_MESSAGES = {
    "timeout": "抽帧超时，请稍后重试",
    "memory": "生成缩略图时内存不足，请释放内存后重试",
    "permission": "没有读取片源或写入缩略图的权限，请检查权限",
    "option": "当前 FFmpeg 不支持所需参数，请检查 FFmpeg 版本",
    "hardware": "硬件解码不可用，请检查转码配置",
    "filter": "当前 FFmpeg 不支持所需的画面或色彩转换",
    "input": "无法读取片源，请检查文件是否完整且可访问",
    "decoder": "无法解码片源，请检查视频编码或文件是否损坏",
    "source_missing": "片源文件不存在，请检查文件位置",
    "source_offline": "片源存储离线，请恢复连接后重试",
    "item_missing": "影片记录已不存在",
    "storage": "无法访问片源存储，请检查存储连接与权限",
    "ffmpeg_missing": "未找到 FFmpeg，请检查服务器安装与路径配置",
    "disk_full": "缩略图存储空间不足，请释放空间后重试",
    "unplayable": "片源不可播放",
    "duration": "无法取得有效时长（支持 24 小时以内的视频）",
    "dolby_vision": "该 Dolby Vision 片源无可用兼容基底，暂不支持预览",
    "frame": "无法从采样位置取得预览画面",
    "tile": "无法拼接预览画面",
    "unknown": "无法生成预览，请检查片源与 FFmpeg 配置后重试",
}
_KNOWN_ERRORS = {
    "片源不可播放": "unplayable",
    "无法取得有效时长（支持 24 小时以内的视频）": "duration",
    "该 Dolby Vision 片源无可用兼容基底，暂不支持预览": "dolby_vision",
    "无法从采样位置取得预览画面": "frame",
    "无法拼接预览画面": "tile",
}


def failure_reason(error):
    """Never publish arbitrary exceptions, source URLs, or FFmpeg stderr."""
    if isinstance(error, ExtractionError):
        code = "timeout" if error.timed_out else error.error_kind
    elif isinstance(error, HTTPException):
        code = {404: "item_missing", 410: "source_missing", 503: "source_offline"}.get(
            error.status_code, "storage")
    elif isinstance(error, (storage.StorageDenied, PermissionError)):
        code = "permission"
    elif isinstance(error, storage.StorageNotFound):
        code = "source_missing"
    elif isinstance(error, storage.StorageOffline):
        code = "source_offline"
    elif isinstance(error, storage.StorageError):
        code = "storage"
    elif isinstance(error, FileNotFoundError):
        code = "ffmpeg_missing"
    elif isinstance(error, OSError) and error.errno == 28:
        code = "disk_full"
    elif isinstance(error, RuntimeError):
        code = _KNOWN_ERRORS.get(str(error), "unknown")
    else:
        code = "unknown"
    if code not in _MESSAGES:
        code = "unknown"
    return code, _MESSAGES[code]


class FailureStore:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "failures.json"
        self.pending = {}
        self.retry_at = 0.0

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(data, list):
            raise ValueError("invalid preview failures")
        records = {}
        for record in data:
            if (not isinstance(record, dict) or record.get("kind") not in ("movie", "episode", "extra")
                    or type(record.get("id")) is not int or not 0 < record["id"] < 2 ** 63
                    or type(record.get("library_id")) is not int or record["library_id"] <= 0
                    or type(record.get("failed_at")) is not int or record["failed_at"] <= 0
                    or not isinstance(record.get("error_code"), str)
                    or record["error_code"] not in _MESSAGES):
                raise ValueError("invalid preview failure record")
            # The public message comes from the fixed vocabulary even for on-disk records.
            clean = {name: record[name] for name in ("kind", "id", "library_id", "failed_at", "error_code")}
            clean["error"] = _MESSAGES[clean["error_code"]]
            records[(clean["kind"], clean["id"])] = clean
        return records

    def snapshot(self):
        records = self._read()
        for key, record in self.pending.items():
            if record is None:
                records.pop(key, None)
            else:
                records[key] = dict(record)
        return records

    def update(self, kind, item_id, record):
        if record is not None:
            record = dict(record)
            record["error"] = _MESSAGES[record["error_code"]]
        self.pending[(kind, item_id)] = record

    def flush(self):
        if not self.pending:
            return True
        now = time.monotonic()
        if now < self.retry_at:
            return False
        try:
            before = self._read()
            records = dict(before)
            for key, record in self.pending.items():
                if record is None:
                    records.pop(key, None)
                else:
                    records[key] = dict(record)
            if records != before:
                self.root.mkdir(parents=True, exist_ok=True)
                temp = self.path.with_suffix(".tmp")
                temp.write_text(json.dumps(list(records.values()), ensure_ascii=False), encoding="utf-8")
                temp.replace(self.path)
        except (OSError, ValueError, UnicodeError):
            self.retry_at = now + 30
            raise
        self.pending.clear()
        self.retry_at = 0.0
        return True
