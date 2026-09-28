"""Catalogue-only TV directory ownership operations."""
import httpx
import json
import threading

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import storage, store, tv_bindings
from ..jobkit import JobRegistry
from ..log import get_logger

router = APIRouter(prefix='/api/tv/bindings')
logger = get_logger('routers.tv_bindings')
_JOBS = JobRegistry(max_finished=20, prefix='tvbinding')
_ACTIVE: dict[tuple, str] = {}
_ACTIVE_LOCK = threading.Lock()


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except httpx.HTTPError as e:
        raise HTTPException(502, '资料来源暂不可用，请稍后重试或使用已有缓存') from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    except storage.StorageError as e:
        raise HTTPException(503, f'无法读取目录：{e}') from e


def _job_error(error):
    if isinstance(error, httpx.HTTPError):
        return '资料来源暂不可用，请稍后重试或使用已有缓存'
    if isinstance(error, storage.StorageError):
        return f'无法读取目录：{error}'
    if isinstance(error, ValueError):
        return str(error)
    logger.exception('TV binding background job failed', exc_info=error)
    return '处理失败，请重试或查看服务端日志'


def _job_worker(jid, key, fn):
    try:
        result = fn()
        _JOBS.update(jid, state='done', done=1, total=1, result=result)
    except Exception as error:
        _JOBS.update(jid, state='failed', error=_job_error(error))
    finally:
        with _ACTIVE_LOCK:
            if _ACTIVE.get(key) == jid:
                _ACTIVE.pop(key, None)


def _start_job(key, kind, library_id, fn):
    with _ACTIVE_LOCK:
        old_id = _ACTIVE.get(key)
        old = _JOBS.get(old_id) if old_id else None
        if old and old.get('state') == 'running':
            return {**old, 'resumed': True}
        job = _JOBS.create(kind=kind, library_id=int(library_id))
        jid = job['job_id']
        _ACTIVE[key] = jid
    threading.Thread(target=_job_worker, args=(jid, key, fn), daemon=True,
                     name=f'tv-binding-{kind}-{library_id}').start()
    return {**job, 'resumed': False}


def _body_key(kind, body):
    return kind, json.dumps(body, ensure_ascii=False, sort_keys=True,
                            separators=(',', ':'))


class Scope(BaseModel):
    library_id: int = Field(gt=0)
    show_id: int | None = Field(default=None, gt=0)


class Directory(BaseModel):
    path: str = Field(min_length=1, max_length=2000)
    season: int | None = Field(default=None, ge=0, le=99)
    override_season: bool = False


class Preview(BaseModel):
    library_id: int = Field(gt=0)
    tmdb_id: int = Field(gt=0, le=2**31-1)
    target_show_id: int | None = Field(default=None, gt=0)
    directories: list[Directory] = Field(min_length=1, max_length=50)
    replace_manual: bool = False
    allow_duplicates: bool = False


class Suggest(BaseModel):
    library_id: int = Field(gt=0)
    paths: list[str] = Field(min_length=1, max_length=12)
    tmdb_id: int | None = Field(default=None, gt=0, le=2**31-1)


class Token(BaseModel):
    token: str = Field(min_length=32, max_length=32)


class Undo(Token):
    dry_run: bool = True


@router.get('/directories')
def directories(library_id: int = Query(gt=0), show_id: int | None = None):
    return _call(tv_bindings.catalog_inventory, library_id, show_id)


@router.post('/directories/refresh')
def refresh_directories(body: Scope):
    key = ('inventory', body.library_id, body.show_id)
    return _start_job(key, 'inventory', body.library_id,
                      lambda: tv_bindings.inventory(body.library_id, body.show_id))


@router.get('/jobs/{job_id}')
def job_status(job_id: str):
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(404, '任务不存在或服务已重启，请重试')
    return job


@router.post('/suggest/start')
def start_suggest(body: Suggest):
    data = body.model_dump()
    return _start_job(_body_key('suggest', data), 'suggest', body.library_id,
                      lambda: tv_bindings.suggest(**data))


@router.post('/preview/start')
def start_preview(body: Preview):
    data = body.model_dump()
    return _start_job(_body_key('preview', data), 'preview', body.library_id,
                      lambda: tv_bindings.preview(**data))


@router.post('/apply/start')
def start_apply(body: Token):
    record = _call(store.get_tv_binding_plan, body.token)
    library_id = int(record['payload']['library_id'])
    return _start_job(('apply', body.token), 'apply', library_id,
                      lambda: tv_bindings.apply(body.token))


@router.post('/suggest')
def suggest(body: Suggest):
    return _call(tv_bindings.suggest, **body.model_dump())


@router.post('/preview')
def preview(body: Preview):
    return _call(tv_bindings.preview, **body.model_dump())


@router.post('/apply')
def apply(body: Token):
    return _call(tv_bindings.apply, body.token)


@router.get('/history')
def history(library_id: int = Query(gt=0)):
    return {'items': store.list_tv_binding_history(library_id)}


@router.post('/undo')
def undo(body: Undo):
    return _call(tv_bindings.undo, body.token, body.dry_run)
