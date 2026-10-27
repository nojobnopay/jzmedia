"""Online-source diagnostics use isolated HTTP fixtures, never real providers."""

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "probe_online_sources.py"


@pytest.fixture
def probe_network():
    stop = threading.Event()
    hits = {"origin": [], "proxy": []}
    servers = []
    threads = []

    class Server(ThreadingHTTPServer):
        # Join handler threads at teardown; stop releases any slow responses.
        daemon_threads = False

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            hits[self.server.role].append({
                "path": urlsplit(self.path).path,
                "proxy_auth": self.headers.get("Proxy-Authorization"),
            })
            path = urlsplit(self.path).path
            base = f"http://127.0.0.1:{servers[0].server_port}"
            status, content_type, headers = 200, "application/json", {}
            if path == "/api.php/provide/vod/":
                body = json.dumps({"list": [{
                    "vod_id": 42,
                    "vod_play_url": f"first${base}/master.m3u8#mp4${base}/PATH_SECRET.mp4?sig=QUERY_SECRET",
                    "vod_pic": f"{base}/cover?token=QUERY_SECRET",
                }]}).encode()
                # Some real MacCMS APIs mislabel their valid JSON this way.
                content_type = "text/html;charset=utf-8"
            elif path in {"/master.m3u8", "/unlisted.m3u8"}:
                body = (b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=500000\n"
                        b"segments/index.m3u8?sig=QUERY_SECRET\n")
                content_type = "application/vnd.apple.mpegurl"
            elif path == "/segments/index.m3u8":
                body = (b"#EXTM3U\n#EXTINF:3,\n../media.ts?token=QUERY_SECRET\n"
                        b"#EXT-X-ENDLIST\n")
                content_type = "application/vnd.apple.mpegurl"
            elif path.endswith(".mp4") or path == "/media.ts":
                start = int(self.headers.get("Range", "bytes=0-65535").split("=")[1].split("-")[0])
                body = (b"\x47" + b"a" * 65535) if path.endswith(".ts") else (
                    b"\x00\x00\x00\x20ftyp" + b"b" * (65536 - 8))
                status = 206
                headers["Content-Range"] = f"bytes {start}-{start + len(body) - 1}/2000000"
                content_type = "video/mp2t" if path.endswith(".ts") else "video/mp4"
            elif path == "/cover":
                body, content_type = b"jpeg", "image/jpeg"
            elif path == "/fail":
                body, status = b"unavailable", 503
            elif path == "/slow":
                stop.wait(5)
                body = b"late"
            else:
                body, content_type = b"<html>not an API</html>", "text/html"
            try:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    try:
        for role in ("origin", "proxy"):
            server = Server(("127.0.0.1", 0), Handler)
            server.role = role
            servers.append(server)
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
            thread.start()
            threads.append(thread)
        yield {
            "base": f"http://127.0.0.1:{servers[0].server_port}",
            "proxy": f"http://127.0.0.1:{servers[1].server_port}",
            "hits": hits,
        }
    finally:
        stop.set()
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)
            assert not thread.is_alive()


@pytest.fixture
def run_probe(tmp_path, probe_network):
    def run(candidates, *extra):
        candidate_file = tmp_path / "candidates.json"
        report_file = tmp_path / "report.json"
        candidate_file.write_text(json.dumps(candidates), encoding="utf-8")
        report_file.unlink(missing_ok=True)
        environment = dict(os.environ)
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            environment[name] = probe_network["proxy"]
        environment.update(NO_PROXY="", no_proxy="")
        environment["P0_TEST_PROXY"] = probe_network["proxy"].replace(
            "http://", "http://user:PROXY_SECRET@")
        environment.pop("P0_TEST_MISSING_PROXY", None)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--candidates", str(candidate_file),
             "--label", "isolated-fixture", "--output", str(report_file),
             "--allow-loopback", *extra],
            env=environment, text=True, capture_output=True, timeout=8, check=False,
        )
        rendered = report_file.read_text(encoding="utf-8") if report_file.exists() else ""
        public_output = result.stdout + result.stderr + rendered
        for secret in ("PROXY_SECRET", "QUERY_SECRET", "PATH_SECRET"):
            assert secret not in public_output
        return result, json.loads(rendered) if rendered else None
    return run


