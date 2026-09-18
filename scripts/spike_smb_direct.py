#!/usr/bin/env python3
"""SMB 直读 spike：不挂载，用进程内 SMB2/3 客户端（smbprotocol）直接读远程媒体。

判定标准（同一共享）：
- 顺序读 ≥ 30 MB/s（单流）；双流合计 ≥ 40 MB/s
- 随机 seek（64 次 1MB 读）P95 < 500 ms
- 目录列举 2k 文件 < 30 s
- HTTP Range 代理下 ffprobe 正常、ffmpeg -ss 出片

用法：
  # 本地自检（验证测量/代理/ffmpeg 链路，不需要 NAS）
  .venv/bin/python scripts/spike_smb_direct.py --selftest

  # 真实 SMB（凭据只读 env / 参数，不落盘不打印）
  SPIKE_SMB_USER=xxx SPIKE_SMB_PASS=xxx \
  .venv/bin/python scripts/spike_smb_direct.py \
      --url '//NAS/video' --subpath Movies --rel '某片/某片.mkv'

默认只读；写测试需显式 --write-test <scratch 子目录>（不会碰媒体目录）。
"""
import argparse
import functools
import hashlib
import json
import os
import random
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.media import ffmpeg_bin, ffprobe_bin  # noqa: E402
from app.smburl import parse_smb_url  # noqa: E402

CHUNK = 1 << 20
DEFAULTS = {
    "seq_min_mbps": 30.0,
    "dual_min_mbps": 40.0,
    "seek_p95_ms": 500.0,
    "list_2k_seconds": 30.0,
}


def _mb(nbytes):
    return nbytes / (1024 * 1024)


class LocalBackend:
    label = "local"

    def __init__(self, root):
        self.root = Path(root).resolve()

    def _p(self, rel):
        p = (self.root / (rel or "")).resolve()
        if p != self.root and self.root not in p.parents:
            raise ValueError("path escapes root: " + rel)
        return p

    def listdir(self, rel):
        out = []
        with os.scandir(self._p(rel)) as it:
            for e in it:
                try:
                    st = e.stat()
                    out.append({"name": e.name, "dir": e.is_dir(), "size": st.st_size})
                except OSError:
                    out.append({"name": e.name, "dir": e.is_dir(), "size": 0})
        return out

    def is_dir(self, rel):
        return self._p(rel).is_dir()

    def size(self, rel):
        return self._p(rel).stat().st_size

    def read(self, rel, offset, length):
        with open(self._p(rel), "rb") as f:
            f.seek(offset)
            return f.read(length)

    def write(self, rel, data):
        p = self._p(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)

    def rename(self, src, dst):
        os.replace(self._p(src), self._p(dst))

    def remove(self, rel):
        os.remove(self._p(rel))


class SmbBackend:
    label = "smb"

    def __init__(self, url, subpath="", username="", password="", domain=""):
        import smbclient
        parsed = parse_smb_url(url)
        if parsed["error"]:
            raise SystemExit("URL 解析失败：" + parsed["error"])
        self.smbclient = smbclient
        self.host = parsed["host"]
        self.user = (username or parsed["username"] or "").strip()
        self.base = "/".join(p for p in (parsed["host"], parsed["share"],
                                         parsed["subpath"], subpath.strip("/")) if p)
        kwargs = {"username": self.user or None, "password": password or None}
        if domain:
            kwargs["domain"] = domain
        smbclient.register_session(self.host, **kwargs)

    def _p(self, rel):
        return "/" + "/".join(p for p in (self.base, (rel or "").strip("/")) if p)

    def listdir(self, rel):
        out = []
        for name in self.smbclient.listdir(self._p(rel)):
            if name in (".", ".."):
                continue
            p = self._p(rel).rstrip("/") + "/" + name
            try:
                st = self.smbclient.stat(p)
                out.append({"name": name, "dir": self.smbclient.path.isdir(p),
                            "size": int(getattr(st, "st_size", 0) or 0)})
            except OSError:
                out.append({"name": name, "dir": False, "size": 0})
        return out

    def is_dir(self, rel):
        return self.smbclient.path.isdir(self._p(rel))

    def size(self, rel):
        return int(self.smbclient.stat(self._p(rel)).st_size)

    def read(self, rel, offset, length):
        with self.smbclient.open_file(self._p(rel), mode="rb") as f:
            f.seek(offset)
            return f.read(length)

    def write(self, rel, data):
        p = self._p(rel)
        parent = p.rsplit("/", 1)[0]
        try:
            self.smbclient.makedirs(parent, exist_ok=True)
        except OSError:
            pass
        with self.smbclient.open_file(p, mode="wb") as f:
            f.write(data)

    def rename(self, src, dst):
        self.smbclient.rename(self._p(src), self._p(dst))

    def remove(self, rel):
        self.smbclient.remove(self._p(rel))


