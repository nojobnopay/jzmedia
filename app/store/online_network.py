"""Atomic network configuration with encrypted proxy credentials and no I/O."""

from contextlib import closing, contextmanager
import json
import time
import uuid

from pydantic import SecretStr

from .. import secrets
from ..network.models import (NetworkProfileCreate, NetworkProfilePatch,
                              NetworkProfilePublic, NetworkRule,
                              SourceNetworkPatch, SourceNetworkPolicy)
from . import _base
from .online import OnlineNotFound, _source


DEFAULT_KEY = "online_network_default"
_PROFILE_FIELDS = ("name", "mode", "proxy_url", "connect_timeout_seconds",
                   "read_timeout_seconds", "max_connections", "ca_file", "ca_dir")


class NetworkConflict(ValueError):
    """A configuration change would break an existing network rule."""


@contextmanager
def _connection(*, write=False, snapshot=False):
    with _base._lock, closing(_base._conn()) as connection, connection:
        connection.execute("PRAGMA foreign_keys=ON")
        if write:
            connection.execute("BEGIN IMMEDIATE")
        elif snapshot:
            connection.execute("BEGIN")
        yield connection


def _public_profile(row):
    if row is None:
        return None
    raw = dict(row)
    public = {key: raw[key] for key in (*_PROFILE_FIELDS, "id", "revision", "created_at", "updated_at")}
    public["has_credentials"] = bool(raw["credentials_encrypted"])
    return NetworkProfilePublic.model_validate(public).model_dump(mode="json")


def _secret_value(value):
    return value.get_secret_value() if isinstance(value, SecretStr) else value


def _encode_credentials(username, password):
    if not username and not password:
        return ""
    try:
        return secrets.encrypt_str(json.dumps({"username": username, "password": password}, ensure_ascii=False))
    except (secrets.SecretError, ValueError, TypeError):
        raise ValueError("网络代理凭据无法保存") from None


def _decode_credentials(value):
    if not value:
        return {"username": "", "password": ""}
    try:
        decoded = json.loads(secrets.decrypt_str(value))
        if (not isinstance(decoded, dict) or set(decoded) != {"username", "password"}
                or not all(isinstance(item, str) for item in decoded.values())):
            raise ValueError
        return decoded
    except (secrets.SecretError, ValueError, TypeError):
        raise ValueError("网络代理凭据无法读取") from None


def list_network_profiles() -> list[dict]:
    with _connection() as connection:
        return [_public_profile(row) for row in connection.execute(
            "SELECT * FROM network_profiles ORDER BY created_at,id")]


def get_network_profile(profile_id: str) -> dict | None:
    with _connection() as connection:
        return _public_profile(connection.execute(
            "SELECT * FROM network_profiles WHERE id=?", (profile_id,)).fetchone())


def create_network_profile(values: dict) -> dict:
    config = NetworkProfileCreate.model_validate(values)
    values = config.model_dump(mode="python")
    encrypted = _encode_credentials(_secret_value(values.pop("username")),
                                    _secret_value(values.pop("password")))
    now = int(time.time())
    record = {**values, "id": "np_" + uuid.uuid4().hex, "credentials_encrypted": encrypted,
              "revision": 1, "created_at": now, "updated_at": now}
    with _connection(write=True) as connection:
        connection.execute(
            "INSERT INTO network_profiles(id,name,mode,proxy_url,credentials_encrypted,"
            "connect_timeout_seconds,read_timeout_seconds,max_connections,ca_file,ca_dir,revision,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            tuple(record[key] for key in ("id", "name", "mode", "proxy_url", "credentials_encrypted",
                                          "connect_timeout_seconds", "read_timeout_seconds", "max_connections",
                                          "ca_file", "ca_dir", "revision", "created_at", "updated_at")),
        )
    return _public_profile(record)


