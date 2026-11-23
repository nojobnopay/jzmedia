"""Online source administration, explicit read-only diagnosis and confirmed repairs."""

import hmac
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .. import config
from ..online import catalog, diagnosis_actions, models, service, source_hosts, source_migration
from ..online.providers import ProviderError
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
            except source_migration.SourceChanged:
                return JSONResponse({"detail": "online_source_changed", "code": "online_source_changed"},
                                    status_code=409, headers={"Cache-Control": "no-store"})
            except (catalog.CatalogError, service.PlaybackError) as error:
                return JSONResponse({"detail": error.category, "code": error.category},
                                    status_code=error.status, headers={"Cache-Control": "no-store"})
            except ProviderError as error:
                return JSONResponse({"detail": error.category, "code": error.category},
                                    status_code=catalog.provider_error_status(error),
                                    headers={"Cache-Control": "no-store"})

        return validated


router = APIRouter(prefix="/api/online", tags=["online"], route_class=OnlineAdminRoute,
                   dependencies=[Depends(_management_access)])


class SourceListResponse(BaseModel):
    items: list[models.OnlineSource]


class DiagnosisConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class DiagnosisRequest(DiagnosisConfirmation):
    question: str = Field(default="", max_length=500)
    external_id: str | None = Field(default=None, min_length=1, max_length=128)
    item_id: models.ItemId | None = None
    line_id: models.LineId | None = None
    episode_id: models.EpisodeId | None = None
    error_code: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    cache_only: bool = False
    compare_direct: bool = False


class DiagnosticEvidence(BaseModel):
    tool: str
    label: str
    outcome: str
    detail: str
    checked_at: int
    page_url: str | None = None


class DiagnosticProposal(BaseModel):
    id: str
    expires_at: int
    kind: Literal["api", "media_direct"]
    old_url: str | None = None
    new_url: str | None = None
    old_label: str | None = None
    new_label: str | None = None
    changes: list[models.HostMigrationChange]


class DiagnosisResult(BaseModel):
    status: str
    answer: str
    evidence: list[DiagnosticEvidence]
    proposal: DiagnosticProposal | None
    can_compare_direct: bool = False


class DiagnosisReceipt(BaseModel):
    status: Literal["applied"]
    answer: str
    evidence: list[DiagnosticEvidence]
    verification_status: str
    source: models.OnlineSource


@router.post("/sources/{source_id}/diagnosis", response_model=DiagnosisResult)
async def diagnose_source(request: Request, source_id: models.SourceId, body: DiagnosisRequest):
    from .online_catalog import _read
    return await _read(request, diagnosis_actions.start, source_id, body.model_dump(exclude_none=True),
                       deadline_seconds=28)


@router.post("/sources/{source_id}/diagnosis/{proposal_id}/confirm", response_model=DiagnosisReceipt)
def confirm_diagnosis(request: Request, source_id: models.SourceId, proposal_id: str, body: DiagnosisConfirmation):
    return diagnosis_actions.confirm(service.capture_access(request), source_id, proposal_id)


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


@router.get("/sources/{source_id}", response_model=models.OnlineSourceDetail)
def get_source(source_id: str):
    value = online_store.get_online_source(source_id)
    if value is None:
        raise _missing()
    return {**value, "configuration_fingerprint": source_migration.fingerprint(value)}


@router.post("/sources/{source_id}/host-migration-preview", response_model=models.HostMigrationPreview)
def preview_host_migration(source_id: models.SourceId, body: models.HostMigrationPreviewRequest):
    source = online_store.get_online_source(source_id)
    if source is None:
        raise _missing()
    return source_migration.preview(source, body.api_base_url, body.allowed_hosts, body.expected_fingerprint)


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


@router.post("/sources/{source_id}/replace-catalogue", response_model=models.CatalogueReplacement)
def replace_catalogue(source_id: models.SourceId, body: models.CatalogueReplacementRequest):
    try:
        result, changed = online_store.replace_catalogue(source_id, body)
    except online_store.OnlineNotFound:
        raise _missing() from None
    except (ValidationError, ValueError, TypeError):
        raise _invalid_config() from None
    if changed:
        service.registry.revoke_source(source_id)
    return result


@router.post("/sources/{source_id}/host-check",
             response_model=source_hosts.PosterHostCheckReport | source_hosts.HostCheckReport)
async def check_source_hosts(request: Request, source_id: models.SourceId,
                             body: source_hosts.HostCheckRequest, x_online_host_report: str | None = Header(None)):
    from .online_catalog import _read
    return await _read(request, source_hosts.check, source_id, body.external_id, x_online_host_report == "2")


@router.post("/sources/{source_id}/allow-hosts", response_model=models.OnlineSource)
def allow_source_hosts(request: Request, source_id: models.SourceId,
                       body: source_hosts.AllowHostsRequest):
    try:
        value, changed = source_hosts.allow(service.capture_access(request), source_id, body.model_dump())
        if changed:
            service.registry.revoke_source(source_id)
        return value
    except online_store.OnlineNotFound:
        raise _missing() from None
    except (ValidationError, ValueError, TypeError):
        raise _invalid_config() from None
