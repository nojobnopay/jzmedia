"""Resolve explicit online network rules into server-only session snapshots."""

from dataclasses import dataclass, field
import hashlib
import json
import os
import ssl

import certifi

from .. import secrets
from ..online.models import SourceId
from pydantic import TypeAdapter, ValidationError
from .models import (ApplicationNetworkPolicy, NetworkProfileCreate,
                     NetworkProfilePublic, PURPOSES, SourceNetworkPolicy)


class NetworkPolicyError(ValueError):
    """A fixed diagnostic category; configuration and secrets are not echoed."""


def _revision(value):
    if type(value) is not int or value < 0:
        raise NetworkPolicyError("invalid_network_configuration")
    return value


def _credentials(ciphertext):
    if not isinstance(ciphertext, str):
        raise NetworkPolicyError("invalid_network_credentials")
    if not ciphertext:
        return "", ""
    try:
        value = json.loads(secrets.decrypt_str(ciphertext))
        if (not isinstance(value, dict) or set(value) != {"username", "password"}
                or not all(isinstance(item, str) for item in value.values())):
            raise ValueError
        return value["username"], value["password"]
    except (secrets.SecretError, ValueError, UnicodeError, OSError):
        raise NetworkPolicyError("invalid_network_credentials") from None


def _tls_context(profile):
    ca_file = profile.ca_file or os.environ.get("SSL_CERT_FILE", "")
    ca_dir = profile.ca_dir or os.environ.get("SSL_CERT_DIR", "")
    if any(not isinstance(value, str) or len(value) > 2048 or "\x00" in value
           for value in (ca_file, ca_dir)):
        raise NetworkPolicyError("invalid_ca_configuration")
    try:
        # trust_env=False also disables HTTPX certificate environment handling.
        # Capture it explicitly once; no proxy environment variables are read.
        if ca_file or ca_dir:
            context = ssl.create_default_context(cafile=ca_file or None, capath=ca_dir or None)
        else:
            context = ssl.create_default_context(cafile=certifi.where())
    except (OSError, ValueError, ssl.SSLError):
        raise NetworkPolicyError("invalid_ca_configuration") from None
    source = "profile" if profile.ca_file or profile.ca_dir else "environment" if ca_file or ca_dir else "default"
    return context, ca_file, ca_dir, source


@dataclass(frozen=True)
class NetworkSnapshot:
    """Retain one route, credentials, limits and trust context per operation.

    This is a server object, never a playback descriptor. Destination approval
    and pinned dialing are mandatory additional dependencies of the reader.
    """

    source_id: str
    purpose: str
    selected_by: str
    mode: str
    profile_id: str | None
    application_revision: int
    source_revision: int
    profile_revision: int
    connect_timeout_seconds: float
    read_timeout_seconds: float
    max_connections: int
    proxy_url: str
    certificate_source: str
    fingerprint: str
    proxy_auth: tuple[str, str] | None = field(repr=False)
    ssl_context: ssl.SSLContext = field(repr=False, compare=False)
    ca_file: str = field(repr=False)
    ca_dir: str = field(repr=False)

    def public_summary(self):
        """A credential-free administrative explanation of the effective rule."""
        return {
            "source_id": self.source_id, "purpose": self.purpose,
            "selected_by": self.selected_by, "mode": self.mode,
            "profile_id": self.profile_id, "application_revision": self.application_revision,
            "source_revision": self.source_revision, "profile_revision": self.profile_revision,
            "connect_timeout_seconds": self.connect_timeout_seconds,
            "read_timeout_seconds": self.read_timeout_seconds,
            "max_connections": self.max_connections,
            "certificate_source": self.certificate_source,
            "target_dns": "local_pinned",
            "destination_validation": "required_per_request", "fingerprint": self.fingerprint,
        }


def resolve_policy_state(state: dict, purpose: str) -> NetworkSnapshot:
    """Resolve one atomically loaded configuration without attempting fallback."""
    if purpose not in PURPOSES:
        raise NetworkPolicyError("unsupported_network_purpose")
    try:
        if (not isinstance(state, dict) or not isinstance(state.get("application"), dict)
                or not isinstance(state.get("source"), dict) or not isinstance(state.get("profiles"), dict)):
            raise NetworkPolicyError("invalid_network_configuration")
        application = state["application"]
        source = state["source"]
        source_id = TypeAdapter(SourceId).validate_python(source["source_id"])
        application_revision = _revision(application["revision"])
        source_revision = _revision(source["revision"])
        default = ApplicationNetworkPolicy.model_validate({"rule": application["rule"]}).rule
        policy = SourceNetworkPolicy.model_validate(source["policy"])
        rule = getattr(policy, purpose)
        selected_by = "purpose"
        if rule.mode == "inherit":
            rule, selected_by = policy.default, "source"
        if rule.mode == "inherit":
            rule, selected_by = default, "application"
        ciphertext = ""
        profile_revision = 0
        if rule.profile_id is None:
            profile = NetworkProfileCreate(name="Built-in direct", mode="direct")
        else:
            raw = state["profiles"].get(rule.profile_id)
            if raw is None:
                raise NetworkPolicyError("network_profile_missing")
            fields = {name: raw[name] for name in NetworkProfilePublic.model_fields if name != "has_credentials"}
            ciphertext = raw["credentials_encrypted"]
            username, password = _credentials(ciphertext)
            public = NetworkProfilePublic.model_validate({**fields, "has_credentials": bool(username or password)})
            if public.id != rule.profile_id or public.mode != rule.mode:
                raise NetworkPolicyError("network_profile_mode_mismatch")
            profile_revision = public.revision
            profile = NetworkProfileCreate.model_validate({
                **{name: getattr(public, name) for name in NetworkProfileCreate.model_fields
                   if name not in {"username", "password"}},
                "username": username, "password": password,
            })
        if profile.mode != rule.mode:
            raise NetworkPolicyError("network_profile_mode_mismatch")
    except NetworkPolicyError:
        raise
    except (KeyError, TypeError, ValueError, ValidationError):
        raise NetworkPolicyError("invalid_network_configuration") from None
    context, ca_file, ca_dir, certificate_source = _tls_context(profile)
    username, password = profile.username.get_secret_value(), profile.password.get_secret_value()
    # Ciphertext is hashed internally: neither it nor credentials are published.
    fingerprint = hashlib.sha256(json.dumps({
        "source_id": source_id, "purpose": purpose, "selected_by": selected_by,
        "rule": rule.model_dump(mode="json"), "application_revision": application_revision,
        "source_revision": source_revision, "profile_revision": profile_revision,
        "options": profile.model_dump(mode="json", exclude={"username", "password", "name"}),
        "credential_revision": hashlib.sha256(ciphertext.encode()).hexdigest(),
        "ca_file": ca_file, "ca_dir": ca_dir,
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return NetworkSnapshot(
        source_id=source_id, purpose=purpose, selected_by=selected_by, mode=rule.mode,
        profile_id=rule.profile_id, application_revision=application_revision,
        source_revision=source_revision, profile_revision=profile_revision,
        connect_timeout_seconds=profile.connect_timeout_seconds,
        read_timeout_seconds=profile.read_timeout_seconds,
        max_connections=profile.max_connections, proxy_url=profile.proxy_url,
        proxy_auth=(username, password) if username or password else None,
        ssl_context=context, ca_file=ca_file, ca_dir=ca_dir,
        certificate_source=certificate_source, fingerprint=fingerprint,
    )


def resolve_online_policy(source_id: str, purpose: str) -> NetworkSnapshot:
    from ..store.online_network import load_online_network_state
    return resolve_policy_state(load_online_network_state(source_id), purpose)
