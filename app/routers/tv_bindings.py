"""Catalogue-only TV directory ownership operations."""
import httpx

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import storage, store, tv_bindings

router = APIRouter(prefix='/api/tv/bindings')


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except httpx.HTTPError as e:
        raise HTTPException(502, '资料来源暂不可用，请稍后重试或使用已有缓存') from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    except storage.StorageError as e:
        raise HTTPException(503, f'无法读取目录：{e}') from e


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
    return _call(tv_bindings.inventory, library_id, show_id)


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