def update_network_profile(profile_id: str, changes: dict) -> dict:
    patch = NetworkProfilePatch.model_validate(changes).model_dump(mode="python", exclude_unset=True)
    with _connection(write=True) as connection:
        row = connection.execute("SELECT * FROM network_profiles WHERE id=?", (profile_id,)).fetchone()
        if row is None:
            raise OnlineNotFound("网络档案不存在")
        previous = dict(row)
        clear = patch.pop("clear_credentials", False)
        credential_changes = {key: _secret_value(patch.pop(key))
                              for key in ("username", "password") if key in patch}
        if not patch and not credential_changes and not clear:
            return _public_profile(previous)
        if clear and credential_changes:
            raise ValueError("不能同时填写和清除代理凭据")
        credentials = ({"username": "", "password": ""} if clear
                       else _decode_credentials(previous["credentials_encrypted"]) if credential_changes
                       else {"username": "", "password": ""})
        credentials.update(credential_changes)
        candidate = NetworkProfileCreate.model_validate({
            **{key: previous[key] for key in _PROFILE_FIELDS}, **patch, **credentials,
        }).model_dump(mode="python")
        encrypted = (_encode_credentials(**credentials) if credential_changes or clear
                     else previous["credentials_encrypted"])
        record = {**previous, **{key: candidate[key] for key in _PROFILE_FIELDS},
                  "credentials_encrypted": encrypted, "revision": previous["revision"] + 1,
                  "updated_at": int(time.time())}
        connection.execute(
            "UPDATE network_profiles SET name=?,proxy_url=?,credentials_encrypted=?,connect_timeout_seconds=?,"
            "read_timeout_seconds=?,max_connections=?,ca_file=?,ca_dir=?,revision=?,updated_at=? WHERE id=?",
            tuple(record[key] for key in ("name", "proxy_url", "credentials_encrypted", "connect_timeout_seconds",
                                          "read_timeout_seconds", "max_connections", "ca_file", "ca_dir", "revision",
                                          "updated_at", "id")),
        )
    return _public_profile(record)


def _application(connection):
    row = connection.execute("SELECT value FROM app_settings WHERE key=?", (DEFAULT_KEY,)).fetchone()
    if row is None:
        return {"rule": NetworkRule(mode="direct").model_dump(mode="json"), "revision": 0}
    try:
        value = json.loads(row["value"])
        if (not isinstance(value, dict) or set(value) != {"rule", "revision"}
                or type(value["revision"]) is not int or value["revision"] < 1):
            raise ValueError
        rule = NetworkRule.model_validate(value["rule"])
        if rule.mode == "inherit":
            raise ValueError
        return {"rule": rule.model_dump(mode="json"), "revision": value["revision"]}
    except (ValueError, TypeError):
        raise ValueError("在线默认网络配置无效") from None


def _source_policy(connection, source_id):
    if connection.execute("SELECT 1 FROM online_sources WHERE id=?", (source_id,)).fetchone() is None:
        raise OnlineNotFound("在线来源不存在")
    row = connection.execute("SELECT * FROM online_network_policies WHERE source_id=?", (source_id,)).fetchone()
    if row is None:
        return {"source_id": source_id, "policy": SourceNetworkPolicy().model_dump(mode="json"), "revision": 0}
    try:
        policy = SourceNetworkPolicy.model_validate(json.loads(row["rules_json"]))
    except (ValueError, TypeError):
        raise ValueError("来源网络配置无效") from None
    return {"source_id": source_id, "policy": policy.model_dump(mode="json"), "revision": row["revision"]}


def _validate_rule(connection, value):
    rule = NetworkRule.model_validate(value)
    if rule.profile_id is not None:
        row = connection.execute("SELECT mode FROM network_profiles WHERE id=?", (rule.profile_id,)).fetchone()
        if row is None:
            raise OnlineNotFound("网络档案不存在")
        if row["mode"] != rule.mode:
            raise ValueError("网络规则与档案类型不匹配")
    return rule.model_dump(mode="json")


