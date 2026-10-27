"""Read existing health observations without triggering upstream requests."""

from fastapi import APIRouter, Request

from ..online import health, service
from ..online.catalog import CatalogError
from ..online.health_models import SourceHealth
from ..online.models import SourceId
from ..store import online_network
from ..store.online import OnlineNotFound
from .online_catalog import OnlineCatalogRoute


router = APIRouter(prefix="/api/online/catalog", tags=["online-health"], route_class=OnlineCatalogRoute)


@router.get("/health", response_model=SourceHealth)
def source_health(request: Request, source_id: SourceId):
    if set(request.query_params) != {"source_id"} or len(request.query_params.getlist("source_id")) != 1:
        raise CatalogError("invalid_online_catalog_request", 422)
    access = service.capture_access(request)
    try:
        state = online_network.load_online_network_state(source_id)
    except OnlineNotFound:
        raise CatalogError("online_source_not_found", 404) from None
    except (ValueError, KeyError, TypeError):
        raise CatalogError("online_health_unavailable", 503) from None
    result = {"source_id": source_id, "cache_scope": "process", "max_age_seconds": health.MAX_AGE_SECONDS,
              "purposes": {purpose: health.registry.summary(state, purpose) for purpose in ("api", "media")}}
    service._current_access(access)
    return result
