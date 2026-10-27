"""Device/mode favourites and progress, separate from NAS media state."""

from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from ..online import service, state, state_models
from ..online.models import EpisodeId, ItemId, LineId


HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
SCOPE_HEADER = "X-Online-State-Scope"


def _error(category, status):
    return JSONResponse({"detail": category, "code": category}, status_code=status, headers=HEADERS)


class OnlineStateRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validated(request):
            try:
                response = await handler(request)
                response.headers.update(HEADERS)
                return response
            except RequestValidationError:
                return _error("invalid_online_state_request", 422)
            except state.OnlineStateError as error:
                return _error(error.category, error.status)
            except service.PlaybackError as error:
                return _error(error.category, error.status)

        return validated


router = APIRouter(prefix="/api/online/state", tags=["online-state"], route_class=OnlineStateRoute)


def _query(request, allowed):
    if any(key not in allowed or len(request.query_params.getlist(key)) != 1 for key in request.query_params):
        raise state.OnlineStateError("invalid_online_state_request", 422)


@router.get("/library", response_model=state_models.Library)
def library(request: Request, section: Literal["favorites", "continue", "history"] = "continue",
            limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0, le=1000)):
    _query(request, {"section", "limit", "offset"})
    return state.library(service.capture_access(request), section, limit, offset)


@router.get("/items/{item_id}", response_model=state_models.ItemState)
def item_state(request: Request, item_id: ItemId, episode_id: EpisodeId | None = None, line_id: LineId | None = None):
    _query(request, {"episode_id", "line_id"})
    return state.item_state(service.capture_access(request), item_id, episode_id=episode_id, line_id=line_id)


@router.put("/items/{item_id}/favorite", response_model=state_models.FavoriteResult)
def favorite(request: Request, item_id: ItemId, body: state_models.FavoriteBody):
    _query(request, set())
    return state.favorite(service.capture_access(request), request.headers.get(SCOPE_HEADER), item_id, body.favorite)


@router.delete("/items/{item_id}/history", response_model=state_models.DeleteResult)
def delete_history(request: Request, item_id: ItemId):
    _query(request, set())
    return state.delete_history(service.capture_access(request), request.headers.get(SCOPE_HEADER), item_id)


@router.post("/progress/claim", response_model=state_models.Claim)
def claim(request: Request, body: state_models.ClaimBody):
    _query(request, set())
    return state.claim(service.capture_access(request), request.headers.get(SCOPE_HEADER), body)


@router.post("/progress", response_model=state_models.ProgressResult)
def progress(request: Request, body: state_models.ProgressBody):
    _query(request, set())
    return state.save_progress(service.capture_access(request), request.headers.get(SCOPE_HEADER), body)