def get_online_network_default() -> dict:
    with _connection(snapshot=True) as connection:
        current = _application(connection)
        _validate_rule(connection, current["rule"])
        return current


def update_online_network_default(rule: dict) -> dict:
    candidate = NetworkRule.model_validate(rule)
    if candidate.mode == "inherit":
        raise ValueError("应用默认网络不能继续继承")
    with _connection(write=True) as connection:
        normalized = _validate_rule(connection, candidate.model_dump(mode="json"))
        try:
            current = _application(connection)
        except ValueError:
            # A full replacement can repair a corrupt setting; reads still fail
            # closed instead of guessing a connection path.
            current = {"revision": 0}
        updated = {"rule": normalized, "revision": current["revision"] + 1}
        connection.execute(
            "INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (DEFAULT_KEY, json.dumps(updated), int(time.time())),
        )
    return updated


def get_source_network_policy(source_id: str) -> dict:
    with _connection(snapshot=True) as connection:
        current = _source_policy(connection, source_id)
        for value in current["policy"].values():
            _validate_rule(connection, value)
        return current


def update_source_network_policy(source_id: str, changes: dict) -> dict:
    patch = SourceNetworkPatch.model_validate(changes).model_dump(mode="json", exclude_unset=True)
    with _connection(write=True) as connection:
        current = _source_policy(connection, source_id)
        candidate = SourceNetworkPolicy.model_validate({**current["policy"], **patch}).model_dump(mode="json")
        for value in candidate.values():
            _validate_rule(connection, value)
        if not patch:
            return current
        revision = current["revision"] + 1
        connection.execute(
            "INSERT INTO online_network_policies(source_id,rules_json,revision,updated_at) VALUES(?,?,?,?)"
            " ON CONFLICT(source_id) DO UPDATE SET rules_json=excluded.rules_json,"
            "revision=excluded.revision,updated_at=excluded.updated_at",
            (source_id, json.dumps(candidate), revision, int(time.time())),
        )
    return {"source_id": source_id, "policy": candidate, "revision": revision}


def delete_network_profile(profile_id: str) -> None:
    with _connection(write=True) as connection:
        if connection.execute("SELECT 1 FROM network_profiles WHERE id=?", (profile_id,)).fetchone() is None:
            raise OnlineNotFound("网络档案不存在")
        default = _application(connection)
        if default["rule"]["profile_id"] == profile_id:
            raise NetworkConflict("网络档案正在被引用")
        for row in connection.execute("SELECT rules_json FROM online_network_policies"):
            try:
                policy = SourceNetworkPolicy.model_validate(json.loads(row["rules_json"])).model_dump(mode="json")
            except (ValueError, TypeError):
                raise NetworkConflict("已有来源网络配置无效，暂不能删除档案") from None
            if any(rule["profile_id"] == profile_id for rule in policy.values()):
                raise NetworkConflict("网络档案正在被引用")
        connection.execute("DELETE FROM network_profiles WHERE id=?", (profile_id,))


def load_online_network_state(source_id: str) -> dict:
    """Read one consistent server-only snapshot, including referenced ciphertext."""
    with _connection(snapshot=True) as connection:
        application = _application(connection)
        source = _source_policy(connection, source_id)
        referenced = {rule["profile_id"] for rule in (application["rule"], *source["policy"].values())
                      if rule["profile_id"] is not None}
        for rule in (application["rule"], *source["policy"].values()):
            _validate_rule(connection, rule)
        profiles = {profile_id: dict(connection.execute(
            "SELECT * FROM network_profiles WHERE id=?", (profile_id,)).fetchone())
                    for profile_id in referenced}
        source_config = _source(connection.execute("SELECT * FROM online_sources WHERE id=?", (source_id,)).fetchone())
        enabled = connection.execute("SELECT value FROM app_settings WHERE key='online_enabled'").fetchone()
        return {"application": application, "source": source, "profiles": profiles,
                "source_config": source_config, "online_enabled": bool(enabled and enabled["value"] == "true")}