def walk(backend, rel, max_entries, max_seconds):
    t0 = time.perf_counter()
    queue = [rel]
    files = dirs = 0
    total = 0
    largest = None
    deadline = t0 + max_seconds
    while queue and files + dirs < max_entries and time.perf_counter() < deadline:
        cur = queue.pop(0)
        try:
            entries = backend.listdir(cur)
        except OSError:
            continue
        for e in entries:
            child = (cur.rstrip("/") + "/" + e["name"]).strip("/")
            if e["dir"]:
                dirs += 1
                queue.append(child)
            else:
                files += 1
                total += e["size"]
                if largest is None or e["size"] > largest["size"]:
                    largest = {"rel": child, "size": e["size"]}
    elapsed = time.perf_counter() - t0
    return {"dirs": dirs, "files": files, "bytes": total, "seconds": elapsed,
            "entries_per_s": (files + dirs) / elapsed if elapsed else 0.0,
            "largest": largest, "truncated": bool(queue)}


def seq_read(backend, rel, size, limit_bytes, chunk=CHUNK):
    limit = min(size, limit_bytes)
    digest = hashlib.sha256()
    t0 = time.perf_counter()
    pos = 0
    while pos < limit:
        data = backend.read(rel, pos, min(chunk, limit - pos))
        if not data:
            break
        if pos == 0:
            digest.update(data[:4096])
        pos += len(data)
    elapsed = time.perf_counter() - t0
    return {"bytes": pos, "seconds": elapsed,
            "mbps": _mb(pos) / elapsed if elapsed else 0.0,
            "head_sha256_4k": digest.hexdigest()[:16]}


def random_seeks(backend, rel, size, count, chunk=CHUNK):
    lat = []
    rnd = random.Random(42)
    for _ in range(count):
        offset = rnd.randrange(0, max(1, size - chunk))
        t0 = time.perf_counter()
        backend.read(rel, offset, chunk)
        lat.append((time.perf_counter() - t0) * 1000)
    lat_sorted = sorted(lat)
    return {"n": count, "mean_ms": statistics.mean(lat), "p50_ms": lat_sorted[len(lat) // 2],
            "p95_ms": lat_sorted[min(len(lat) - 1, int(len(lat) * 0.95))],
            "max_ms": max(lat)}


def concurrent_read(backend, rels, seconds, threads):
    stop = threading.Event()
    counters = [0] * threads
    errors = []

    def worker(idx):
        rel = rels[idx % len(rels)]
        size = backend.size(rel)
        pos = 0
        try:
            while not stop.is_set():
                n = min(CHUNK, max(1, size - pos))
                data = backend.read(rel, pos, n)
                if not data:
                    pos = 0
                    continue
                counters[idx] += len(data)
                pos += len(data)
                if pos >= size:
                    pos = 0
        except Exception as e:
            errors.append(f"{type(e).__name__}: {e}")

    workers = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(threads)]
    t0 = time.perf_counter()
    for w in workers:
        w.start()
    time.sleep(seconds)
    stop.set()
    for w in workers:
        w.join(timeout=10)
    elapsed = time.perf_counter() - t0
    total = sum(counters)
    return {"threads": threads, "seconds": elapsed, "bytes": total,
            "mbps_total": _mb(total) / elapsed if elapsed else 0.0,
            "mbps_each": [_mb(c) / elapsed if elapsed else 0.0 for c in counters],
            "errors": errors}


