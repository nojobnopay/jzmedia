from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import onboarding, tmdb

router = APIRouter(prefix="/api")


class UploadResult(BaseModel):
    model_config = {"extra": "forbid"}
    library_id: int = Field(gt=0)
    uploaded: int = Field(ge=0, le=500)
    skipped: int = Field(ge=0, le=500)
    failed: int = Field(ge=0, le=500)
    cancelled: bool = False


class ProgressUpdate(BaseModel):
    model_config = {"extra": "forbid"}
    status: Literal["active", "deferred", "completed"] | None = None
    step: int | None = Field(default=None, ge=1, le=4)
    library_id: int | None = Field(default=None, gt=0)
    kind: Literal["movie", "tv"] | None = None
    import_mode: Literal["scan", "upload"] | None = None
    tmdb_skipped: bool | None = None
    upload_result: UploadResult | None = None


@router.get("/onboarding")
def get_progress():
    return onboarding.view()


@router.patch("/onboarding")
def update_progress(body: ProgressUpdate):
    changes = body.model_dump(exclude_unset=True)
    if any(v is None for k, v in changes.items() if k not in ("library_id", "upload_result")):
        raise HTTPException(422, "进度字段不能为空")
    try:
        return onboarding.update(changes)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/tmdb/check")
def check_tmdb():
    signature = onboarding.tmdb_signature()
    onboarding.record_check("tmdb", signature, False)
    result = tmdb.check_connection()
    onboarding.record_check("tmdb", signature, result["ok"])
    return result
