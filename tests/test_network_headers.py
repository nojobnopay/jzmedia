"""Header isolation across exact origins without requests or browser state."""

from dataclasses import FrozenInstanceError

import httpx
import pytest

from app.network.headers import (HEADER_BYTES_LIMIT, HEADER_VALUE_LIMIT, HeaderGrant,
                                 HeaderPolicy, HeaderPolicyError)
from app.network.targets import ValidatedTarget


SOURCE_ID = "src_" + "1" * 32


def target(host="cdn.example", *, scheme="https", port=443):
    authority = f"{host}:{port}"
    return ValidatedTarget(f"{scheme}://{authority}/a", scheme, host, port,
                           ("8.8.8.8",), f"{scheme}://{authority}", authority)


def test_ordinary_headers_allow_only_small_playback_set_with_canonical_names():
    value = HeaderPolicy(SOURCE_ID).headers_for(target(), {
        "accept": "application/vnd.apple.mpegurl", "Accept-Language": "zh-CN,en;q=0.5",
        "USER-AGENT": "jzmedia/1", "Range": "bytes=0-", "if-range": '"etag"',
        "If-None-Match": '"etag"', "If-Modified-Since": "Wed, 21 Oct 2015 07:28:00 GMT",
    })
    assert set(value) == {"Accept", "Accept-Language", "User-Agent", "Range", "If-Range",
                          "If-None-Match", "If-Modified-Since"}
    assert value["Range"] == "bytes=0-"


@pytest.mark.parametrize("name", [
    "Authorization", "Cookie", "Referer", "Origin", "Host", "Set-Cookie", "Proxy-Authorization",
    "Proxy-Authenticate", "Proxy-Connection", "Connection", "Keep-Alive", "TE", "Trailer",
    "Transfer-Encoding", "Upgrade", "Content-Length", "X-Api-Token", "X-Viewing-Session",
    "X-Tv-Cast-Token", "X-Receiver-Token", "X-Forwarded-Host", "X-Custom-Extension",
])
def test_sensitive_and_hop_by_hop_caller_headers_are_rejected_without_echo(name):
    with pytest.raises(HeaderPolicyError, match="^upstream_header_not_allowed$") as caught:
        HeaderPolicy(SOURCE_ID).headers_for(target(), {name: "private-secret"})
    assert "private-secret" not in str(caught.value)


def test_explicit_credential_grant_is_frozen_hidden_and_applies_to_its_exact_origin():
    supplied = {"Authorization": "Bearer private-secret", "Cookie": "upstream_session=private-cookie",
                "Referer": "https://provider.example/", "Origin": "https://provider.example"}
    grant = HeaderGrant("HTTPS://CDN.EXAMPLE/", supplied)
    supplied["Authorization"] = "Bearer replaced-secret"
    value = HeaderPolicy(SOURCE_ID, [grant])
    approved = value.headers_for(target(), {"Range": "bytes=100-"})
    assert grant.origin == "https://cdn.example:443"
    assert approved == {"Authorization": "Bearer private-secret", "Cookie": "upstream_session=private-cookie",
                        "Referer": "https://provider.example/", "Origin": "https://provider.example",
                        "Range": "bytes=100-"}
    assert "private-secret" not in repr(grant) + repr(value)
    assert "private-cookie" not in repr(grant) + repr(value)
    with pytest.raises(FrozenInstanceError):
        grant.headers = ()
    approved["Cookie"] = "changed=1"
    assert value.headers_for(target())["Cookie"] == "upstream_session=private-cookie"


def test_each_redirect_origin_gets_only_its_own_explicit_grants():
    first = HeaderGrant("https://cdn.example", {"Authorization": "Bearer origin-a-secret", "Cookie": "a=secret"})
    second = HeaderGrant("https://media.example:8443", {"Referer": "https://provider-b.example/"})
    value = HeaderPolicy(SOURCE_ID, [first, second])
    ordinary = {"Range": "bytes=0-"}
    assert value.headers_for(target(), ordinary)["Authorization"] == "Bearer origin-a-secret"
    assert value.headers_for(target("media.example", port=8443), ordinary) == {
        "Range": "bytes=0-", "Referer": "https://provider-b.example/"}
    assert value.headers_for(target("media.example"), ordinary) == ordinary
    assert value.headers_for(target(scheme="http", port=80), ordinary) == ordinary
    assert value.headers_for(target(port=8443), ordinary) == ordinary


