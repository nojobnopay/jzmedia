"""Network configuration only; no endpoint fetches a caller-supplied target."""

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict

from ..network.models import (NetworkProfileCreate,
                              NetworkProfilePatch, NetworkProfilePublic,
                              NetworkPurpose, NetworkRule, SourceNetworkPatch, SourceNetworkPolicy)
from ..network.policy import resolve_online_policy
from ..store import online_network as network_store
from ..store.online import OnlineNotFound
from .online import OnlineAdminRoute, _invalid_config, _management_access


router = APIRouter(prefix="/api/online", tags=["online"], route_class=OnlineAdminRoute,
                   dependencies=[Depends(_management_access)])


class ProfileListResponse(BaseModel):
    items: list[NetworkProfilePublic]


class DefaultResponse(BaseModel):
    rule: NetworkRule
    revision: int


class DefaultPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    rule: NetworkRule


class SourcePolicyResponse(BaseModel):
    source_id: str
    policy: SourceNetworkPolicy
    revision: int


def _missing() -> HTTPException:
    return HTTPException(404, "网络档案或在线来源不存在", headers={"Cache-Control": "no-store"})


def _conflict() -> HTTPException:
    return HTTPException(409, "网络档案仍被规则引用，请先修改相关规则",
                         headers={"Cache-Control": "no-store"})


@router.get("/network/profiles", response_model=ProfileListResponse)
def list_profiles():
    return {"items": network_store.list_network_profiles()}


@router.post("/network/profiles", response_model=NetworkProfilePublic, status_code=201)
def create_profile(body: NetworkProfileCreate):
    # Keep SecretStr intact for the store's explicit encryption boundary.
    return network_store.create_network_profile(body.model_dump())


@router.get("/network/profiles/{profile_id}", response_model=NetworkProfilePublic)
def get_profile(profile_id: str):
    value = network_store.get_network_profile(profile_id)
    if value is None:
        raise _missing()
    return value


@router.patch("/network/profiles/{profile_id}", response_model=NetworkProfilePublic)
def update_profile(profile_id: str, body: NetworkProfilePatch):
    try:
        return network_store.update_network_profile(profile_id, body.model_dump(exclude_unset=True))
    except OnlineNotFound:
        raise _missing() from None
    except ValueError:
        raise _invalid_config() from None


@router.delete("/network/profiles/{profile_id}", status_code=204)
def delete_profile(profile_id: str):
    try:
        network_store.delete_network_profile(profile_id)
    except OnlineNotFound:
        raise _missing() from None
    except network_store.NetworkConflict:
        raise _conflict() from None
    except ValueError:
        raise _invalid_config() from None
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/network/default", response_model=DefaultResponse)
def get_default():
    try:
        return network_store.get_online_network_default()
    except OnlineNotFound:
        raise _missing() from None
    except ValueError:
        raise _invalid_config() from None


@router.patch("/network/default", response_model=DefaultResponse)
def update_default(body: DefaultPatch):
    try:
        return network_store.update_online_network_default(body.rule.model_dump())
    except OnlineNotFound:
        raise _missing() from None
    except ValueError:
        raise _invalid_config() from None


@router.get("/sources/{source_id}/network", response_model=SourcePolicyResponse)
def get_source_policy(source_id: str):
    try:
        return network_store.get_source_network_policy(source_id)
    except OnlineNotFound:
        raise _missing() from None
    except ValueError:
        raise _invalid_config() from None


@router.patch("/sources/{source_id}/network", response_model=SourcePolicyResponse)
def update_source_policy(source_id: str, body: SourceNetworkPatch):
    try:
        return network_store.update_source_network_policy(source_id, body.model_dump(exclude_unset=True))
    except OnlineNotFound:
        raise _missing() from None
    except ValueError:
        raise _invalid_config() from None


@router.get("/sources/{source_id}/network/effective")
def effective_source_policy(source_id: str, purpose: NetworkPurpose):
    """Explain the server route without connecting to a proxy or provider."""
    try:
        return resolve_online_policy(source_id, purpose).public_summary()
    except OnlineNotFound:
        raise _missing() from None
    except ValueError:
        raise HTTPException(422, "网络配置当前不可用，请检查档案、凭据和证书配置",
                            headers={"Cache-Control": "no-store"}) from None
