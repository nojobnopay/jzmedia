"""内网 HTTP Range 文件服务（指导 §12/§32）：给 ffprobe/ffmpeg 读远程媒体。

- 仅监听 127.0.0.1（`MEDIA_PROXY_HOST` 可覆盖，供 compose internal 网络场景）；
  无鉴权，禁止对外暴露（约束 6：rclone/代理不得出现在公网）。
- 不落盘：请求期间持有一个后端读句柄（SMB 长句柄顺序读，避免逐块 open 开销）。
- URL: `http://<host>:<port>/v1/l{library_id}/{quote(rel)}`（rel 为库内相对路径）。
- Range: `bytes=a-b` / `a-` / `-suffix`；206/416/HEAD；1MB 块流式输出。
- 失败映射：404 NotFound / 400 InvalidPath / 403 Denied / 503 Offline / 500 其它。
"""
from __future__ import annotations

import os
import secrets as _secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote, urlsplit

from ..log import get_logger
from .base import (
    StorageDenied, StorageError, StorageInvalidPath, StorageNotFound, StorageOffline,
)

logger = get_logger("storage.httpproxy")

CHUNK = 1 << 20
_PREFIX = "/v1/l"      # 持久库：/v1/l<library_id>/<rel>
_DPREFIX = "/v1/d/"    # 临时（诊断用）：/v1/d/<token>/<rel>

_lock = threading.Lock()
_server: ThreadingHTTPServer | None = None
_host = ""
_tokens: dict[str, tuple[float, object]] = {}
_TOKEN_TTL = 600.0


def ensure_server() -> str:
    """懒启动单例服务，返回 `http://host:port`。线程安全。"""
    global _server, _host
    with _lock:
        if _server is None:
            _host = os.getenv("MEDIA_PROXY_HOST", "127.0.0.1").strip() or "127.0.0.1"
            httpd = ThreadingHTTPServer((_host, 0), _Handler)
            httpd.daemon_threads = True
            threading.Thread(target=httpd.serve_forever, daemon=True,
                             name="jzmedia-media-proxy").start()
            _server = httpd
            logger.info("内网媒体代理已启动 %s:%s", _host, httpd.server_address[1])
        return f"http://{_host}:{_server.server_address[1]}"


def url_for(library_id, rel: str) -> str:
    """库内相对路径 → 代理 URL（供 ffprobe/ffmpeg 输入）。"""
    base = ensure_server()
    return f"{base}{_PREFIX}{int(library_id)}/{quote(str(rel or '').strip('/'), safe='/')}"


def url_for_backend(backend, rel: str) -> str:
    """临时注册后端并返回 URL（诊断/预检用：尚无库行或库行 id 不可用时）。

    令牌随机、TTL 自动过期；不落库、不写盘（仅进程内存）。
    """
    base = ensure_server()
    token = _secrets.token_urlsafe(16)
    now = time.time()
    with _lock:
        for k in [k for k, (exp, _b) in _tokens.items() if exp <= now]:
            _tokens.pop(k, None)
        _tokens[token] = (now + _TOKEN_TTL, backend)
    return f"{base}{_DPREFIX}{token}/{quote(str(rel or '').strip('/'), safe='/')}"


def shutdown() -> None:
    """进程退出/测试复位：停服务并清空单例。"""
    global _server, _host
    with _lock:
        if _server is not None:
            try:
                _server.shutdown()
                _server.server_close()
            except OSError as e:
                logger.debug("媒体代理关闭异常: %s", e)
        _server = None
        _host = ""
        _tokens.clear()


def _parse_path(raw: str) -> tuple[str, str, str]:
    """代理路径 → (kind, key, rel)：kind=l（库 id）/d（临时令牌）。"""
    path = urlsplit(raw).path
    if path.startswith(_DPREFIX):
        rest = path[len(_DPREFIX):]
        token, _, rel = rest.partition("/")
        if not token:
            raise StorageNotFound("bad proxy path")
        return "d", token, unquote(rel)
    if not path.startswith(_PREFIX):
        raise StorageNotFound("bad proxy path")
    rest = path[len(_PREFIX):]
    lid_s, _, rel = rest.partition("/")
    try:
        lid = int(lid_s)
    except ValueError as e:
        raise StorageNotFound("bad proxy path") from e
    return "l", str(lid), unquote(rel)


def parse_range(header: str | None, size: int) -> tuple[int, int, int]:
    """(start, end, status)；非法语法退回 200，越界返回 416。"""
    if size <= 0:
        return 0, -1, 200
    if not header or not header.startswith("bytes="):
        return 0, size - 1, 200
    spec = header[6:].split(",")[0].strip()
    first, _, last = spec.partition("-")
    try:
        if first:
            start = int(first)
            end = int(last) if last else size - 1
        elif last:
            start = max(0, size - int(last))
            end = size - 1
        else:
            return 0, size - 1, 200
    except ValueError:
        return 0, size - 1, 200
    if start >= size or start < 0 or end < start:
        return 0, size - 1, 416
    return start, min(end, size - 1), 206


_parse_range = parse_range   # 兼容旧名（测试/内部引用）


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):   # 默认 stderr 噪音关闭，降为 debug
        logger.debug("media-proxy %s", fmt % args)

    def do_HEAD(self):
        self._serve(head=True)

    def do_GET(self):
        self._serve(head=False)

    def _serve(self, head: bool) -> None:
        try:
            kind, key, rel = _parse_path(self.path)
            backend = _backend_for(kind, key)
            st = backend.stat(rel)
            if st.is_dir:
                self._error(404, "is a directory")
                return
            start, end, status = _parse_range(self.headers.get("Range"), st.size)
            if status == 416:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{st.size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            length = max(0, end - start + 1)
            self.send_response(status)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{st.size}")
            self.end_headers()
            if head or length <= 0:
                return
            with backend.open_read(rel) as fh:
                fh.seek(start)
                left = length
                while left > 0:
                    data = fh.read(min(left, CHUNK))
                    if not data:
                        break
                    self.wfile.write(data)
                    left -= len(data)
        except StorageNotFound:
            self._error(404, "not found")
        except StorageInvalidPath:
            self._error(400, "invalid path")
        except StorageDenied:
            self._error(403, "permission denied")
        except StorageOffline as e:
            logger.warning("媒体代理源离线: %s", e)
            self._error(503, "source offline")
        except StorageError as e:
            logger.warning("媒体代理读取失败: %s", e)
            self._error(500, "storage error")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:   # 防御：单个请求异常不能杀死服务
            logger.warning("媒体代理内部错误: %s", e)
            self._error(500, "internal error")

    def _error(self, status: int, message: str) -> None:
        try:
            body = message.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


def _backend_for(kind: str, key: str):
    if kind == "d":
        with _lock:
            hit = _tokens.get(key)
        if not hit or hit[0] <= time.time():
            raise StorageNotFound("diagnostic proxy token expired")
        return hit[1]
    from .factory import backend_for   # 延迟导入防环
    return backend_for(int(key))
