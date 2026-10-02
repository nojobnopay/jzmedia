"""同版本 HTML 帮助站：仅托管构建产物，不读取文档源码或业务数据。"""

import json
import os
import re
from functools import lru_cache
from pathlib import Path

from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .log import get_logger


HELP_DIST = Path(__file__).resolve().parent.parent / "docs" / ".vitepress" / "dist"
_HASH = re.compile(r"sha256-[A-Za-z0-9+/]{43}=")
_logger = get_logger("help_site")


@lru_cache(maxsize=4)
def _read_hashes(path: str, modified: int, size: int) -> tuple[str, ...]:
    # mtime/size 构成缓存键，宿主重新构建后不需要重启服务。
    try:
        with open(path, encoding="utf-8") as source:
            values = json.load(source)["scriptHashes"]
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not _HASH.fullmatch(value) for value in values
        ):
            raise ValueError("invalid script hashes")
        return tuple(sorted(set(values)))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        _logger.warning("帮助站 CSP 清单无法读取: %s: %s", path, exc)
        return ()


class HelpFiles(StaticFiles):
    def __init__(self, directory=HELP_DIST):
        super().__init__(directory=directory, html=True, check_dir=False)

    async def __call__(self, scope, receive, send):
        if not os.path.isfile(os.path.join(self.directory, "index.html")):
            response = HTMLResponse(
                '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>帮助文档尚未构建 · JZMedia</title>'
                '<main><h1>帮助文档尚未构建</h1>'
                '<p>请管理员在项目的 docs 目录运行 '
                '<code>npm ci &amp;&amp; npm run build</code>，或使用包含帮助文档的新镜像。</p>'
                '<p><a href="/">返回媒体库</a></p></main></html>',
                status_code=503, headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return
        await super().__call__(scope, receive, send)

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    def csp(self, default: str) -> str:
        path = os.path.join(self.directory, "csp-hashes.json")
        try:
            stat = os.stat(path)
        except FileNotFoundError:
            return default
        except OSError as exc:
            _logger.warning("帮助站 CSP 清单不可访问: %s", exc)
            return default
        hashes = _read_hashes(path, stat.st_mtime_ns, stat.st_size)
        if not hashes:
            return default
        return default.replace("script-src 'self'", "script-src 'self' " +
                               " ".join(f"'{value}'" for value in hashes), 1)