def test_callers_cannot_supply_a_granted_secret_even_if_it_matches_the_server_grant():
    value = HeaderPolicy(SOURCE_ID, [HeaderGrant("https://cdn.example", {"Authorization": "Bearer secret"})])
    with pytest.raises(HeaderPolicyError, match="^upstream_header_not_allowed$"):
        value.headers_for(target(), {"Authorization": "Bearer secret"})


@pytest.mark.parametrize("origin", [
    "ftp://cdn.example", "https://user:private-secret@cdn.example", "https://cdn.example/path",
    "https://cdn.example?private-secret", "https://cdn.example#", "https://cdn.example:0",
    "https://cdn.example:", "https://cdn.example\\evil", "https://%63dn.example",
    "https://[fe80::1%25eth0]", "https://cdn.example\n",
])
def test_grant_origin_is_exact_and_structural_errors_hide_secrets(origin):
    with pytest.raises(HeaderPolicyError, match="^invalid_header_grant_origin$") as caught:
        HeaderGrant(origin, {"Authorization": "Bearer private-secret"})
    assert "private-secret" not in repr(caught.value)


@pytest.mark.parametrize("name", ["X-Api-Token", "X-Viewing-Session", "Host", "Connection",
                                  "Set-Cookie", "Proxy-Authorization", "Range"])
def test_server_grants_cannot_authorize_application_or_transport_fields(name):
    with pytest.raises(HeaderPolicyError, match="^upstream_header_not_allowed$"):
        HeaderGrant("https://cdn.example", {name: "private-secret"})


@pytest.mark.parametrize("cookie", [
    "jz_viewing=private-secret", "a=1; jz_viewing=private-secret", "JZ_VIEWING=private-secret; a=1",
    '"jz_viewing"=private-secret', "jz_viewing",
])
def test_jzmedia_viewing_cookie_is_never_authorized_as_an_upstream_cookie(cookie):
    with pytest.raises(HeaderPolicyError, match="^invalid_upstream_headers$"):
        HeaderGrant("https://cdn.example", {"Cookie": cookie})


@pytest.mark.parametrize("headers", [
    {"Accept": "text/html\r\nAuthorization: private-secret"}, {"Accept": "\x00"},
    {"Accept": "\x7f"}, {"Accept": "value\tvalue"}, {"Accept": "非ASCII"},
    {"Accept\n": "value"}, {"": "value"}, {"A" * 65: "value"}, {"Accept": 1},
    [("Accept", "a"), ("accept", "b")], [("Accept",)], "Accept: value",
    {"Accept": "a" * (HEADER_VALUE_LIMIT + 1)}, [("Accept", "a")] * 33,
])
def test_untrusted_header_shapes_controls_non_ascii_duplicates_and_limits_are_rejected(headers):
    with pytest.raises(HeaderPolicyError):
        HeaderPolicy(SOURCE_ID).headers_for(target(), headers)


def test_httpx_duplicate_header_names_are_not_silently_coalesced():
    headers = httpx.Headers([("Accept", "a"), ("Accept", "b")])
    with pytest.raises(HeaderPolicyError, match="^upstream_header_not_allowed$"):
        HeaderPolicy(SOURCE_ID).headers_for(target(), headers)


def test_combined_ordinary_and_granted_header_byte_budget_is_enforced():
    grant = HeaderGrant("https://cdn.example", {"Cookie": "c=" + "x" * 8000})
    value = HeaderPolicy(SOURCE_ID, [grant])
    assert len(value.headers_for(target(), {"Accept": "x" * 8000})["Accept"]) == 8000
    with pytest.raises(HeaderPolicyError, match="^invalid_upstream_headers$"):
        value.headers_for(target(), {"Accept": "x" * HEADER_VALUE_LIMIT, "User-Agent": "x" * 1000})
    assert HEADER_BYTES_LIMIT < 3 * HEADER_VALUE_LIMIT


def test_duplicate_origin_grants_and_invalid_source_ids_fail_closed():
    first = HeaderGrant("https://cdn.example", {"Authorization": "Bearer first-secret"})
    second = HeaderGrant("https://cdn.example:443/", {"Authorization": "Bearer second-secret"})
    with pytest.raises(HeaderPolicyError, match="^invalid_header_policy$"):
        HeaderPolicy(SOURCE_ID, [first, second])
    with pytest.raises(HeaderPolicyError, match="^invalid_header_policy$"):
        HeaderPolicy("bad-source")
