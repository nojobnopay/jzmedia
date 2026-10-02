"""First-use progress: persist choices, derive completion from real data."""
import hashlib
import json
import threading
import time

from . import config, store
from .log import get_logger
from .store.onboarding import snapshot

logger = get_logger("onboarding")
_LOCK = threading.RLock()
_KEY = "onboarding_state"
_DEFAULT = {"version": 1, "status": "not_started", "step": 1,
            "library_id": None, "kind": "movie", "import_mode": "scan",
            "tmdb_skipped": False, "upload_result": None}


def _read():
    raw = store.get_setting(_KEY)
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("version") == 1:
                return {**_DEFAULT, **data}
        except (ValueError, TypeError):
            logger.warning("invalid onboarding progress; starting with defaults")
    return dict(_DEFAULT)


def _write(data):
    store.set_setting(_KEY, json.dumps(data, ensure_ascii=False))


def _digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def tmdb_signature():
    return _digest([config.effective_with_source(k)[0] for k in (
        "tmdb_read_token", "tmdb_api_key", "tmdb_proxy", "tmdb_language", "tmdb_image_base")])


def library_signature(lib):
    media = store.get_media_library(lib["media_library_id"]) or {}
    # Exclude status/timestamps changed by diagnostics, include credentials and paths.
    return _digest({"library": {k: lib.get(k) for k in (
        "id", "media_library_id", "kind", "subpath", "path", "enabled")},
        "media": {k: v for k, v in media.items()
                  if k not in ("last_status", "last_error", "last_check_at", "updated_at")}})


def record_check(name, signature, ok, library_id=None, writable=False):
    with _LOCK:
        data = _read()
        data[name + "_check"] = {"signature": signature, "ok": bool(ok),
                                 "library_id": library_id, "writable": bool(writable),
                                 "checked_at": int(time.time())}
        _write(data)


def _view(data):
    out = {key: data.get(key, default) for key, default in _DEFAULT.items()}
    lib = store.get_library(out["library_id"]) if out["library_id"] else None
    valid = bool(lib and lib.get("effective_enabled") and lib.get("kind") == out["kind"])
    check = data.get("library_check") or {}
    library_verified = bool(valid and check.get("ok") and
                            check.get("library_id") == out["library_id"] and
                            check.get("signature") == library_signature(lib))
    tmdb_check = data.get("tmdb_check") or {}
    tmdb_verified = bool(tmdb_check.get("ok") and tmdb_check.get("signature") == tmdb_signature())
    content = snapshot(out["library_id"] if valid else None, out["kind"])
    max_step = 1 if not (tmdb_verified or out["tmdb_skipped"]) else (
        2 if not library_verified else 3 if not content["count"] else 4)
    out.update(step=min(out["step"], max_step), max_step=max_step,
               tmdb_verified=tmdb_verified, library_verified=library_verified,
               target_valid=valid, content=content,
               upload_allowed=bool(library_verified and check.get("writable") and not lib.get("read_only")),
               can_complete=max_step == 4,
               show_welcome=out["status"] == "active" or (
                   out["status"] == "not_started" and content["total"] == 0))
    return out


def view():
    with _LOCK:
        return _view(_read())


def update(changes):
    with _LOCK:
        data = {**_read(), **changes}
        out = _view(data)
        if changes.get("status") == "completed" and not out["can_complete"]:
            raise ValueError("请先确认资料来源、检查视频库，并导入至少一部电影或一集剧集")
        data["step"] = out["step"]
        _write(data)
        return out
