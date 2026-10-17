"""Explicit, catalogue-only backfill of children's classification evidence."""
import json
import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import store, tmdb, viewing
from ..content_rules import evaluate_content, merge_content_evidence, normalize_content_evidence, normalize_tmdb_evidence
from ..jobkit import JobRegistry
from ..log import get_logger

router = APIRouter(prefix="/api/viewing/refresh", tags=["viewing"])
logger = get_logger("viewing.refresh")
_JOBS = JobRegistry(prefix="viewing-refresh")
_START_LOCK = threading.Lock()
_REQUIRED = {"adult", "certifications", "keywords"}


class _EvidenceError(ValueError):
    """A fixed, safe diagnostic suitable for returning to the browser."""


class RefreshBody(BaseModel):
    library_id: int | None = Field(default=None, gt=0)


def _payload(raw):
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _candidates(library_id):
    with store._conn() as connection:
        if library_id is not None and not connection.execute(
                "SELECT 1 FROM libraries WHERE id=?", (library_id,)).fetchone():
            raise HTTPException(404, "视频库不存在")
        result = []
        for kind, table in (("movie", "movies"), ("tv", "tv_shows")):
            query = f"SELECT DISTINCT tmdb_id FROM {table} WHERE children_auto_status='unknown' AND tmdb_id>0"
            params = ()
            if library_id is not None:
                query += " AND library_id=?"
                params = (library_id,)
            result.extend((kind, int(row[0])) for row in connection.execute(query + " ORDER BY tmdb_id", params))
    return result


def _save_evidence(kind, tmdb_id, evidence, library_id=None):
    """Update scoped evidence atomically without touching metadata or local choices."""
    table = "movies" if kind == "movie" else "tv_shows"
    library_clause = " AND library_id=?" if library_id is not None else ""
    library_params = (library_id,) if library_id is not None else ()
    with store._lock, store._conn() as connection:
        cached = connection.execute("SELECT payload_json FROM tmdb_cache WHERE media_type=? AND tmdb_id=?",
                                    (kind, tmdb_id)).fetchone()
        payload = _payload(cached[0] if cached else None)
        merged = merge_content_evidence(payload.get("content_evidence"), evidence)
        rows = connection.execute(f"SELECT id,content_evidence,children_auto_status,children_reason,genres,genre_ids"
                                  f" FROM {table} WHERE tmdb_id=?" + library_clause,
                                  (tmdb_id, *library_params)).fetchall()
        # A removed or rematched scoped item cannot borrow a match in another library.
        if not rows:
            return 0
        if not cached or normalize_content_evidence(payload.get("content_evidence")) != merged:
            payload["content_evidence"] = merged
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            if cached:
                connection.execute("UPDATE tmdb_cache SET payload_json=? WHERE media_type=? AND tmdb_id=?",
                                   (encoded, kind, tmdb_id))
            else:
                connection.execute("INSERT INTO tmdb_cache(tmdb_id,media_type,payload_json) VALUES(?,?,?)",
                                   (tmdb_id, kind, encoded))
        changed_ids = []
        for row in rows:
            local = merge_content_evidence(row["content_evidence"], merged)
            status, reason = evaluate_content(kind, local, row["genres"], row["genre_ids"])
            if kind == "tv":
                status, reason = viewing.effective_show_classification(
                    connection, row["id"], status, reason, show_fields={"content_evidence": local})
            if (normalize_content_evidence(row["content_evidence"]) == local
                    and row["children_auto_status"] == status and row["children_reason"] == reason):
                continue
            connection.execute(f"UPDATE {table} SET content_evidence=?,children_auto_status=?,children_reason=?"
                               " WHERE id=? AND tmdb_id=?" + library_clause,
                               (json.dumps(local, ensure_ascii=False), status, reason,
                                row["id"], tmdb_id, *library_params))
            changed_ids.append(row["id"])
        if changed_ids:
            viewing.invalidate_content_access(connection)
    if kind == "movie":
        for item_id in changed_ids:
            store.resync_fts(item_id)
    return len(changed_ids)


def _worker(job_id, candidates, library_id=None):
    done = changed = failed_count = 0
    failed = []
    try:
        with viewing.unrestricted():
            for kind, tmdb_id in candidates:
                job = _JOBS.get(job_id)
                if not job or job["state"] != "running":
                    return
                label = f"{'电影' if kind == 'movie' else '剧集'} TMDB {tmdb_id}"
                _JOBS.update(job_id, current=label)
                try:
                    detail = (tmdb.movie_detail if kind == "movie" else tmdb.tv_detail)(tmdb_id)
                    if not isinstance(detail, dict) or detail.get("id") != tmdb_id:
                        raise _EvidenceError("来源返回的作品身份不一致")
                    evidence = normalize_tmdb_evidence(kind, detail)
                    if not _REQUIRED.issubset(evidence["available"]):
                        raise _EvidenceError("分级或风险资料获取不完整，已保留原资料")
                    job = _JOBS.get(job_id)
                    if not job or job["state"] != "running":
                        return
                    changed += _save_evidence(kind, tmdb_id, evidence, library_id)
                except Exception as error:
                    failed_count += 1
                    message = str(error) if isinstance(error, _EvidenceError) else "资料来源暂不可用，已保留原资料"
                    failed.append({"kind": kind, "tmdb_id": tmdb_id, "error": message[:160]})
                    failed = failed[-50:]
                    logger.warning("content evidence refresh failed kind=%s tmdb_id=%s type=%s",
                                   kind, tmdb_id, type(error).__name__)
                done += 1
                _JOBS.update(job_id, done=done, changed=changed, failed=failed, failed_count=failed_count)
            if (_JOBS.get(job_id) or {}).get("state") == "running":
                _JOBS.update(job_id, state="done", current="")
    except Exception:
        logger.exception("content evidence refresh job failed")
        _JOBS.update(job_id, state="failed", error="资料补全失败，请稍后重试")
    finally:
        _JOBS.update(job_id, worker_finished=True, current="")


@router.post("")
def start_refresh(body: RefreshBody | None = None):
    viewing.require_management()
    library_id = body.library_id if body else None
    with _START_LOCK:
        # A cancelled worker can still be waiting for its current HTTP response.
        active = next((job for job in _JOBS.snapshot() if not job.get("worker_finished", True)), None)
        if active:
            if active["state"] == "running" and active.get("library_id") == library_id:
                return {**active, "resumed": True}
            raise HTTPException(409, "儿童资料补全任务仍在运行，请稍后重试")
        with viewing.unrestricted():
            candidates = _candidates(library_id)
        job = _JOBS.create(total=len(candidates), library_id=library_id, changed=0,
                           failed_count=0, worker_finished=False)
        if candidates:
            threading.Thread(target=_worker, args=(job["job_id"], candidates, library_id), daemon=True,
                             name="viewing-evidence-refresh").start()
        else:
            _JOBS.update(job["job_id"], state="done", worker_finished=True)
            job = _JOBS.get(job["job_id"])
    return {**job, "resumed": False}


@router.get("/{job_id}")
def refresh_status(job_id: str):
    viewing.require_management()
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在或服务已重启，请重试")
    return job


@router.post("/{job_id}/cancel")
def cancel_refresh(job_id: str):
    viewing.require_management()
    if not _JOBS.get(job_id):
        raise HTTPException(404, "任务不存在或服务已重启，请重试")
    _JOBS.cancel(job_id)
    return _JOBS.get(job_id)
