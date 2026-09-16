"""store.media_info（自 app/store.py 拆分，评审 B9/R02-Q3；对外经 app.store 门面使用）。"""
import json
import time
from ._base import _conn, _dump_list, _lock, logger
__all__ = ['get_media_info', 'list_media_info_brief', 'upsert_media_info']

def get_media_info(movie_id: int) -> dict | None:
    """读版本媒体信息缓存（含 audio_json/sub_json 已 parse 为 list）。无行返回 None。"""
    with _lock, _conn() as c:
        try:
            row = c.execute("SELECT * FROM media_info WHERE movie_id=?", (movie_id,)).fetchone()
        except Exception:
            return None
        if not row:
            return None
        d = dict(row)
        for k, name in (("audio_json", "audio"), ("sub_json", "subs"),
                        ("attachments_json", "attachments")):
            v = d.pop(k, "[]")
            try:
                lst = json.loads(v or "[]")
                d[name] = lst if isinstance(lst, list) else []
            except Exception:
                d[name] = []
        d["playable"] = bool(d.get("playable"))
        return d


def list_media_info_brief() -> list[dict]:
    """全部探测行的轻量视图（评审 B8/R12-D6：probe-missing 预筛用，不解析 JSON 列）。"""
    with _lock, _conn() as c:
        try:
            return [dict(r) for r in c.execute(
                "SELECT movie_id, playable, probe_ver, probe_error FROM media_info")]
        except Exception:
            return []


def upsert_media_info(movie_id: int, info: dict) -> dict:
    """写版本媒体信息缓存（ffprobe 本地派生）。info 含 container/duration/width/height/
    vcodec/acodec/音频与字幕列表（含 disposition）/HDR/DV/位深/附件等（见 media.probe）。
    环境错误（ffprobe 缺失/超时/不可读）不落库：返回旧行或内存透传（probed_at=0），
    下次打开自动重试——避免宿主/容器换环境后把“无效”误刻进库。返回落库后行。"""
    try:
        from ..media import is_retryable_error as _retryable
    except Exception:
        def _retryable(_m: str) -> bool:
            return False
    if not info.get("playable") and _retryable(str(info.get("probe_error") or "")):
        old = get_media_info(int(movie_id))
        if old and old.get("playable"):
            return old
        logger.debug("probe transient error mid=%s err=%s (not persisted)", movie_id,
                     info.get("probe_error"))
        transient = dict(info)
        transient["audio"] = list(info.get("audio") or [])
        transient["subs"] = list(info.get("subs") or [])
        transient["attachments"] = list(info.get("attachments") or [])
        transient["playable"] = False
        transient["probed_at"] = 0
        transient.setdefault("dv_profile", 0)
        transient.setdefault("probe_ver", 0)
        return transient
    now = int(time.time())
    audio_s = _dump_list(info.get("audio"))
    subs_s = _dump_list(info.get("subs"))
    attach_s = _dump_list(info.get("attachments"))

    def _iv(key):
        try:
            return max(0, int(info.get(key) or 0))
        except (TypeError, ValueError):
            return 0

    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO media_info(movie_id, container, duration, width, height,"
            " vcodec, acodec, vbitrate, abitrate, audio_json, sub_json, dv_profile,"
            " probe_ver, video_profile, video_level, bit_depth, pix_fmt, color_transfer,"
            " color_primaries, hdr, dv_bl_compat, hdr10plus, attachments_json,"
            " playable, probe_error, probed_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(movie_id) DO UPDATE SET container=excluded.container,"
            " duration=excluded.duration, width=excluded.width, height=excluded.height,"
            " vcodec=excluded.vcodec, acodec=excluded.acodec,"
            " vbitrate=excluded.vbitrate, abitrate=excluded.abitrate,"
            " audio_json=excluded.audio_json, sub_json=excluded.sub_json,"
            " dv_profile=excluded.dv_profile, probe_ver=excluded.probe_ver,"
            " video_profile=excluded.video_profile, video_level=excluded.video_level,"
            " bit_depth=excluded.bit_depth, pix_fmt=excluded.pix_fmt,"
            " color_transfer=excluded.color_transfer,"
            " color_primaries=excluded.color_primaries, hdr=excluded.hdr,"
            " dv_bl_compat=excluded.dv_bl_compat, hdr10plus=excluded.hdr10plus,"
            " attachments_json=excluded.attachments_json,"
            " playable=excluded.playable, probe_error=excluded.probe_error,"
            " probed_at=excluded.probed_at",
            (int(movie_id), str(info.get("container") or "")[:16],
             float(info.get("duration") or 0),
             int(info.get("width") or 0), int(info.get("height") or 0),
             str(info.get("vcodec") or "")[:32], str(info.get("acodec") or "")[:32],
             int(info.get("vbitrate") or 0), int(info.get("abitrate") or 0),
             audio_s, subs_s, _iv("dv_profile"),
             _iv("probe_ver"), str(info.get("video_profile") or "")[:40],
             _iv("video_level"), _iv("bit_depth"), str(info.get("pix_fmt") or "")[:24],
             str(info.get("color_transfer") or "")[:24],
             str(info.get("color_primaries") or "")[:24], str(info.get("hdr") or "")[:16],
             _iv("dv_bl_compat"), _iv("hdr10plus"), attach_s,
             1 if info.get("playable") else 0,
             str(info.get("probe_error") or "")[:300], now))
    out = get_media_info(int(movie_id))
    return out if out is not None else info   # 评审 B7/R02-B5：不用 assert 当控制流

