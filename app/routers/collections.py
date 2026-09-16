"""手工合集：建合集 → 海报墙多选/详情页加入 → 合集页查看/改名/移除。

成员以海报粒度存放（有 tmdb_id 按 tmdb，无按单行 id），同片多版本自动跟随海报。
"""
from fastapi import APIRouter, HTTPException

import threading

from .. import store

router = APIRouter(prefix="/api/collections")

# 补全 Job（内存态，单进程自用；重启丢失可重跑，回填幂等）
_JOBS: dict = {}
_JOBS_LOCK = threading.RLock()


@router.get("")
def list_all(q: str = ""):
    return {"items": store.list_collections(q)}


@router.post("")
def create(body: dict):
    body = body or {}
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(422, "name required")
    ids = _ids_from(body)
    tmdb_cid = body.get("tmdb_collection_id")
    if tmdb_cid is not None:
        tmdb_cid = _int_id(tmdb_cid)
    try:
        return store.create_collection(name, body.get("overview") or "",
                                       tmdb_cid, ids)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/suggest")
def suggest(min_members: int = 2):
    """TMDB 系列推荐（纯本地只读）：库内同系列≥min_members 部即一项，用户点接受才建合集。"""
    return store.suggest_series_collections(min_members)


@router.post("/suggest/backfill")
def suggest_backfill(body: dict | None = None):
    """补全系列信息：后台逐个轻量刷新待排查的 tmdb_id（用户手动触发才调网）。
    立即返回 {job_id, total}；前端轮询 ./status 看进度，推荐经 GET /suggest 增量呈现。
    轻量路径只写镜像+文字（不下海报/头像、不写 NFO），手工标题不覆盖；
    确认无系列的独立片自动排除（force=True 忽略排除全量重查）。"""
    import threading as _threading
    import time as _time
    import uuid as _uuid
    limit = ((body or {}).get("limit") or 50)
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        raise HTTPException(422, "limit must be int")
    force = bool((body or {}).get("force"))
    from .. import scanner
    with _JOBS_LOCK:
        for jid, j in _JOBS.items():
            if j["state"] == "running":
                return {"job_id": jid, "total": j["total"], "resumed": True}
        tids = store.tmdb_ids_missing_collection(limit, force)
        if not tids:
            return {"job_id": "", "total": 0, "resumed": False,
                    "suggest": store.suggest_series_collections()}
        jid = _uuid.uuid4().hex[:12]
        _JOBS[jid] = {"state": "running", "done": 0, "total": len(tids),
                      "current_title": "", "failed": [], "force": force,
                      "started_at": int(_time.time())}
    def _run():
        for tid in tids:
            with _JOBS_LOCK:
                if _JOBS.get(jid, {}).get("state") != "running":
                    return
            try:
                m = store.get_movie_by_tmdb(tid)
                title = (m or {}).get("title", "") if m else ""
                with _JOBS_LOCK:
                    _JOBS[jid]["current_title"] = title or f"TMDB {tid}"
                # 轻量：只抓详情写镜像+文字扇出，不跑海报/头像/NFO
                scanner.refresh_tmdb_id_fast(tid)
            except Exception as e:
                with _JOBS_LOCK:
                    _JOBS[jid]["failed"].append(
                        {"tmdb_id": tid,
                         "title": (_JOBS[jid].get("current_title") or ""),
                         "error": str(e)[:200]})
            finally:
                with _JOBS_LOCK:
                    if jid in _JOBS:
                        _JOBS[jid]["done"] += 1
                        _JOBS[jid]["current_title"] = ""
        with _JOBS_LOCK:
            if jid in _JOBS and _JOBS[jid]["state"] == "running":
                _JOBS[jid]["state"] = "done"
    _threading.Thread(target=_run, daemon=True).start()
    return {"job_id": jid, "total": len(tids), "resumed": False, "force": force}


@router.get("/suggest/backfill/status")
def suggest_backfill_status(job_id: str = ""):
    """补全进度轮询：{state, done, total, current_title, failed}。无 job_id 查最近一个。"""
    with _JOBS_LOCK:
        if job_id and job_id in _JOBS:
            j = _JOBS[job_id]
            return {"job_id": job_id, **{k: (list(v) if k == "failed" else v)
                                         for k, v in j.items()}}
        if _JOBS:
            jid = max(_JOBS, key=lambda k: _JOBS[k].get("started_at", 0))
            j = _JOBS[jid]
            return {"job_id": jid, **{k: (list(v) if k == "failed" else v)
                                      for k, v in j.items()}}
    return {"job_id": "", "state": "idle", "done": 0, "total": 0,
            "current_title": "", "failed": []}