@pytest.fixture
def full_candidate(probe_network):
    base = probe_network["base"]
    return {"sources": [{
        "id": "mock", "type": "maccms", "api": f"{base}/api.php/provide/vod/",
        "samples": [{"id": "42", "query": "test", "playlist": f"{base}/master.m3u8",
                     "media": f"{base}/PATH_SECRET.mp4?sig=QUERY_SECRET"}],
    }]}


def test_direct_ignores_environment_proxy_and_reads_bounded_media(run_probe, full_candidate, probe_network):
    result, report = run_probe(full_candidate)
    assert result.returncode == 0, (result.stderr, report)
    assert probe_network["hits"]["origin"]
    assert not probe_network["hits"]["proxy"]
    assert report["summary"] == {"passed": 1, "failed": 0}
    assert report["network"]["trust_env"] is False
    checks = report["results"][0]["checks"]
    assert checks["search"]["sample_id_found"] is True
    assert checks["detail"]["playlist_listed_in_detail"] is True
    assert checks["hls"]["decoded"] is False
    assert all(item["range_honored"] and item["bytes_read"] == 65536
               for item in checks["mp4_ranges"]["ranges"])
    assert report["results"][0]["playback_verified"] is False


def test_explicit_proxy_is_used_and_credentials_are_redacted(run_probe, full_candidate, probe_network):
    result, report = run_probe(full_candidate, "--mode", "proxy", "--proxy-env", "P0_TEST_PROXY")
    assert result.returncode == 0, (result.stderr, report)
    assert not probe_network["hits"]["origin"]
    assert probe_network["hits"]["proxy"]
    expected_auth = "Basic " + base64.b64encode(b"user:PROXY_SECRET").decode()
    assert all(hit["proxy_auth"] == expected_auth for hit in probe_network["hits"]["proxy"])
    assert expected_auth not in json.dumps(report)
    assert report["network"]["mode"] == "proxy"


@pytest.mark.parametrize("kind,samples", [("maccms", []), ("peertube", [{"id": "x"}])])
def test_api_html_does_not_count_as_success(run_probe, probe_network, kind, samples):
    result, report = run_probe({"sources": [{
        "id": "html", "type": kind, "api": f"{probe_network['base']}/not-json", "samples": samples,
    }]})
    assert result.returncode == 1
    assert report["results"][0]["error"]["category"] == "invalid_json"


def test_media_must_be_listed_by_detail_even_when_both_urls_respond(run_probe, full_candidate, probe_network):
    full_candidate["sources"][0]["samples"][0]["playlist"] = f"{probe_network['base']}/unlisted.m3u8"
    result, report = run_probe(full_candidate)
    assert result.returncode == 1
    checks = report["results"][0]["checks"]
    assert checks["detail"]["playlist_listed_in_detail"] is False
    assert checks["hls"]["ok"] is True


def test_one_source_failure_preserves_other_source_results(run_probe, probe_network):
    base = probe_network["base"]
    result, report = run_probe({"sources": [
        {"id": "bad", "type": "http", "api": f"{base}/fail"},
        {"id": "good", "type": "http", "api": f"{base}/cover"},
    ]})
    assert result.returncode == 1
    assert report["summary"] == {"passed": 1, "failed": 1}
    assert report["results"][0]["checks"]["api"]["status"] == 503
    assert report["results"][1]["ok"] is True


def test_total_timeout_leaves_a_report(run_probe, probe_network):
    started = time.monotonic()
    result, report = run_probe({"sources": [
        {"id": "slow", "type": "http", "api": f"{probe_network['base']}/slow"},
    ]}, "--total-timeout", "0.25", "--request-timeout", "1")
    assert time.monotonic() - started < 3  # The fixture would otherwise wait five seconds.
    assert result.returncode == 1
    assert report["results"][0]["error"]["category"] in {"total_timeout", "timeout"}


def test_manifest_depth_is_bounded(run_probe, full_candidate):
    result, report = run_probe(full_candidate, "--max-manifest-depth", "1")
    assert result.returncode == 1
    assert report["results"][0]["checks"]["hls"]["error"]["category"] == "manifest_depth_limit"


def test_invalid_configuration_has_tool_error_exit_code(run_probe):
    result, report = run_probe({"sources": []})
    assert result.returncode == 2
    assert report is None
    assert json.loads(result.stderr)["tool_error"] == "ValueError"


def test_missing_explicit_proxy_has_tool_error_exit_code(run_probe, full_candidate):
    result, report = run_probe(full_candidate, "--mode", "proxy", "--proxy-env", "P0_TEST_MISSING_PROXY")
    assert result.returncode == 2
    assert report is None
