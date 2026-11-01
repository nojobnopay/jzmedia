"""Unified asynchronous TV intake and durable organisation suggestions."""
import threading
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from .. import store, tv_ingest
from ..jobkit import JobRegistry

router = APIRouter(prefix='/api')
_JOBS = JobRegistry(prefix='tvingest', max_finished=15)
_START_LOCK = threading.Lock()


class IngestBody(BaseModel):
    ids: list[int] | None = Field(default=None, min_length=1, max_length=500)
    library_id: int | None = Field(default=None, ge=1)
    media_library_id: int | None = Field(default=None, ge=1)
    source: Literal['manual', 'detail', 'upload', 'scan'] = 'manual'
    retry_metadata: bool = False
    actions: list[str] | None = None
    allow_torrent: bool = False
    allow_absolute_shows: list[int] = Field(default_factory=list)

    @model_validator(mode='after')
    def scope(self):
        if sum(v is not None for v in (self.ids, self.library_id, self.media_library_id)) != 1:
            raise ValueError('ids、library_id、media_library_id 必须且只能指定一个')
        if self.ids and any(sid <= 0 for sid in self.ids):
            raise ValueError('剧集 ID 必须为正整数')
        return self


def _worker(jid, ids, lids, body):
    def stopped():
        job = _JOBS.get(jid)
        return job is None or job['state'] in ('cancelled', 'failed')

    def progress(phase, done, total):
        if not stopped():
            _JOBS.update(jid, phase=phase, done=done, total=total)

    try:
        with tv_ingest.operation_scope(lids, stopped):
            if stopped():
                raise tv_ingest.IngestionCancelled()
            _JOBS.update(jid, state='running', phase='discover')
            result = tv_ingest.prepare(ids=ids if ids else None, library_ids=lids, source=body.source,
                retry_metadata=body.retry_metadata, actions=body.actions,
                allow_torrent=body.allow_torrent, allow_absolute_shows=body.allow_absolute_shows,
                should_stop=stopped, progress_cb=progress)
            if not stopped():
                _JOBS.update(jid, state='done', phase='done', summary=result,
                             done=len(ids), total=len(ids))
    except tv_ingest.IngestionCancelled:
        _JOBS.update(jid, state='cancelled', phase='cancelled')
    except Exception as exc:
        from ..metadata.state import safe_error
        _JOBS.update(jid, state='failed', phase='failed', error=safe_error(exc))
    finally:
        _JOBS.update(jid, worker_finished=True)


@router.post('/jobs/tv-ingest')
def start(body: IngestBody):
    try:
        shows, lids = tv_ingest.resolve_scope(ids=body.ids, library_id=body.library_id,
                                            media_library_id=body.media_library_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    from ..scanner.tv_organize import TV_ORGANIZE_ACTIONS
    if body.actions == [] or (body.actions is not None and any(a not in TV_ORGANIZE_ACTIONS for a in body.actions)):
        raise HTTPException(422, '不支持的整理项目')
    ids = sorted(int(s['id']) for s in shows)
    request_key = (ids, sorted(lids), bool(body.retry_metadata), sorted(body.actions or TV_ORGANIZE_ACTIONS),
                   bool(body.allow_torrent), sorted(set(body.allow_absolute_shows)))
    with _START_LOCK:
        for active in _JOBS.snapshot():
            if (active.get('worker_finished') is False and active.get('state') in ('queued', 'running')
                    and active.get('request_key') == request_key):
                return {'job_id': active['job_id'], 'state': active['state'], 'resumed': True}
        job = _JOBS.create(state='queued', phase='queued', ids=ids, library_ids=lids,
                           source=body.source, request_key=request_key, worker_finished=False)
        threading.Thread(target=_worker, args=(job['job_id'], ids, lids, body), daemon=True).start()
    return {'job_id': job['job_id'], 'state': 'queued', 'resumed': False}


@router.get('/jobs/tv-ingest/{job_id}')
def status(job_id: str):
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, '入库检查任务不存在，可重新检查')
    return {k: v for k, v in job.items() if k != 'request_key'}


@router.post('/jobs/tv-ingest/{job_id}/cancel')
def cancel(job_id: str):
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, '入库检查任务不存在')
    if job['state'] == 'queued':
        _JOBS.update(job_id, state='cancelled', phase='cancelled')
    else:
        _JOBS.cancel(job_id)
    return {'job_id': job_id, 'state': (_JOBS.get(job_id) or {})['state']}


@router.get('/tv/ingestion-reviews')
def reviews(library_id: int | None = Query(default=None, ge=1),
            media_library_id: int | None = Query(default=None, ge=1),
            show_id: int | None = Query(default=None, ge=1), include_ignored: bool = False):
    if sum(v is not None for v in (library_id, media_library_id, show_id)) != 1:
        raise HTTPException(422, '请选择一个剧集或视频库范围')
    try:
        _, lids = tv_ingest.resolve_scope(ids=[show_id] if show_id else None,
                                          library_id=library_id, media_library_id=media_library_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    items = store.tv_ingest.list_reviews(lids, show_id, include_ignored)
    for item in items:
        item['needs_check'] = bool(item['dirty'])
        item['plan'].setdefault('title', item['title'])
        item['plan'].setdefault('show_id', item['show_id'])
        item['plan'].setdefault('library_id', item['library_id'])
        item['plan'].update(state=item['state'], auto_prompt=bool(item['state'] == 'pending'
            and item['plan'].get('groups') and not item['plan'].get('blocked')))
    return {'items': items, 'count': len(items)}


class DecisionBody(BaseModel):
    preview_id: str = Field(min_length=1)
    ids: list[int] | None = Field(default=None, min_length=1)
    decision: Literal['deferred', 'ignored']


@router.post('/tv/ingestion-reviews/decision')
def decision(body: DecisionBody):
    try:
        ids = store.tv_ingest.decide(body.preview_id, body.ids, body.decision)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {'ok': True, 'preview_id': body.preview_id, 'ids': ids, 'state': body.decision}
