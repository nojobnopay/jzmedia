#!/usr/bin/env python3
"""Bounded, read-only source diagnostics; never decode or save media.

Requires the repository's httpx dependency. Candidates are supplied by the caller:
{"sources": [{"id": "example", "type": "maccms", "api": "https://.../",
 "samples": [{"id": "123", "query": "title", "playlist": "https://..."}]}]}
Types: maccms, peertube, http. A sample may have media (MP4), playlist (HLS),
or both. No samples means only the source API is checked. type can be inferred
from /provide/vod or /api/v1/ for existing P0 candidate files.

Exit 0: all configured checks passed. Exit 1: a valid report contains failed
checks. Exit 2: configuration, dependency or report-writing failure. HTTP success
and sampled bytes do not establish playback, content rights or lasting uptime.
"""

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import sys
import time
from urllib.parse import urljoin, urlsplit

try:
    import httpx
except ImportError:
    httpx = None


class ProbeFailure(Exception):
    """A deliberately public, credential-free failure category."""


def redacted_url(value):
    """Do not persist URL credentials, signed queries or path-based tokens."""
    try:
        parts = urlsplit(str(value))
        host = parts.hostname or "invalid"
        if ":" in host:
            host = f"[{host}]"
        port = f":{parts.port}" if parts.port else ""
        path_hash = hashlib.sha256(parts.path.encode()).hexdigest()[:16]
        query = "?<redacted>" if parts.query else ""
        return f"{parts.scheme}://{host}{port}/<path-{path_hash}>{query}"
    except (ValueError, UnicodeError):
        return "<invalid-url>"


def error_category(error):
    if isinstance(error, ProbeFailure):
        return str(error)
    if isinstance(error, (asyncio.TimeoutError, httpx.TimeoutException)):
        return "timeout"
    if isinstance(error, httpx.ProxyError):
        return "proxy_error"
    if isinstance(error, httpx.ConnectError):
        return "connection_error"
    if isinstance(error, socket.gaierror):
        return "dns_error"
    if isinstance(error, (json.JSONDecodeError, UnicodeError)):
        return "invalid_json"
    if isinstance(error, httpx.HTTPError):
        return "http_transport_error"
    if isinstance(error, (ValueError, TypeError, KeyError)):
        return "invalid_response"
    return "probe_error"


def failure(error):
    # Exception messages can contain proxy credentials or signed target URLs.
    return {"category": error_category(error), "exception_type": type(error).__name__}


class Budget:
    def __init__(self, args):
        self.args = args
        self.deadline = time.monotonic() + args.total_timeout
        self.requests = 0

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ProbeFailure("total_timeout")
        return remaining

    def request(self):
        self.remaining()
        if self.requests >= self.args.max_requests:
            raise ProbeFailure("request_budget_exhausted")
        self.requests += 1


async def check_target(url, budget):
    parts = urlsplit(str(url))
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ProbeFailure("unsupported_url")
    if parts.username or parts.password:
        raise ProbeFailure("target_credentials_not_allowed")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    addresses = await asyncio.wait_for(
        asyncio.get_running_loop().getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM),
        timeout=min(budget.args.request_timeout, budget.remaining()),
    )
    resolved = {ipaddress.ip_address(address[4][0]) for address in addresses}
    if not resolved or not all(ip.is_global or (
            budget.args.allow_loopback and ip.is_loopback) for ip in resolved):
        raise ProbeFailure("non_public_target")


async def fetch(client, url, limit, budget, headers=None):
    """Read at most limit body bytes, with explicit redirect and time budgets."""
    started = time.monotonic()
    redirects = []
    for _ in range(budget.args.max_redirects + 1):
        await check_target(url, budget)
        budget.request()
        timeout = min(budget.args.request_timeout, budget.remaining())
        async with client.stream("GET", url, headers=headers, timeout=timeout) as response:
            if response.is_redirect:
                redirects.append({"url": redacted_url(response.url),
                                  "status": response.status_code})
                location = response.headers.get("location")
                if not location:
                    raise ProbeFailure("missing_redirect_location")
                url = urljoin(str(response.url), location)
                continue
            body = bytearray()
            limited = False
            async for chunk in response.aiter_bytes():
                budget.remaining()
                body.extend(chunk[:limit - len(body)])
                if len(body) >= limit:
                    limited = True
                    break
            # Only copy a small, fixed header set; never cookies or credentials.
            result = {
                "status": response.status_code, "url": redacted_url(response.url),
                "redirects": redirects, "bytes_read": len(body), "read_limit_reached": limited,
                "content_type": response.headers.get("content-type", "")[:120],
                "content_range": response.headers.get("content-range", "")[:120],
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "ok": 200 <= response.status_code < 300,
            }
            # The actual URL is retained only in memory for relative resolution.
            return result, bytes(body), str(response.url)
    raise ProbeFailure("redirect_limit")