class RangeProxy:
    def __init__(self, backend, rel):
        self.backend = backend
        self.rel = rel
        self.size = backend.size(rel)
        handler = functools.partial(_RangeHandler, backend=backend, rel=rel, size=self.size)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/{os.path.basename(self.rel)}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class _RangeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, backend, rel, size, **kwargs):
        self._backend = backend
        self._rel = rel
        self._size = size
        super().__init__(*args, **kwargs)

    def log_message(self, fmt, *args):
        pass

    def do_HEAD(self):
        self._serve(head=True)

    def do_GET(self):
        self._serve(head=False)

    def _serve(self, head):
        start, end = 0, self._size - 1
        status = 200
        rng = (self.headers.get("Range") or "").strip()
        if rng.startswith("bytes="):
            spec = rng[6:].split(",")[0].strip()
            first, _, last = spec.partition("-")
            if first:
                start = int(first)
                end = int(last) if last else self._size - 1
            elif last:
                start = max(0, self._size - int(last))
                end = self._size - 1
            status = 206
        end = min(end, self._size - 1)
        length = max(0, end - start + 1)
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{self._size}")
        self.end_headers()
        if head:
            return
        pos = start
        left = length
        try:
            while left > 0:
                data = self._backend.read(self._rel, pos, min(left, CHUNK))
                if not data:
                    break
                self.wfile.write(data)
                pos += len(data)
                left -= len(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


def ffprobe_url(url, timeout=60):
    cmd = [ffprobe_bin(), "-v", "error", "-show_entries", "format=duration,size",
           "-of", "json", url]
    t0 = time.perf_counter()
    r = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    elapsed = time.perf_counter() - t0
    info = {}
    try:
        info = json.loads((r.stdout or b"{}").decode("utf-8", "replace")).get("format", {})
    except ValueError:
        pass
    return {"ok": r.returncode == 0, "seconds": elapsed, "info": info,
            "stderr": (r.stderr or b"").decode("utf-8", "replace").strip()[-300:]}


def ffmpeg_seek(url, seek_s, duration=10, timeout=120):
    cmd = [ffmpeg_bin(), "-v", "error", "-ss", str(seek_s), "-i", url,
           "-t", str(duration), "-c", "copy", "-f", "null", "-"]
    t0 = time.perf_counter()
    r = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    elapsed = time.perf_counter() - t0
    return {"ok": r.returncode == 0, "seconds": elapsed,
            "stderr": (r.stderr or b"").decode("utf-8", "replace").strip()[-300:]}


def write_test(backend, scratch_rel, mb=4):
    rel = scratch_rel.strip("/") + "/spike-write.bin"
    renamed = scratch_rel.strip("/") + "/spike-write-renamed.bin"
    payload = os.urandom(CHUNK * max(1, mb))
    t0 = time.perf_counter()
    backend.write(rel, payload)
    write_seconds = time.perf_counter() - t0
    t1 = time.perf_counter()
    backend.rename(rel, renamed)
    rename_seconds = time.perf_counter() - t1
    size = backend.size(renamed)
    backend.remove(renamed)
    return {"bytes": size, "write_mbps": _mb(size) / write_seconds if write_seconds else 0.0,
            "write_seconds": write_seconds, "rename_seconds": rename_seconds}


def ttfb(url, read_bytes=CHUNK, timeout=30):
    import http.client
    from urllib.parse import urlparse
    u = urlparse(url)
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=timeout)
    t0 = time.perf_counter()
    conn.request("GET", u.path, headers={"Range": f"bytes=0-{read_bytes - 1}"})
    resp = conn.getresponse()
    first = resp.read(1024)
    ttfb_ms = (time.perf_counter() - t0) * 1000
    resp.read()
    conn.close()
    return {"status": resp.status, "ttfb_ms": ttfb_ms, "first_bytes": len(first)}


