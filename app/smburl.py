"""SMB 地址解析（建库/编辑连接单一输入框用）：`\\主机\共享\目录` → 三段结构化。

- 同时兼容 `//主机/共享/目录` 与 `smb://[用户@]主机/共享/目录`（percent 解码）。
- 只做语法解析，不校验主机可达；错误以中文 error 返回（路由/表单直接展示）。
- 存库仍拆成 host/share/subpath（挂载源、凭据文件、宿主挂载指引都要用）。
"""
import re

__all__ = ['parse_smb_url']

_SCHEME_RE = re.compile(r"^smb://", re.IGNORECASE)
_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _unquote(s: str) -> str:
    try:
        from urllib.parse import unquote
        return unquote(s)
    except Exception:
        return s


def parse_smb_url(raw: str) -> dict:
    """返回 {host, share, subpath, username, error}；error 非空时其余字段无意义。"""
    text = str(raw or "").strip()
    if not text:
        return {"host": "", "share": "", "subpath": "", "username": "",
                "error": "请输入服务器/共享路径，如 \\\\ServerName\\ShareName"}
    if _DRIVE_RE.match(text):
        return {"host": "", "share": "", "subpath": "", "username": "",
                "error": "这看起来是 Windows 映射盘路径；请填 NAS 的 \\\\主机\\共享\\目录，或容器的本地路径"}
    is_url = bool(_SCHEME_RE.match(text))
    body = _SCHEME_RE.sub("", text)
    username = ""
    if is_url and "@" in body.split("/", 1)[0]:
        userinfo, _, body = body.partition("@")
        username = _unquote(userinfo.split(";")[-1] or "")
    body = body.replace("\\", "/")
    segs = [s for s in body.split("/") if s]
    if len(segs) < 2:
        return {"host": segs[0] if segs else "", "share": "", "subpath": "",
                "username": username,
                "error": "还需要共享名，形如 \\\\ServerName\\ShareName"}
    host = _unquote(segs[0]) if is_url else segs[0]
    share = _unquote(segs[1]) if is_url else segs[1]
    if not host:
        return {"host": "", "share": "", "subpath": "", "username": username,
                "error": "服务器地址不能为空"}
    if not share:
        return {"host": host, "share": "", "subpath": "", "username": username,
                "error": "共享名不能为空"}
    parts = segs[2:]
    if is_url:
        parts = [_unquote(s) for s in parts]
    return {"host": host, "share": share, "subpath": "/".join(parts),
            "username": username, "error": ""}