async def fetch_json(client, url, budget):
    result, body, _ = await fetch(client, url, budget.args.max_api_bytes, budget)
    if not result["ok"]:
        return result, None
    if result["read_limit_reached"]:
        raise ProbeFailure("api_body_limit")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ProbeFailure("invalid_json_shape")
    return result, value


async def probe_hls(client, url, budget):
    steps = []
    try:
        for depth in range(budget.args.max_manifest_depth):
            result, body, base = await fetch(client, url, budget.args.max_playlist_bytes, budget)
            steps.append({"type": "playlist", "depth": depth, **result})
            if not result["ok"]:
                return {"ok": False, "steps": steps}
            if result["read_limit_reached"]:
                raise ProbeFailure("playlist_body_limit")
            if not body.lstrip().startswith(b"#EXTM3U"):
                raise ProbeFailure("not_hls")
            lines = [line.strip() for line in body.decode("utf-8-sig").splitlines()]
            master = next((index for index, line in enumerate(lines)
                           if line.startswith("#EXT-X-STREAM-INF:")), None)
            if master is not None:
                child = next((line for line in lines[master + 1:]
                              if line and not line.startswith("#")), None)
                if not child:
                    raise ProbeFailure("empty_master")
                url = urljoin(base, child)
                continue
            methods = sorted(set(re.findall(r"(?:^|,)METHOD=([^,]+)",
                                            ",".join(line.split(":", 1)[1] for line in lines
                                                     if line.startswith("#EXT-X-KEY:")))))
            if any(method != "NONE" for method in methods):
                raise ProbeFailure("encrypted_hls_not_probed")
            segments = [urljoin(base, line) for line in lines if line and not line.startswith("#")]
            durations = [float(line.split(":", 1)[1].split(",", 1)[0]) for line in lines
                         if line.startswith("#EXTINF:")]
            duration = sum(durations)
            if not math.isfinite(duration):
                raise ProbeFailure("invalid_duration")
            steps.append({"type": "media_playlist", "segments": len(segments),
                          "has_endlist": "#EXT-X-ENDLIST" in lines,
                          "encryption_methods": methods, "duration_seconds": round(duration, 3)})
            if not segments:
                raise ProbeFailure("empty_media_playlist")
            successful = True
            for index in sorted({0, len(segments) // 2, len(segments) - 1}):
                try:
                    observed, payload, _ = await fetch(
                        client, segments[index], budget.args.max_media_bytes, budget,
                        {"Range": f"bytes=0-{budget.args.max_media_bytes - 1}"},
                    )
                    observed["ok"] = observed["ok"] and bool(payload)
                    steps.append({"type": "segment", "index": index,
                                  "mpeg_ts_sync": bool(payload and payload[0] == 0x47), **observed})
                    successful = successful and observed["ok"]
                except Exception as error:
                    successful = False
                    steps.append({"type": "segment", "index": index, "ok": False,
                                  "error": failure(error)})
            return {"ok": successful, "steps": steps, "decoded": False}
        raise ProbeFailure("manifest_depth_limit")
    except Exception as error:
        return {"ok": False, "steps": steps, "error": failure(error), "decoded": False}


def source_type(source):
    if source.get("type"):
        return source["type"]
    path = urlsplit(source["api"]).path
    if "/provide/vod" in path:
        return "maccms"
    if path.rstrip("/").endswith("/api/v1"):
        return "peertube"
    return "http"


async def probe_job(source, sample, budget, proxy):
    result = {"source": source["id"], "sample_id": str(sample.get("id", ""))[:200],
              "scope": "bounded_http_reads", "playback_verified": False, "checks": {}}
    checks = result["checks"]
    try:
        limits = httpx.Limits(max_connections=2, max_keepalive_connections=1)
        async with httpx.AsyncClient(trust_env=False, proxy=proxy, follow_redirects=False,
                                     limits=limits, headers={"User-Agent": "jzmedia-p0-probe/1"}) as client:
            kind = source_type(source)
            if kind == "maccms" and sample:
                url = httpx.URL(source["api"]).copy_merge_params({"ac": "list", "wd": sample["query"]})
                observed, data = await fetch_json(client, url, budget)
                if data is not None:
                    observed["sample_id_found"] = any(str(item.get("vod_id")) == str(sample["id"])
                                                       for item in data.get("list", []))
                    observed["ok"] = observed["ok"] and observed["sample_id_found"]
                checks["search"] = observed
                url = httpx.URL(source["api"]).copy_merge_params({"ac": "detail", "ids": sample["id"]})
                observed, data = await fetch_json(client, url, budget)
                item = next((item for item in (data or {}).get("list", [])
                             if str(item.get("vod_id")) == str(sample["id"])), {})
                observed["ok"] = observed["ok"] and bool(item)
                observed["episode_counts"] = [len(line.split("#")) for line in
                                               str(item.get("vod_play_url", "")).split("$$$") if line]
                links = {str(httpx.URL(entry.split("$", 1)[1]))
                         for line in str(item.get("vod_play_url", "")).split("$$$")
                         for entry in line.split("#") if "$" in entry}
                for field in ("playlist", "media"):
                    if sample.get(field):
                        matches = str(httpx.URL(sample[field])) in links
                        observed[f"{field}_listed_in_detail"] = matches
                        observed["ok"] = observed["ok"] and matches
                checks["detail"] = observed
                if item.get("vod_pic"):
                    try:
                        observed, _, _ = await fetch(client, item["vod_pic"], budget.args.max_media_bytes,
                                                     budget, {"Range": f"bytes=0-{budget.args.max_media_bytes - 1}"})
                        checks["poster"] = observed
                    except Exception as error:
                        checks["poster"] = {"ok": False, "error": failure(error)}
            elif kind == "peertube" and sample:
                url = source["api"].rstrip("/") + "/videos/" + str(sample["id"])
                observed, data = await fetch_json(client, url, budget)
                observed["licence_id"] = (data or {}).get("licence", {}).get("id")
                observed["download_enabled"] = bool((data or {}).get("downloadEnabled"))
                observed["duration"] = (data or {}).get("duration")
                if sample.get("media"):
                    files = list((data or {}).get("files", []))
                    for playlist in (data or {}).get("streamingPlaylists", []):
                        files.extend(playlist.get("files", []))
                    observed["media_listed_in_detail"] = any(
                        sample["media"] in (file.get("fileUrl"), file.get("fileDownloadUrl"))
                        for file in files)
                    observed["ok"] = observed["ok"] and observed["media_listed_in_detail"]
                checks["detail"] = observed
            elif kind in {"maccms", "peertube"}:
                observed, data = await fetch_json(client, source["api"], budget)
                if kind == "maccms":
                    observed["ok"] = observed["ok"] and isinstance((data or {}).get("list"), list)
                checks["api"] = observed
            else:
                observed, _, _ = await fetch(client, source["api"], budget.args.max_api_bytes, budget)
                checks["api"] = observed
            if sample.get("playlist"):
                checks["hls"] = await probe_hls(client, sample["playlist"], budget)
            if sample.get("media"):
                ranges = []
                for start in (0, 1000000):
                    try:
                        observed, payload, _ = await fetch(
                            client, sample["media"], budget.args.max_media_bytes, budget,
                            {"Range": f"bytes={start}-{start + budget.args.max_media_bytes - 1}"},
                        )
                        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+|\*)", observed["content_range"])
                        observed["range_honored"] = bool(observed["status"] == 206 and match
                                                          and int(match[1]) == start)
                        observed["ok"] = observed["ok"] and bool(payload) and observed["range_honored"]
                        ranges.append({"requested_start": start, **observed})
                    except Exception as error:
                        ranges.append({"requested_start": start, "ok": False, "error": failure(error)})
                checks["mp4_ranges"] = {"ok": all(item["ok"] for item in ranges), "ranges": ranges}
        result["ok"] = bool(checks) and all(check.get("ok", False) for check in checks.values())
    except Exception as error:
        result.update(ok=False, error=failure(error))
    return result


async def run_probes(args, candidates, proxy):
    budget = Budget(args)
    semaphore = asyncio.Semaphore(args.concurrency)

    async def run(source, sample):
        async def bounded():
            async with semaphore:
                return await probe_job(source, sample, budget, proxy)
        try:
            return await asyncio.wait_for(bounded(), timeout=budget.remaining())
        except (asyncio.TimeoutError, ProbeFailure):
            return {"source": source["id"], "sample_id": str(sample.get("id", ""))[:200],
                    "ok": False, "scope": "bounded_http_reads", "playback_verified": False,
                    "error": {"category": "total_timeout"}}

    jobs = [(source, sample) for source in candidates["sources"]
            for sample in (source.get("samples") or [{}])]
    results = await asyncio.gather(*(run(source, sample) for source, sample in jobs))
    return {
        "schema_version": 1, "label": args.label, "observed_at": datetime.now(timezone.utc).isoformat(),
        "network": {"mode": args.mode, "trust_env": False, "proxy_env": args.proxy_env,
                    "transparent_host_or_router_proxy": "not_inspected"},
        "scope": "bounded HTTP reads only; no decoding, playback, rights or uptime verification",
        "dns_scope": "public addresses checked before each request; transport DNS is not pinned",
        "limits": {key: getattr(args, key) for key in (
            "request_timeout", "total_timeout", "max_requests", "max_redirects",
            "max_manifest_depth", "max_api_bytes", "max_playlist_bytes", "max_media_bytes")},
        "requests_started": budget.requests, "results": results,
        "summary": {"passed": sum(bool(item["ok"]) for item in results),
                    "failed": sum(not item["ok"] for item in results)},
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--label", required=True, help="Environment label; do not put secrets here")
    parser.add_argument("--mode", choices=("direct", "proxy"), default="direct")
    parser.add_argument("--proxy-env", help="Read an explicit proxy URL from this environment variable")
    parser.add_argument("--output", required=True, help="JSON report path, or - for stdout")
    parser.add_argument("--request-timeout", type=float, default=15)
    parser.add_argument("--total-timeout", type=float, default=120)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--max-requests", type=int, default=100)
    parser.add_argument("--max-redirects", type=int, default=3)
    parser.add_argument("--max-manifest-depth", type=int, default=3)
    parser.add_argument("--max-api-bytes", type=int, default=2 * 1024 * 1024)
    parser.add_argument("--max-playlist-bytes", type=int, default=512 * 1024)
    parser.add_argument("--max-media-bytes", type=int, default=64 * 1024)
    parser.add_argument("--allow-loopback", action="store_true", help="Allow isolated loopback test fixtures only")
    args = parser.parse_args(argv)
    if args.mode == "proxy" and not args.proxy_env:
        parser.error("proxy mode requires --proxy-env")
    if args.mode == "direct" and args.proxy_env:
        parser.error("direct mode does not accept --proxy-env")
    if args.proxy_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.proxy_env):
        parser.error("--proxy-env must be an environment variable name")
    for name in ("request_timeout", "total_timeout", "concurrency", "max_requests", "max_manifest_depth",
                 "max_api_bytes", "max_playlist_bytes", "max_media_bytes"):
        if not math.isfinite(getattr(args, name)) or getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive and finite")
    if args.max_redirects < 0:
        parser.error("--max-redirects must not be negative")
    if args.max_api_bytes > 8 * 1024 * 1024 or args.max_playlist_bytes > 2 * 1024 * 1024:
        parser.error("API/playlist caps are 8 MiB/2 MiB")
    if args.max_media_bytes > 1024 * 1024:
        parser.error("media reads are capped at 1 MiB per request")
    if args.concurrency > 8 or args.max_requests > 500 or args.max_redirects > 10 or args.max_manifest_depth > 10:
        parser.error("concurrency/request/redirect/manifest-depth caps are 8/500/10/10")
    return args


def load_candidates(path):
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("candidate_file_too_large")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("expected_object")
    sources = value.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= 20:
        raise ValueError("expected_1_to_20_sources")
    samples = 0
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get("id"), str) or not isinstance(source.get("api"), str):
            raise ValueError("invalid_source")
        if source_type(source) not in {"maccms", "peertube", "http"}:
            raise ValueError("unsupported_source_type")
        entries = source.get("samples", [])
        if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
            raise ValueError("invalid_samples")
        if source_type(source) in {"maccms", "peertube"} and any("id" not in item for item in entries):
            raise ValueError("sample_id_required")
        if source_type(source) == "maccms" and any("query" not in item for item in entries):
            raise ValueError("maccms_query_required")
        if any(not isinstance(item.get(key, ""), str) for item in entries
               for key in ("media", "playlist", "query")):
            raise ValueError("invalid_sample_url_or_query")
        samples += max(1, len(entries))
    if samples > 40:
        raise ValueError("too_many_samples")
    return value


def main(argv=None):
    args = parse_args(argv)
    try:
        if httpx is None:
            raise RuntimeError("missing_httpx")
        proxy = os.environ.get(args.proxy_env, "") if args.proxy_env else None
        if args.mode == "proxy":
            parts = urlsplit(proxy)
            if not parts.hostname or parts.scheme not in {"http", "https"}:
                raise ValueError("proxy_must_be_http_or_https")
            if parts.port is not None and not 1 <= parts.port <= 65535:
                raise ValueError("invalid_proxy_port")
        candidates = load_candidates(args.candidates)
        report = asyncio.run(run_probes(args, candidates, proxy))
        rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output == "-":
            sys.stdout.write(rendered)
        else:
            destination = Path(args.output)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + f".{os.getpid()}.tmp")
            temporary.write_text(rendered, encoding="utf-8")
            temporary.replace(destination)
            print(json.dumps({"report_written": True, "summary": report["summary"]}))
        return 1 if report["summary"]["failed"] else 0
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        # Do not include error text: parser/transport messages can contain secrets.
        print(json.dumps({"tool_error": type(error).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
