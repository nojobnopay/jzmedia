"""Validated identities, administrator configuration, and playback contracts.

URL validation here is deliberately structural and performs no DNS lookup or
connection. The public-target and connected-address policy belongs to the
network layer before any future upstream request.
"""

import ipaddress
import re
from typing import Annotated, Literal
from urllib.parse import unquote, urlsplit, urlunsplit

from pydantic import (BaseModel, ConfigDict, Field, StringConstraints,
                      computed_field, field_validator, model_serializer, model_validator)


SourceId = Annotated[str, StringConstraints(min_length=36, max_length=36,
                                         pattern=r"^src_[0-9a-f]{32}$")]
ItemId = Annotated[str, StringConstraints(min_length=36, max_length=36,
                                       pattern=r"^itm_[0-9a-f]{32}$")]
EpisodeId = Annotated[str, StringConstraints(min_length=35, max_length=35,
                                          pattern=r"^ep_[0-9a-f]{32}$")]
LineId = Annotated[str, StringConstraints(min_length=35, max_length=35,
                                       pattern=r"^ln_[0-9a-f]{32}$")]
Provider = Literal["maccms", "open_catalog"]

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True,
                              validate_default=True, hide_input_in_errors=True)


def normalize_external_id(value: str | int) -> str:
    """Keep provider identity independent of title, ordering, or playback URL."""
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str):
        raise ValueError("external_id must be text or an integer")
    if _CONTROL.search(value):
        raise ValueError("external_id must be a single-line identifier")
    value = value.strip()
    if not value or len(value) > 512:
        raise ValueError("external_id must contain 1 to 512 characters")
    return value


def normalize_host(value: str) -> str:
    """Normalize one exact host, without accepting ports or wildcard patterns."""
    if not isinstance(value, str) or not value or len(value) > 253:
        raise ValueError("allowed host must be an exact hostname or public IP")
    if re.search(r"[\s/@?#%\\\x00-\x1f\x7f]", value) or "*" in value:
        raise ValueError("allowed host must not contain a URL or wildcard")
    if value.endswith(".."):
        raise ValueError("allowed host has an invalid hostname")
    value = value.removesuffix(".").lower()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        if ":" in value or "[" in value or "]" in value:
            raise ValueError("allowed host must not contain a port") from None
        if all(re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", label) for label in value.split(".")):
            raise ValueError("allowed host must not use an abbreviated numeric IP")
        try:
            value = value.encode("idna").decode("ascii")
        except UnicodeError:
            raise ValueError("allowed host has an invalid hostname") from None
        if (len(value) > 253 or not all(_LABEL.fullmatch(label) for label in value.split("."))
                or value == "localhost" or value.endswith((".localhost", ".local"))):
            raise ValueError("allowed host has an invalid or local hostname")
        return value
    if not address.is_global:
        raise ValueError("allowed host must not be a local or reserved IP")
    return address.compressed


def validate_api_base_url(value: str) -> str:
    """Validate only URL syntax and literal hosts, never network reachability."""
    if not isinstance(value, str) or _CONTROL.search(value):
        raise ValueError("API address must be a single-line HTTP/HTTPS URL")
    value = value.strip()
    if not value or len(value) > 2048 or re.search(r"[\s\\]", value):
        raise ValueError("API address must be a valid HTTP/HTTPS URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or "@" in parsed.netloc or "?" in value or "#" in value or port == 0):
            raise ValueError
        host = normalize_host(parsed.hostname)
        decoded_path = unquote(parsed.path)
        if (_CONTROL.search(decoded_path) or "\\" in decoded_path
                or any(part in {".", ".."} for part in decoded_path.split("/"))):
            raise ValueError
    except (ValueError, UnicodeError):
        raise ValueError("API address must be HTTP/HTTPS without credentials, query, or fragment") from None
    authority = f"[{host}]" if ":" in host else host
    if port is not None:
        authority += f":{port}"
    # The endpoint path and its trailing slash can be meaningful to providers.
    return urlunsplit((parsed.scheme, authority, parsed.path, "", ""))


def _single_line(value: str) -> str:
    if _CONTROL.search(value):
        raise ValueError("text must not contain control characters")
    return value.strip()


class OnlineSettings(_Contract):
    enabled: bool = False


class OnlineSettingsPatch(_Contract):
    enabled: bool


class OnlineProviderOptions(_Contract):
    search_action: Literal["list", "detail", "videolist"] = "list"
    detail_action: Literal["detail", "videolist"] = "detail"


class OnlineSourceCreate(_Contract):
    name: str = Field(min_length=1, max_length=128)
    provider: Provider
    api_base_url: str = Field(max_length=2048)
    allowed_hosts: list[str] = Field(default_factory=list, max_length=128)
    enabled: bool = False
    priority: int = Field(default=100, ge=0, le=1000)
    provider_options: OnlineProviderOptions = Field(default_factory=OnlineProviderOptions)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value):
        value = _single_line(value)
        if not value:
            raise ValueError("name must not be blank")
        return value

    @field_validator("api_base_url")
    @classmethod
    def normalize_url(cls, value):
        return validate_api_base_url(value)

    @field_validator("allowed_hosts")
    @classmethod
    def normalize_hosts(cls, values):
        return list(dict.fromkeys(normalize_host(value) for value in values))

    @model_validator(mode="after")
    def include_api_host(self):
        host = normalize_host(urlsplit(self.api_base_url).hostname)
        if host not in self.allowed_hosts:
            if len(self.allowed_hosts) >= 128:
                raise ValueError("allowed_hosts has no room for the API host")
            object.__setattr__(self, "allowed_hosts", [*self.allowed_hosts, host])
        return self


class OnlineSourcePatch(_Contract):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    api_base_url: str | None = Field(default=None, max_length=2048)
    allowed_hosts: list[str] | None = Field(default=None, max_length=128)
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=1000)
    provider_options: OnlineProviderOptions | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, values):
        if isinstance(values, dict) and any(value is None for value in values.values()):
            raise ValueError("source changes cannot contain null values")
        return values

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value):
        return OnlineSourceCreate.normalize_name(value) if value is not None else None

    @field_validator("api_base_url")
    @classmethod
    def normalize_url(cls, value):
        return validate_api_base_url(value) if value is not None else None

    @field_validator("allowed_hosts")
    @classmethod
    def normalize_hosts(cls, values):
        return OnlineSourceCreate.normalize_hosts(values) if values is not None else None


