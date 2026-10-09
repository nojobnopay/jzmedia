"""routers.stream.subtitles（自 app/routers/stream.py 拆分，评审 B9/R12-Q1；经 stream 门面使用）。"""
import os
import re
import hashlib
import shutil
import subprocess
import threading
from urllib.parse import quote, unquote
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from ...config import settings
from ... import library_paths
from ... import storage
from ...scanner import (sidecar_subtitles_fs, _SIDECAR_LANG_HINTS,
                        _guess_sidecar_lang)
from ... import media as _media
from ...log import get_logger
logger = get_logger("stream.subtitles")
from .common import (_ffmpeg_ok, _media_cached_or_probe, _run_ffmpeg_to_temp,
                     _version_source, router, version_cache_dir)
__all__ = ['hls_subtitle', '_sidecar_src', '_convert_sidecar', '_extract_embedded', '_FONT_RE', '_SIDECAR_LANG_HINTS', '_guess_sidecar_lang', '_sub_list', '_sub_pick', 'hls_subtitle_ass', 'hls_subtitle_sup', '_dump_attachments', 'stream_fonts', '_FONT_MIME', '_font_mime', 'stream_builtin_font', 'stream_attachment_font']

@router.get("/{version_id}/sub/{idx}.vtt")
def hls_subtitle(version_id: int, idx: int, kind: str = "movie"):
    """文字字幕 → WebVTT（浏览器 <track>；内嵌抽取/外挂转换，缓存复用）。图片字幕 415。"""
    m, src = _version_source(version_id, kind)
    info = _media_cached_or_probe(m, src)
    track = _sub_pick(m, info, idx)
    if int(track.get("image") or 0):
        raise HTTPException(415, "image subtitle (PGS/VobSub): client render or burn-in only")
    if not _ffmpeg_ok():
        raise HTTPException(501, "ffmpeg not installed in server image")
    si = int(idx)
    if track.get("source") == "sidecar":
        dest = _convert_sidecar(_sidecar_src(m, str(track.get("sidecar") or "")),
                                int(m["id"]), "vtt", kind)
    else:
        dest = _extract_embedded(src, int(m["id"]), track, si, "vtt", kind)
    return FileResponse(dest, media_type="text/vtt", filename=f"sub{si}.vtt")


def _sidecar_src(m: dict, rel: str):
    """外挂字幕 MediaSource（本地/远程统一；rel 来自服务端枚举，不接受客户端路径）。"""
    try:
        return storage.media_source(
            m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID, rel)
    except storage.StorageNotFound:
        raise HTTPException(404, "sidecar subtitle missing")
    except storage.StorageOffline as e:
        raise HTTPException(503, f"source offline: {e}")
    except storage.StorageError as e:
        raise HTTPException(500, f"storage error: {e}")


def _convert_sidecar(src, vid: int, dest_ext: str,
                     kind: str = "movie") -> str:
    """外挂字幕转换到缓存（srt→vtt/ass、ass/ssa→vtt）。按源 mtime 失效。"""
    codec = "webvtt" if dest_ext == "vtt" else "ass"
    key = hashlib.blake2b((src.rel + "|" + dest_ext).encode("utf-8"),
                          digest_size=6).hexdigest()   # 评审 B6/R11-B6：不用 SHA-1
    sdir = os.path.join(version_cache_dir(kind, vid), "subs")
    os.makedirs(sdir, exist_ok=True)
    dest = os.path.join(sdir, f"side_{key}.{dest_ext}")
    try:
        fresh = os.path.isfile(dest) and os.path.getmtime(dest) > float(src.mtime or 0)
    except OSError:
        fresh = False
    if not fresh:
        _run_ffmpeg_to_temp(
            dest,
            lambda tmp: [_media.ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
                         "-i", src.input, "-c:s", codec, tmp],
            timeout=120, err_msg="subtitle convert failed")
    return dest


def _extract_embedded(src, vid: int, track: dict, si: int, dest_ext: str,
                      kind: str = "movie") -> str:
    """内嵌字幕抽取到缓存（vtt→webvtt 转换；ass→ASS；sup→PGS 流拷贝）。按源 mtime 失效。
    `src` 为 MediaSource：输入可为本地路径或内网 URL（远程直读），mtime 来自后端 stat。"""
    codec = {"vtt": "webvtt", "ass": "ass"}.get(dest_ext, "copy")
    sdir = os.path.join(version_cache_dir(kind, vid), "subs")
    os.makedirs(sdir, exist_ok=True)
    dest = os.path.join(sdir, f"{si}.{dest_ext}")
    try:
        fresh = os.path.isfile(dest) and os.path.getmtime(dest) > float(src.mtime or 0)
    except OSError:
        fresh = False
    if not fresh:
        ff_idx = track.get("ff_index", si)
        _run_ffmpeg_to_temp(
            dest,
            lambda tmp: [_media.ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
                         "-i", src.input, "-map", f"0:{ff_idx}", "-c:s", codec, tmp],
            timeout=180 if dest_ext == "sup" else 120, err_msg="subtitle extract failed")
    return dest


