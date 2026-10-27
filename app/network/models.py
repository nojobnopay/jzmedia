"""Network configuration contracts. Validation never opens a connection."""

import ipaddress
import os
import re
from typing import Annotated, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import (BaseModel, ConfigDict, Field, SecretStr, StringConstraints,
                      field_validator, model_validator)


ProfileId = Annotated[str, StringConstraints(pattern=r"^np_[0-9a-f]{32}$", min_length=35, max_length=35)]
NetworkPurpose = Literal["api", "media", "image", "subtitle", "subscription"]
PURPOSES = ("api", "media", "image", "subtitle", "subscription")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True,
                              validate_default=True, hide_input_in_errors=True)


def validate_proxy_url(value: str) -> str:
    """Validate a configured proxy endpoint, not a public media destination.

    A NAS proxy may intentionally be on localhost or a private network. Its
    credentials are separate write-only fields rather than URL userinfo.
    """
    if value == "":
        return ""
    if not isinstance(value, str) or _CONTROL.search(value):
        raise ValueError("proxy address must be a single-line HTTP/HTTPS endpoint")
    value = value.strip()
    try:
        if not value or len(value) > 2048 or re.search(r"[\s\\]", value):
            raise ValueError
        parts = urlsplit(value)
        port = parts.port
        if (parts.scheme not in {"http", "https"} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or "@" in parts.netloc or "?" in value or "#" in value
                or parts.path not in {"", "/"} or port == 0):
            raise ValueError
        host = parts.hostname
        if "%" in host:
            raise ValueError
        try:
            host = ipaddress.ip_address(host).compressed
        except ValueError:
            host = host.removesuffix(".").encode("idna").decode("ascii").lower()
            if len(host) > 253 or not all(_LABEL.fullmatch(label) for label in host.split(".")):
                raise ValueError
        authority = f"[{host}]" if ":" in host else host
        if port is not None:
            authority += f":{port}"
        return urlunsplit((parts.scheme, authority, "", "", ""))
    except (ValueError, UnicodeError):
        raise ValueError("proxy address must be HTTP/HTTPS without credentials, path, query, or fragment") from None


def validate_ca_path(value: str) -> str:
    if not isinstance(value, str) or _CONTROL.search(value) or len(value) > 2048:
        raise ValueError("CA path must be an absolute single-line filesystem path")
    if value and not os.path.isabs(value):
        raise ValueError("CA path must be absolute")
    return value


def _name(value: str) -> str:
    if _CONTROL.search(value) or not value.strip():
        raise ValueError("profile name must be nonempty single-line text")
    return value.strip()


def _credential(value: SecretStr, *, username=False) -> SecretStr:
    raw = value.get_secret_value()
    if len(raw) > 1024 or _CONTROL.search(raw) or username and ":" in raw:
        raise ValueError("proxy credential has an invalid format")
    return value


class _ProfileFields(_Contract):
    name: str = Field(min_length=1, max_length=128)
    mode: Literal["direct", "proxy"]
    proxy_url: str = Field(default="", max_length=2048)
    connect_timeout_seconds: float = Field(default=5.0, ge=1, le=30, allow_inf_nan=False)
    read_timeout_seconds: float = Field(default=15.0, ge=1, le=120, allow_inf_nan=False)
    max_connections: int = Field(default=4, ge=1, le=16)
    ca_file: str = ""
    ca_dir: str = ""

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value):
        return _name(value)

    @field_validator("proxy_url")
    @classmethod
    def normalize_proxy(cls, value):
        return validate_proxy_url(value)

    @field_validator("ca_file", "ca_dir")
    @classmethod
    def normalize_ca(cls, value):
        return validate_ca_path(value)

    @model_validator(mode="after")
    def consistent_endpoint(self):
        if (self.mode == "proxy") != bool(self.proxy_url):
            raise ValueError("proxy profiles require an endpoint; direct profiles cannot have one")
        return self