def make_selftest_data(tmp, mb=64, duration=120):
    rnd = os.urandom(CHUNK)
    big = Path(tmp) / "sample.bin"
    with open(big, "wb") as f:
        for _ in range(mb):
            f.write(rnd)
    video = Path(tmp) / "sample.mp4"
    cmd = [ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi",
           "-i", "testsrc2=size=1280x720:rate=25", "-t", str(duration),
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-shortest", str(video)]
    r = subprocess.run(cmd, capture_output=True, timeout=300, check=False)
    if r.returncode != 0:
        raise SystemExit("生成自检视频失败：" + (r.stderr or b"").decode()[-300:])
    return {"big": "sample.bin", "video": "sample.mp4"}


def report(args, backend, result):
    print(f"\n=== SMB 直读 spike（{backend.label}）===")
    lst = result.get("listing", {})
    print(f"列举：{lst.get('files', 0)} 文件 / {lst.get('dirs', 0)} 目录，"
          f"{lst.get('seconds', 0):.2f}s，{lst.get('entries_per_s', 0):.1f} 项/s"
          f"{'（截断）' if lst.get('truncated') else ''}")
    seq = result.get("seq_read", {})
    print(f"顺序读：{_mb(seq.get('bytes', 0)):.1f} MB，{seq.get('seconds', 0):.2f}s，"
          f"{seq.get('mbps', 0):.1f} MB/s")
    con = result.get("concurrency", {})
    if con:
        print(f"并发读：{con.get('threads', 0)} 流合计 {con.get('mbps_total', 0):.1f} MB/s，"
              f"各 {['%.1f' % x for x in con.get('mbps_each', [])]}")
    sk = result.get("seeks", {})
    print(f"随机 seek：P50 {sk.get('p50_ms', 0):.0f} ms / P95 {sk.get('p95_ms', 0):.0f} ms "
          f"（{sk.get('n', 0)} 次）")
    probe = result.get("ffprobe")
    if probe:
        print(f"ffprobe：{'OK' if probe['ok'] else 'FAIL'}，{probe['seconds']:.2f}s，"
              f"duration={probe['info'].get('duration', '?')}")
    seek = result.get("ffmpeg_seek")
    if seek:
        print(f"ffmpeg seek：{'OK' if seek['ok'] else 'FAIL'}，{seek['seconds']:.2f}s")
    if result.get("ttfb"):
        print(f"代理 TTFB：{result['ttfb']['ttfb_ms']:.0f} ms")
    if result.get("write_test"):
        w = result["write_test"]
        print(f"写测试：{w['write_mbps']:.1f} MB/s，rename {w['rename_seconds'] * 1000:.0f} ms")
    verdict = result.get("verdict")
    if verdict:
        print("\n判定：" + ("GO" if verdict["go"] else "NO-GO"))
        for line in verdict["reasons"]:
            print("  - " + line)
    print()
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print("JSON 已写入 " + args.json)


def verdict_of(result):
    reasons = []
    go = True
    seq = result.get("seq_read", {})
    if seq.get("mbps", 0) >= DEFAULTS["seq_min_mbps"]:
        reasons.append(f"顺序读 {seq.get('mbps', 0):.1f} ≥ {DEFAULTS['seq_min_mbps']} MB/s")
    else:
        go = False
        reasons.append(f"顺序读 {seq.get('mbps', 0):.1f} < {DEFAULTS['seq_min_mbps']} MB/s")
    con = result.get("concurrency") or {}
    if con:
        if con.get("mbps_total", 0) >= DEFAULTS["dual_min_mbps"]:
            reasons.append(f"双流合计 {con.get('mbps_total', 0):.1f} ≥ "
                           f"{DEFAULTS['dual_min_mbps']} MB/s")
        else:
            go = False
            reasons.append(f"双流合计 {con.get('mbps_total', 0):.1f} < "
                           f"{DEFAULTS['dual_min_mbps']} MB/s")
    sk = result.get("seeks", {})
    if sk.get("p95_ms", 0) < DEFAULTS["seek_p95_ms"]:
        reasons.append(f"seek P95 {sk.get('p95_ms', 0):.0f} < {DEFAULTS['seek_p95_ms']:.0f} ms")
    else:
        go = False
        reasons.append(f"seek P95 {sk.get('p95_ms', 0):.0f} ≥ {DEFAULTS['seek_p95_ms']:.0f} ms")
    lst = result.get("listing", {})
    if lst.get("files", 0) >= 2000:
        if lst.get("seconds", 0) < DEFAULTS["list_2k_seconds"]:
            reasons.append(f"列举 2k 文件 {lst.get('seconds', 0):.1f}s "
                           f"< {DEFAULTS['list_2k_seconds']:.0f}s")
        else:
            go = False
            reasons.append(f"列举 2k 文件 {lst.get('seconds', 0):.1f}s ≥ "
                           f"{DEFAULTS['list_2k_seconds']:.0f}s")
    else:
        reasons.append(f"样本目录仅 {lst.get('files', 0)} 文件（2k 门槛未实测）")
    for key, label in (("ffprobe", "ffprobe"), ("ffmpeg_seek", "ffmpeg seek")):
        block = result.get(key)
        if block is not None and not block.get("ok"):
            go = False
            reasons.append(f"{label} 失败：" + (block.get("stderr") or "")[:120])
    return {"go": go, "reasons": reasons}


def main():
    ap = argparse.ArgumentParser(description="SMB 直读 spike（默认只读）")
    ap.add_argument("--selftest", action="store_true", help="本地文件自检（不需要 NAS）")
    ap.add_argument("--url", default=os.getenv("SPIKE_SMB_URL", ""),
                    help="//主机/共享[/目录] 或 smb://用户@主机/共享/目录")
    ap.add_argument("--subpath", default="", help="共享内再下一层目录")
    ap.add_argument("--user", default=os.getenv("SPIKE_SMB_USER", ""))
    ap.add_argument("--password", default=os.getenv("SPIKE_SMB_PASS", ""))
    ap.add_argument("--domain", default=os.getenv("SPIKE_SMB_DOMAIN", ""))
    ap.add_argument("--list-rel", default=".", help="列举根（相对共享/子目录）")
    ap.add_argument("--rel", default="", help="大文件相对路径；缺省自动选最大")
    ap.add_argument("--read-mb", type=int, default=2048)
    ap.add_argument("--seek-n", type=int, default=64)
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--concurrency-seconds", type=float, default=15)
    ap.add_argument("--max-entries", type=int, default=8000)
    ap.add_argument("--list-seconds", type=float, default=90)
    ap.add_argument("--skip-ffmpeg", action="store_true")
    ap.add_argument("--write-test", default="", help="写测试 scratch 子目录（默认不做写测试）")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    result = {"backend": "selftest" if args.selftest else "smb", "started_at": time.time()}
    if args.selftest:
        tmp = tempfile.mkdtemp(prefix="spike-smb-")
        data = make_selftest_data(tmp)
        backend = LocalBackend(tmp)
        result["sample"] = data
        rel_big, rel_video = data["big"], data["video"]
    else:
        if not args.url:
            raise SystemExit("缺少 --url 或 SPIKE_SMB_URL（如 //NAS/video）")
        if not args.password:
            print("提示：未提供 SPIKE_SMB_PASS/--password，将尝试匿名/当前用户登录", file=sys.stderr)
        t0 = time.perf_counter()
        try:
            backend = SmbBackend(args.url, args.subpath, args.user, args.password, args.domain)
        except Exception as e:
            raise SystemExit(
                f"连接/认证失败：{type(e).__name__}: {e}\n"
                "请检查 SPIKE_SMB_URL / SPIKE_SMB_USER / SPIKE_SMB_PASS（只读账号即可），"
                "或先确认 NAS 上该共享已开启 SMB 且允许该账号访问")
        result["connect_seconds"] = time.perf_counter() - t0
        print(f"连接 {backend.host} 用时 {result['connect_seconds'] * 1000:.0f} ms")
        rel_big, rel_video = args.rel, args.rel

    result["listing"] = walk(backend, args.list_rel, args.max_entries, args.list_seconds)
    if not rel_big:
        largest = (result["listing"].get("largest") or {})
        rel_big = largest.get("rel", "")
        if not rel_big:
            raise SystemExit("目录里没找到可读文件，请用 --rel 指定样本文件")
        print(f"自动选择样本：{rel_big}（{_mb(largest['size']):.0f} MB）")
    if not rel_video:
        rel_video = rel_big
    size = backend.size(rel_big)
    result["sample_file"] = {"rel": rel_big, "size": size}
    result["seq_read"] = seq_read(backend, rel_big, size, args.read_mb * (1 << 20))
    result["seeks"] = random_seeks(backend, rel_big, size, args.seek_n)
    result["concurrency"] = concurrent_read(backend, [rel_big], args.concurrency_seconds,
                                            max(1, args.threads))
    if not args.skip_ffmpeg:
        proxy = RangeProxy(backend, rel_video)
        try:
            result["ttfb"] = ttfb(proxy.url)
            result["ffprobe"] = ffprobe_url(proxy.url)
            dur = float(result["ffprobe"].get("info", {}).get("duration") or 0)
            seek_at = max(1, int(dur * 0.5)) if dur > 4 else 1
            result["ffmpeg_seek"] = ffmpeg_seek(proxy.url, seek_at, min(10, max(2, dur - 1)))
        finally:
            proxy.close()
    if args.write_test:
        result["write_test"] = write_test(backend, args.write_test)
    result["verdict"] = verdict_of(result)
    report(args, backend, result)


if __name__ == "__main__":
    main()