_FONT_RE = re.compile(r"^[^/\\]{1,120}\.(?:ttf|otf|ttc|woff2?)$", re.IGNORECASE)


def _sub_list(m: dict, info: dict) -> list[dict]:
    """播放器字幕轨清单：内嵌（ffprobe 缓存）+ 正片同名外挂（实时枚举磁盘）。
    索引即前端 subIdx；图片 codec 归一为 pgs/vobsub；外挂带 source/sidecar。
    自动默认：片源无任何内嵌字幕时，第一条中文文本外挂标 default=1；
    无中文外挂再兜底第一条文本外挂（用户 2026-09：仅上传了 大桥下面.srt 也应自动加载）；
    图片外挂不自动选，避免意外触发烧录重编。"""
    out: list[dict] = []
    for t in (info.get("subs") or []):
        t2 = dict(t or {})
        codec = _media.norm_codec(str(t2.get("codec") or ""))
        t2["codec"] = codec
        t2["image"] = 1 if (int(t2.get("image") or 0) or codec in _media.IMAGE_SUBS) else 0
        t2["source"] = "embedded"
        out.append(t2)
    try:
        backend = storage.backend_for(
            m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
        side = sidecar_subtitles_fs(backend, m.get("file_path") or "")
    except Exception as e:
        logger.debug("sidecar list failed file=%s: %s", m.get("file_path"), e)
        side = []
    has_embedded = bool(info.get("subs"))
    auto_done = False
    side_items: list[dict] = []
    for s in side:
        lang, title = _guess_sidecar_lang(str(s.get("suffix") or ""))
        item = {"index": len(out), "ff_index": None, "codec": s.get("codec") or "",
                "image": int(s.get("image") or 0), "lang": lang,
                "title": title or s.get("name") or "", "default": 0, "forced": 0,
                "source": "sidecar", "sidecar": s.get("rel") or ""}
        if (not has_embedded and not auto_done and not item["image"] and lang == "chi"):
            item["default"] = 1
            auto_done = True
        side_items.append(item)
        out.append(item)
    if not has_embedded and not auto_done:
        for item in side_items:
            if not item["image"]:
                item["default"] = 1
                break
    for i, t in enumerate(out):
        t["index"] = i
    return out


def _sub_pick(m: dict, info: dict, idx) -> dict:
    """按索引取字幕轨（内嵌+外挂合并清单）；越界 404。"""
    subs = _sub_list(m, info)
    try:
        si = int(idx)
    except (TypeError, ValueError):
        raise HTTPException(422, "bad subtitle index")
    if not (0 <= si < len(subs)):
        raise HTTPException(404, "subtitle not found")
    return subs[si]


@router.get("/{version_id}/sub/{idx}.ass")
def hls_subtitle_ass(version_id: int, idx: int, request: Request, kind: str = "movie"):
    """ASS/SSA 原始文件（JASSUB WASM/libass 客户端渲染，保留字体/位置/动画）。
    外挂 ass/ssa 直接服务（本地 FileResponse / 远程 Range 流式）；外挂 srt 转 ASS；
    文本类非 ASS 415；图片字幕 415（客户端渲染/烧录）。"""
    m, src = _version_source(version_id, kind)
    info = _media_cached_or_probe(m, src)
    track = _sub_pick(m, info, idx)
    if int(track.get("image") or 0):
        raise HTTPException(415, "image subtitle: client render or burn-in only")
    codec = _media.norm_codec(str(track.get("codec") or ""))
    if codec not in _media.ASS_SUBS and codec not in ("srt", "subrip", "webvtt", "vtt"):
        raise HTTPException(415, f"not an ass/ssa subtitle: {codec or 'unknown'}")
    if not _ffmpeg_ok():
        raise HTTPException(501, "ffmpeg not installed in server image")
    si = int(idx)
    if track.get("source") == "sidecar":
        side = _sidecar_src(m, str(track.get("sidecar") or ""))
        if codec in _media.ASS_SUBS:
            from ..blob import media_response
            return media_response(request, side.backend, side.rel,
                                  media_type="text/x-ssa",
                                  filename=os.path.basename(side.rel))
        dest = _convert_sidecar(side, int(m["id"]), "ass", kind)
        return FileResponse(dest, media_type="text/x-ssa", filename=f"sub{si}.ass")
    dest = _extract_embedded(src, int(m["id"]), track, si, "ass", kind)
    return FileResponse(dest, media_type="text/x-ssa", filename=f"sub{si}.ass")


@router.get("/{version_id}/sub/{idx}.sup")
def hls_subtitle_sup(version_id: int, idx: int, request: Request, kind: str = "movie"):
    """PGS 原始 .sup（libpgs 浏览器端解码渲染，零转码）：内嵌 `-c:s copy` 抽取缓存；
    外挂 .sup 直接服务（本地 FileResponse / 远程 Range 流式）；非 PGS 415（VobSub 只能烧录）。"""
    m, src = _version_source(version_id, kind)
    info = _media_cached_or_probe(m, src)
    track = _sub_pick(m, info, idx)
    codec = _media.norm_codec(str(track.get("codec") or ""))
    if codec != "pgs":
        raise HTTPException(415, f"not a pgs subtitle: {codec or 'unknown'}")
    if track.get("source") == "sidecar":
        side = _sidecar_src(m, str(track.get("sidecar") or ""))
        from ..blob import media_response
        return media_response(request, side.backend, side.rel,
                              media_type="application/x-pgs",
                              filename=os.path.basename(side.rel))
    if not _ffmpeg_ok():
        raise HTTPException(501, "ffmpeg not installed in server image")
    si = int(idx)
    dest = _extract_embedded(src, int(m["id"]), track, si, "sup", kind)
    return FileResponse(dest, media_type="application/x-pgs", filename=f"sub{si}.sup")


_dump_locks: dict[str, threading.Lock] = {}
_dump_locks_guard = threading.Lock()


def _dump_lock_for(fdir: str) -> threading.Lock:
    """按字体缓存目录串行 dump（JASSUB 一次取几十个字体：无锁时并发请求各跑一次全量 dump）。"""
    key = os.path.normpath(fdir or "")
    with _dump_locks_guard:
        lk = _dump_locks.get(key)
        if lk is None:
            lk = threading.Lock()
            _dump_locks[key] = lk
        return lk


def _font_dump_targets(attachments) -> list[tuple[int, str, str]]:
    """附件清单 → 显式 dump 目标 [(流序号, 安全临时名, 真实文件名)]。

    ffmpeg ≥7 对 `-dump_attachment:t ""`（按内嵌文件名落盘）做安全名检查，
    中文/括号文件名直接拒绝（`Filename ... is unsafe`，未麻的部屋事故）；
    逐流显式指定输出则不受限（ffmpeg 7.1 实测单遍多路可用）。

    `index` 取自 ffprobe 附件流序号（与 ffmpeg 流定位符一致，md5 跨版本核对过）。"""
    targets: list[tuple[int, str, str]] = []
    for a in (attachments or []):
        name = os.path.basename(str((a or {}).get("name") or ""))
        if not _FONT_RE.match(name):
            continue
        try:
            idx = int((a or {}).get("index", -1))
        except (TypeError, ValueError):
            continue
        if idx < 0:
            continue
        ext = os.path.splitext(name)[1].lower() or ".ttf"
        targets.append((idx, f"att{idx}{ext}", name))
    return targets


def _dump_attachments(input_url: str, fdir: str, attachments=None) -> None:
    """一次性 dump 全部附件到 fdir（MKV 字体给 JASSUB 用）。
    `input_url` 可为本地路径或内网 URL（远程直读）。
    有附件清单时逐流显式输出（绕过新版 ffmpeg 的内嵌文件名安全检查），
    落盘后再改名为真实文件名；无清单时回退整批按内嵌文件名落盘（旧版兼容）。
    ffmpeg 以附件元数据文件名落盘且相对 CWD：在独立 .dump 子目录执行，只回收
    合法字体名（basename + 字体扩展），其余（路径逃逸/非字体）丢弃。成功
    （至少解出一个文件，或整批 rc==0）写 .dumped 标记，失败不写（下次重试）。"""
    marker = os.path.join(fdir, ".dumped")
    if os.path.isfile(marker):
        return
    with _dump_lock_for(fdir):
        if os.path.isfile(marker):
            return
        tmp = os.path.join(fdir, ".dump")
        os.makedirs(tmp, exist_ok=True)
        renamed = 0
        rc = 1
        targets = _font_dump_targets(attachments)
        if targets:
            argv = [_media.ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error"]
            for idx, tmpname, _real in targets:
                argv += [f"-dump_attachment:{idx}", os.path.join(tmp, tmpname)]
            argv += ["-i", input_url]
            try:
                proc = subprocess.run(argv, cwd=tmp, timeout=120, check=False,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                rc = proc.returncode
            except Exception:
                rc = 1
            for _idx, tmpname, real in targets:
                src = os.path.join(tmp, tmpname)
                if os.path.isfile(src):
                    try:
                        os.replace(src, os.path.join(fdir, real))
                        renamed += 1
                    except OSError:
                        pass
            if renamed <= 0:
                logger.warning("dump attachments failed file=%s rc=%s", input_url, rc)
        else:
            try:
                proc = subprocess.run(
                    [_media.ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
                     "-dump_attachment:t", "", "-i", input_url],
                    cwd=tmp, timeout=120, check=False,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                rc = proc.returncode
            except Exception:
                rc = 1
            for n in os.listdir(tmp):
                src = os.path.join(tmp, n)
                if os.path.isfile(src) and os.path.basename(n) == n and _FONT_RE.match(n):
                    try:
                        os.replace(src, os.path.join(fdir, n))
                        renamed += 1
                    except OSError:
                        pass
            if rc != 0 and renamed <= 0:
                logger.warning("dump attachments failed file=%s rc=%s", input_url, rc)
        shutil.rmtree(tmp, ignore_errors=True)
        if renamed > 0 or rc == 0:
            try:
                with open(marker, "w", encoding="utf-8") as fh:
                    fh.write("1")
            except OSError:
                pass


@router.get("/{version_id}/fonts")
def stream_fonts(version_id: int, kind: str = "movie"):
    """ASS 渲染可用字体清单：MKV 内嵌 attachment + data/fonts 内置（运行时可投放）。
    附件字体在首次请求字体文件时懒抽取（见 /{id}/fonts/{name}）。"""
    m, src = _version_source(version_id, kind)
    info = _media_cached_or_probe(m, src)
    out = []
    for a in (info.get("attachments") or []):
        name = os.path.basename(str(a.get("name") or ""))
        if _FONT_RE.match(name):
            out.append({"name": name, "source": "attachment",
                        "url": f"/api/stream/{int(m['id'])}/fonts/{quote(name)}"})
    bdir = os.path.join(settings.data_dir, "fonts")
    if os.path.isdir(bdir):
        for name in sorted(os.listdir(bdir)):
            if _FONT_RE.match(name):
                out.append({"name": name, "source": "builtin",
                            "url": f"/api/stream/fonts/builtin/{quote(name)}"})
    return {"version_id": int(m["id"]), "fonts": out}


_FONT_MIME = {".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf",
              ".otf": "font/otf", ".ttc": "font/collection"}


def _font_mime(name: str) -> str:
    """按扩展名给字体 MIME（评审 B8/R13-B6）：此前统一 font/ttf 不准。"""
    return _FONT_MIME.get(os.path.splitext(name)[1].lower(), "font/ttf")


@router.get("/fonts/builtin/{name}")
def stream_builtin_font(name: str):
    """内置字体（data/fonts/*，运行时可投放；不在仓库里塞大字体）。"""
    fn = os.path.basename(unquote(name or ""))
    if not _FONT_RE.match(fn):
        raise HTTPException(422, "bad font name")
    path = os.path.join(settings.data_dir, "fonts", fn)
    if not os.path.isfile(path):
        raise HTTPException(404, "font not found")
    return FileResponse(path, media_type=_font_mime(fn), filename=fn)


@router.get("/{version_id}/fonts/{name}")
def stream_attachment_font(version_id: int, name: str, kind: str = "movie"):
    """MKV 内嵌字体（attachment）：首次请求时从片源 dump 到缓存 fonts/。"""
    m, src = _version_source(version_id, kind)
    info = _media_cached_or_probe(m, src)
    wanted = os.path.basename(unquote(name or ""))
    if not _FONT_RE.match(wanted):
        raise HTTPException(422, "bad font name")
    att = {os.path.basename(str(a.get("name") or "")) for a in (info.get("attachments") or [])}
    if wanted not in att:
        raise HTTPException(404, "font not found in this file")
    fdir = os.path.join(version_cache_dir(kind, int(m["id"])), "fonts")
    os.makedirs(fdir, exist_ok=True)
    dest = os.path.join(fdir, wanted)
    if not os.path.isfile(dest):
        _dump_attachments(src.input, fdir, info.get("attachments"))
    if not os.path.isfile(dest):
        raise HTTPException(404, "font extract failed")
    return FileResponse(dest, media_type=_font_mime(wanted), filename=wanted)