@router.post("/suggest/backfill/cancel")
def suggest_backfill_cancel(body: dict | None = None):
    """取消补全：后台循环协作式退出，已刷的不回滚。"""
    job_id = ((body or {}).get("job_id") or "")
    with _JOBS_LOCK:
        target = job_id if job_id in _JOBS else (
            max(_JOBS, key=lambda k: _JOBS[k].get("started_at", 0)) if _JOBS else "")
        if not target or _JOBS[target]["state"] != "running":
            return {"job_id": target, "state": (_JOBS[target]["state"] if target else "idle")}
        _JOBS[target]["state"] = "cancelled"
        return {"job_id": target, "state": "cancelled"}


@router.post("/{cid}/members/top-up")
def top_up(cid: int):
    """一键补齐：把库内同系列、尚未入成员的影片收进已有合集（服务端重算差集）。"""
    try:
        r = store.top_up_collection(cid)
    except LookupError:
        raise HTTPException(404, "collection not found")
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"id": cid, **r}


@router.get("/{cid}")
def get_one(cid: int):
    d = store.get_collection(cid)
    if not d:
        raise HTTPException(404, "collection not found")
    return d


@router.patch("/{cid}")
def patch_one(cid: int, body: dict):
    if not store.get_collection(cid):
        raise HTTPException(404, "collection not found")
    allowed = {"name", "overview", "poster_path", "tmdb_collection_id"}
    data = {k: v for k, v in (body or {}).items() if k in allowed}
    if "tmdb_collection_id" in data and data["tmdb_collection_id"] is not None:
        try:
            data["tmdb_collection_id"] = _int_id(data["tmdb_collection_id"])
        except (TypeError, ValueError):
            raise HTTPException(422, "tmdb_collection_id must be int")
    try:
        out = store.update_collection(cid, **data)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if not out:
        raise HTTPException(404, "collection not found")
    return out


@router.delete("/{cid}")
def delete_one(cid: int):
    if not store.delete_collection(cid):
        raise HTTPException(404, "collection not found")
    return {"id": cid, "deleted": True}


# 成员/ID 上限（评审 B6/R06-B3）：超 SQLite 64 位的 int 会在绑定时抛 OverflowError→500，
# 列表过长会超 SQL 变量上限；统一在这里拦成 422
_MAX_IDS = 2000
_ID_MAX = 2 ** 63 - 1


def _int_id(v) -> int:
    try:
        i = int(v)
    except (TypeError, ValueError):
        raise HTTPException(422, "id must be int")
    if not (0 < i <= _ID_MAX):
        raise HTTPException(422, "id out of range")
    return i


def _ids_from(body: dict) -> list[int]:
    raw = (body or {}).get("movie_ids", (body or {}).get("member_ids", []))
    if isinstance(raw, list) and len(raw) > _MAX_IDS:
        raise HTTPException(422, f"too many ids (max {_MAX_IDS})")
    try:
        return [_int_id(x) for x in (raw or [])]
    except HTTPException:
        raise
    except (TypeError, ValueError):
        raise HTTPException(422, "movie_ids must be int list")


@router.post("/{cid}/members")
def add_members(cid: int, body: dict):
    ids = _ids_from(body)
    if not ids:
        raise HTTPException(422, "movie_ids required")
    try:
        r = store.add_collection_members(cid, ids)
    except LookupError:
        raise HTTPException(404, "collection not found")
    return {"id": cid, **r}


@router.delete("/{cid}/members")
def remove_members(cid: int, body: dict):
    ids = _ids_from(body)
    if not ids:
        raise HTTPException(422, "movie_ids required")
    try:
        r = store.remove_collection_members(cid, ids)
    except LookupError:
        raise HTTPException(404, "collection not found")
    return {"id": cid, **r}


@router.post("/{cid}/members/remove")
def remove_members_post(cid: int, body: dict):
    """DELETE 带 body 在部分客户端受限，前端用此 POST 别名移除成员（语义同 DELETE）。"""
    return remove_members(cid, body)


@router.post("/from-tmdb-series")
def from_tmdb_series(body: dict):
    """一键建合集：按某片的 TMDB 系列把库内同系列全收进新合集（人物合集仍手动建）。"""
    movie_id = (body or {}).get("movie_id")
    if not movie_id:
        raise HTTPException(422, "movie_id required")
    try:
        movie_id = _int_id(movie_id)
    except (TypeError, ValueError):
        raise HTTPException(422, "movie_id must be int")
    hint = store.collection_hint_for_movie(movie_id)
    if not hint or not hint.get("collection_tmdb_id"):
        raise HTTPException(422, "movie has no tmdb collection")
    name = ((body or {}).get("name") or hint.get("collection_name") or "").strip()
    if not name:
        raise HTTPException(422, "name required")
    member_ids = [x["id"] for x in hint.get("in_library", [])]
    try:
        return store.create_collection(name, (body or {}).get("overview") or "",
                                       hint["collection_tmdb_id"], member_ids)
    except ValueError as e:
        raise HTTPException(422, str(e))