class OnlineSource(OnlineSourceCreate):
    id: SourceId
    created_at: int = Field(ge=0)
    updated_at: int = Field(ge=0)


class OnlineItem(_Contract):
    id: ItemId
    source_id: SourceId
    external_id: str
    kind: Literal["movie", "tv"]
    title: str = Field(default="", max_length=512)
    created_at: int = Field(ge=0)
    updated_at: int = Field(ge=0)

    @field_validator("external_id", mode="before")
    @classmethod
    def normalize_identity(cls, value):
        return normalize_external_id(value)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value):
        return _single_line(value)


class OnlineEpisode(_Contract):
    id: EpisodeId
    item_id: ItemId
    external_id: str
    title: str = Field(default="", max_length=512)
    season: int | None = Field(default=None, ge=0)
    episode: int | None = Field(default=None, ge=0)
    sort_order: int = Field(default=0, ge=0)
    created_at: int = Field(ge=0)
    updated_at: int = Field(ge=0)

    @field_validator("external_id", mode="before")
    @classmethod
    def normalize_identity(cls, value):
        return normalize_external_id(value)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value):
        return _single_line(value)


class OnlinePlaybackIdentity(_Contract):
    source_id: SourceId
    item_id: ItemId
    kind: Literal["movie", "episode"]
    episode_id: EpisodeId | None = None
    line_id: LineId | None = None

    @model_serializer(mode="wrap")
    def serialize_identity(self, handler):
        values = handler(self)
        # Existing P1 clients retain their exact identity representation.
        # P2 line selection is an optional stable identifier, never a URL.
        if self.line_id is None:
            values.pop("line_id", None)
        return values

    @model_validator(mode="after")
    def consistent_episode_identity(self):
        if (self.kind == "episode") != (self.episode_id is not None):
            raise ValueError("episode identity must match the playback kind")
        return self


class OnlinePlaybackDescriptor(_Contract):
    identity: OnlinePlaybackIdentity
    session_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{32,128}$")]
    transport: Literal["mp4", "hls"]
    processing: Literal["passthrough"] = "passthrough"
    seekable: bool
    duration_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    time_offset_seconds: float = Field(default=0.0, ge=0, le=0, allow_inf_nan=False)
    expires_at: int = Field(gt=0)

    @field_validator("session_id")
    @classmethod
    def safe_session_identifier(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", value):
            raise ValueError("session_id must be an opaque URL-safe identifier")
        return value

    @computed_field
    @property
    def media_url(self) -> str:
        """Only a server session path crosses the client contract boundary."""
        return f"/api/online/sessions/{self.session_id}/media"
