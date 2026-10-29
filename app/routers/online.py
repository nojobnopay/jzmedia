"""Online source administration; configuration changes perform no upstream I/O."""

import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ValidationError

from .. import config
from ..online import models
from ..online.source_templates import SourceTemplateList, list_source_templates
from ..store import online as online_store


def _management_access(request: Request, response: Response) -> None:
    """Protect configuration reads as well as writes when an API token is set.

    The application viewing middleware independently requires a management grant
    for this namespace when device protection is enabled.
    """
    response.headers["Cache-Control"] = "no-store"
    expected = config.effective_jzmedia_token()
    if not expected:
        return
    supplied = (request.headers.get("x-api-token") or "").strip()
    if not supplied:
        authorization = (request.headers.get("authorization") or "").strip()
        if authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
    if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(401, "需要访问令牌", headers={"Cache-Control": "no-store"})


def _invalid_config() -> HTTPException:
    return HTTPException(422, "在线配置无效，请检查字段和地址格式",
                         headers={"Cache-Control": "no-store"})


class OnlineAdminRoute(APIRoute):
    """Keep typed OpenAPI bodies without reflecting rejected configuration."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validated(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                # FastAPI's normal error JSON includes the rejected input.
                raise _invalid_config() from None

        return validated


router = APIRouter(prefix="/api/online", tags=["online"], route_class=OnlineAdminRoute,
                   dependencies=[Depends(_management_access)])


class SourceListResponse(BaseModel):
    items: list[models.OnlineSource]


def _missing() -> HTTPException:
    return HTTPException(404, "在线来源不存在", headers={"Cache-Control": "no-store"})


@router.get("/settings", response_model=models.OnlineSettings)
def get_settings():
    return online_store.get_online_settings()


@router.get("/source-templates", response_model=SourceTemplateList)
def get_source_templates():
    return list_source_templates()


@router.patch("/settings", response_model=models.OnlineSettings)
def update_settings(body: models.OnlineSettingsPatch):
    value = online_store.update_online_settings(body.enabled)
    if not value["enabled"]:
        from ..online.service import registry
        registry.close_all()
    return value


@router.get("/sources", response_model=SourceListResponse)
def list_sources():
    return {"items": online_store.list_online_sources()}


@router.post("/sources", response_model=models.OnlineSource, status_code=201)
def create_source(body: models.OnlineSourceCreate):
    return online_store.create_online_source(body.model_dump())


@router.get("/sources/{source_id}", response_model=models.OnlineSource)
def get_source(source_id: str):
    value = online_store.get_online_source(source_id)
    if value is None:
        raise _missing()
    return value


@router.patch("/sources/{source_id}", response_model=models.OnlineSource)
def update_source(source_id: str, body: models.OnlineSourcePatch):
    try:
        value, changed = online_store.update_online_source_with_change(source_id, body.model_dump(exclude_unset=True))
        if not value["enabled"] or changed:
            from ..online.service import registry
            registry.revoke_source(source_id)
        return value
    except online_store.OnlineNotFound:
        raise _missing() from None
    except (ValidationError, ValueError, TypeError):
        raise _invalid_config() from None
