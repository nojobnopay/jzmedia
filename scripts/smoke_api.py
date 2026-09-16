#!/usr/bin/env python3
"""L2 场景 smoke：隔离临时目录起后端（离线确定性），跑关键 API 场景并断言。

用法：.venv/bin/python scripts/smoke_api.py
覆盖：health / scan（含 B1 四条规则回归）/ movies / search / facets / missing /
organize / restore / clean-sidecars / clean-episodes / collections / backends /
probe-missing / stats / settings。播放转码链路不在此层（宿主无 ffmpeg，需 docker 人工验）。
"""
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]

FIXTURES = [
    ("电影/The Sample Movie (2024).mkv", b"x"),               # 正片：不得被当样片
    ("电影/Inception.2010.1080p.BluRay.sample.mkv", b"x"),    # 样片：不入库
    ("电影/Some.Show.S01E01.1080p.mkv", b"x"),                # 剧集：不入库
    ("#recycle/old.mkv", b"x"),                               # 回收站：不扫描
    ("@eaDir/thumb.mkv", b"x"),                               # NAS 缩略图目录：不扫描
    (".hidden/x.mkv", b"x"),                                  # 隐藏目录：不扫描
    ("电影/Inception.2010.1080p.srt", b"1\n"),                # 外挂字幕：不参与扫描
]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _req(base: str, path: str, method: str = "GET", body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=240) as r:
        raw = r.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def _wait_health(base: str, proc, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            raise SystemExit(f"smoke server exited early:\n{out}")
        try:
            if _req(base, "/api/health").get("status") == "ok":
                return
        except (urllib.error.URLError, ConnectionError, json.JSONDecodeError):
            time.sleep(0.4)
    raise SystemExit("smoke server health timeout")


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="jzmedia-smoke-"))
    media = tmp / "media"
    for rel, content in FIXTURES:
        p = media / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    port = _free_port()
    env = {**os.environ, "DATA_DIR": str(tmp / "data"), "MEDIA_ROOT": str(media),
           "SMOKE_PORT": str(port), "TMDB_READ_TOKEN": "", "TMDB_API_KEY": ""}
    proc = subprocess.Popen([sys.executable, str(ROOT / "scripts/_smoke_app.py")],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    base = f"http://127.0.0.1:{port}"
    failed = []
    try:
        _wait_health(base, proc)

        def check(name, cond, detail=""):
            print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
            if not cond:
                failed.append(name)

        h = _req(base, "/api/health")
        check("health ok", h.get("status") == "ok", str(h))

        s = _req(base, "/api/scan", method="POST")
        check("scan returns results", isinstance(s.get("results"), list) and len(s["results"]) >= 1,
              str(s)[:200])

        items = _req(base, "/api/movies")["items"]
        paths = {m["file_path"] for m in items}
        check("sample not in library", not any("sample" in p for p in paths), str(paths))
        check("episode not in library", not any(".S01E01." in p for p in paths), str(paths))
        check("recycle pruned", not any(p.startswith("#recycle/") for p in paths), str(paths))
        check("eaDir pruned", not any(p.startswith("@eaDir/") for p in paths), str(paths))
        check("hidden pruned", not any(p.startswith(".hidden/") for p in paths), str(paths))
        check("sample-titled movie scanned", any("The Sample Movie" in p for p in paths),
              str(paths))

        sr = _req(base, "/api/search?q=Sample")
        check("search works", isinstance(sr.get("items"), list))

        facets = _req(base, "/api/facets")
        check("facets keys", {"genres", "regions", "years", "tags"} <= set(facets))

        miss = _req(base, "/api/files/missing")
        check("no missing files", miss.get("total") == 0, str(miss))

        org = _req(base, "/api/files/organize",
                   method="POST", body={"mode": "inplace", "dry_run": True})
        check("organize dry-run", isinstance(org.get("plans"), list), str(org)[:200])

        res = _req(base, "/api/files/restore-original", method="POST", body={"dry_run": True})
        check("restore dry-run", res.get("dry_run") is True, str(res)[:200])

        cs = _req(base, "/api/files/clean-sidecars", method="POST", body={"dry_run": True})
        check("clean-sidecars dry-run", isinstance(cs.get("plans"), list), str(cs)[:200])

        ce = _req(base, "/api/files/clean-episodes", method="POST", body={"dry_run": True})
        check("clean-episodes dry-run", isinstance(ce.get("plans"), list) and ce.get("total") == 0,
              str(ce)[:200])

        col = _req(base, "/api/collections", method="POST", body={"name": "smoke-合集"})
        check("collection create", bool(col.get("id")), str(col)[:200])
        cols = _req(base, "/api/collections")
        check("collection listed", any(c["id"] == col.get("id") for c in cols.get("items", [])))
        _req(base, f"/api/collections/{col['id']}", method="DELETE")
        check("collection deleted",
              not any(c["id"] == col.get("id") for c in _req(base, "/api/collections").get("items", [])))

        be = _req(base, "/api/stream/backends")
        check("backends reported", bool(be.get("name")), str(be))

        pm = _req(base, "/api/stream/probe-missing", method="POST", body={"limit": 5})
        check("probe-missing runs", isinstance(pm.get("total"), int), str(pm)[:200])

        st = _req(base, "/api/jobs/stats")
        check("stats missing_files 0", st.get("missing_files") == 0, str(st))

        cfg = _req(base, "/api/settings")
        check("settings readable", cfg.get("tmdb_configured") is False, str(cfg)[:200])
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 60)
    if failed:
        print(f"SMOKE FAILED: {len(failed)} -> {failed}")
        return 1
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
