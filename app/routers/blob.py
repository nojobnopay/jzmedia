"""媒体直发（本地 FileResponse / 远程 Range 流式）——详情页与剧集 blob 共用。

- 本地/挂载后端：FileResponse 原样直发（Range/条件请求由 Starlette 处理）；
- 远程直读后端：StorageBackend 流式代理（1MB 块 + Content-Range，浏览器可拖进度），
  凭据只存在于 Storage 层，绝不进 URL/响应（约束 7）。
"""
from __future__ import annotations

import mimetypes
import os
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from .. import storage
from ..storage import httpproxy

__all__ = ['media_response', 'text_response', 'guess_media_type']

_CHUNK = 1 << 20


def _stat_or_http(backend, rel: str):
    try:
        st = backend.stat(rel)
    except storage.StorageNotFound:
        raise HTTPException(404, "file missing")
    except storage.StorageOffline as e:
        raise HTTPException(503, f"source offline: {e}")
    except storage.StorageDenied as e:
        raise HTTPException(403, str(e))
    except storage.StorageError as e:
        raise HTTPException(500, f"storage error: {e}")
    if st.is_dir:
        raise HTTPException(404, "is a directory")
    return st


def _disposition(filename: str, inline: bool) -> str:
    kind = "inline" if inline else "attachment"
    name = os.path.basename(filename or "")
    return f"{kind}; filename*=UTF-8''{quote(name)}"


def media_response(request: Request, backend, rel: str, *, filename: str = "",
                   media_type: str | None = None, inline: bool = False):
    """统一媒体响应：Range → 206/416；本地走 FileResponse，远程走流式代理。"""
    st = _stat_or_http(backend, rel)
    name = filename or os.path.basename(backend.norm(rel))
    local = None
    try:
        local = backend.abs_path(rel)
    except storage.StorageError:
        local = None
    if local:
        return FileResponse(local, media_type=media_type,
                            filename=None if inline else name,
                            headers={"X-Content-Type-Options": "nosniff"})
    start, end, status = httpproxy.parse_range(request.headers.get("range"), st.size)
    headers = {"Accept-Ranges": "bytes", "X-Content-Type-Options": "nosniff",
               "Content-Disposition": _disposition(name, inline)}
    if status == 416:
        headers["Content-Range"] = f"bytes */{st.size}"
        return Response(status_code=416, headers=headers)
    length = max(0, end - start + 1)
    headers["Content-Length"] = str(length)
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{st.size}"

    def _iter():
        with backend.open_read(rel) as fh:
            fh.seek(start)
            left = length
            while left > 0:
                data = fh.read(min(left, _CHUNK))
                if not data:
                    break
                left -= len(data)
                yield data

    return StreamingResponse(_iter(), status_code=status, headers=headers,
                             media_type=media_type or "application/octet-stream")


def text_response(backend, rel: str, limit: int = 64 * 1024):
    """前 N 字节文本预览（srt/nfo/剧本）；远程/本地同一接口。"""
    try:
        chunk = backend.read(rel, 0, limit)
    except storage.StorageNotFound:
        raise HTTPException(404, "file missing")
    except storage.StorageOffline as e:
        raise HTTPException(503, f"source offline: {e}")
    except storage.StorageError as e:
        raise HTTPException(500, f"read failed: {e}")
    try:
        text = chunk.decode("utf-8")
    except UnicodeDecodeError:
        text = chunk.decode("gbk", errors="replace")
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(text, headers={"X-Content-Type-Options": "nosniff"})


def guess_media_type(rel: str) -> str:
    return mimetypes.guess_type(rel)[0] or "application/octet-stream"