class NetworkProfileCreate(_ProfileFields):
    username: SecretStr = Field(default_factory=lambda: SecretStr(""), repr=False)
    password: SecretStr = Field(default_factory=lambda: SecretStr(""), repr=False)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value):
        return _credential(value, username=True)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value):
        return _credential(value)

    @model_validator(mode="after")
    def direct_has_no_credentials(self):
        if self.mode == "direct" and (self.username.get_secret_value() or self.password.get_secret_value()):
            raise ValueError("direct profiles cannot contain proxy credentials")
        return self


class NetworkProfilePatch(_Contract):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    proxy_url: str | None = Field(default=None, max_length=2048)
    username: SecretStr | None = Field(default=None, repr=False)
    password: SecretStr | None = Field(default=None, repr=False)
    connect_timeout_seconds: float | None = Field(default=None, ge=1, le=30, allow_inf_nan=False)
    read_timeout_seconds: float | None = Field(default=None, ge=1, le=120, allow_inf_nan=False)
    max_connections: int | None = Field(default=None, ge=1, le=16)
    ca_file: str | None = None
    ca_dir: str | None = None
    clear_credentials: bool = False

    @model_validator(mode="before")
    @classmethod
    def reject_null_and_ambiguous_clear(cls, values):
        if isinstance(values, dict):
            if any(value is None for value in values.values()):
                raise ValueError("profile changes cannot contain null values")
            if values.get("clear_credentials") and ({"username", "password"} & values.keys()):
                raise ValueError("cannot clear and replace proxy credentials together")
        return values

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value):
        return _name(value) if value is not None else None

    @field_validator("proxy_url")
    @classmethod
    def normalize_proxy(cls, value):
        return validate_proxy_url(value) if value is not None else None

    @field_validator("ca_file", "ca_dir")
    @classmethod
    def normalize_ca(cls, value):
        return validate_ca_path(value) if value is not None else None

    @field_validator("username")
    @classmethod
    def validate_username(cls, value):
        return _credential(value, username=True) if value is not None else None

    @field_validator("password")
    @classmethod
    def validate_password(cls, value):
        return _credential(value) if value is not None else None


class NetworkProfilePublic(_ProfileFields):
    id: ProfileId
    has_credentials: bool
    revision: int = Field(ge=1)
    created_at: int = Field(ge=0)
    updated_at: int = Field(ge=0)

    @model_validator(mode="after")
    def direct_has_no_auth(self):
        if self.mode == "direct" and self.has_credentials:
            raise ValueError("direct profiles cannot contain proxy credentials")
        return self


class NetworkRule(_Contract):
    mode: Literal["inherit", "direct", "proxy"] = "inherit"
    profile_id: ProfileId | None = None

    @model_validator(mode="after")
    def consistent_reference(self):
        if self.mode == "inherit" and self.profile_id is not None:
            raise ValueError("inherited rules cannot name a profile")
        if self.mode == "proxy" and self.profile_id is None:
            raise ValueError("proxy rules must name a profile")
        return self


class ApplicationNetworkPolicy(_Contract):
    rule: NetworkRule = Field(default_factory=lambda: NetworkRule(mode="direct"))

    @model_validator(mode="after")
    def require_effective_default(self):
        if self.rule.mode == "inherit":
            raise ValueError("application network default cannot inherit")
        return self


class SourceNetworkPolicy(_Contract):
    default: NetworkRule = Field(default_factory=NetworkRule)
    api: NetworkRule = Field(default_factory=NetworkRule)
    media: NetworkRule = Field(default_factory=NetworkRule)
    image: NetworkRule = Field(default_factory=NetworkRule)
    subtitle: NetworkRule = Field(default_factory=NetworkRule)
    subscription: NetworkRule = Field(default_factory=NetworkRule)


class SourceNetworkPatch(_Contract):
    default: NetworkRule | None = None
    api: NetworkRule | None = None
    media: NetworkRule | None = None
    image: NetworkRule | None = None
    subtitle: NetworkRule | None = None
    subscription: NetworkRule | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, values):
        if isinstance(values, dict) and any(value is None for value in values.values()):
            raise ValueError("source network changes cannot contain null values")
        return values
