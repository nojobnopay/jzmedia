"""Isolated, opt-in LAN casting fixture; never mount production data or media.

Example (inside the current jzmedia image)::

    python /fixture/casting_lan_server.py --root /tmp/jzmedia-cast-lan-example \
        --port 58081 --base-url http://192.168.1.10:58081

The root must be new or empty. Real discovery and user-triggered DLNA commands
are enabled. Shutdown does not stop a TV; POST /fixture/stop first, which only
stops receivers whose reported TrackURI still belongs to this fixture.
"""

import argparse
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit


def binary(name):
    """Use installed binaries only; no downloads or startup network requests."""
    found = shutil.which(name)
    if found:
        return Path(found)
    import importlib.util
    spec = importlib.util.find_spec("static_ffmpeg")
    if spec and spec.origin:
        for candidate in (Path(spec.origin).parent / "bin").glob(f"*/{name}"):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
    raise SystemExit(f"An installed {name} is required")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--media-ttl", type=int, default=7200)
    args = parser.parse_args()
    original = args.root.absolute()
    root = original.resolve()
    if (original.is_symlink() or root.parent != Path("/tmp")
            or not root.name.startswith("jzmedia-cast-lan-")
            or len(root.name) <= len("jzmedia-cast-lan-")
            or root.exists() and (not root.is_dir() or any(root.iterdir()))):
        parser.error("--root must be a new or empty /tmp/jzmedia-cast-lan-* directory")
    try:
        url = urlsplit(args.base_url.rstrip("/"))
        address = ipaddress.ip_address(url.hostname or "")
        lan = address.version == 4 and any(address in ipaddress.ip_network(net) for net in (
            "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
        if (url.scheme != "http" or not lan or url.username or url.password or url.query
                or url.fragment or url.path or url.port != args.port
                or not 1024 <= args.port <= 65535 or args.port == 8080):
            raise ValueError
    except ValueError:
        parser.error("--base-url must be http://PRIVATE_IPV4:PORT matching a separate --port")
    if args.media_ttl < 7200:
        parser.error("--media-ttl must be at least 7200 seconds")
    base_url = args.base_url.rstrip("/")
    candidates = (Path.cwd(), Path("/app"), *Path(__file__).resolve().parents)
    repo = next((p for p in candidates if (p / "app/config.py").is_file()), None)
    if repo is None:
        parser.error("Run with the jzmedia source tree available (for example /app)")
    sys.path.insert(0, str(repo))
    root.mkdir(mode=0o700, exist_ok=True)
    media = root / "media"
    media.mkdir(mode=0o700)
    os.environ.update(DATA_DIR=str(root / "data"), MEDIA_ROOT=str(media),
                      TMDB_READ_TOKEN="", TMDB_API_KEY="", TMDB_PROXY="", JZMEDIA_TOKEN="",
                      TRANSCODER="sw", CAST_BASE_URL=base_url)
    ffmpeg, ffprobe = binary("ffmpeg"), binary("ffprobe")
    os.environ["PATH"] = os.pathsep.join((str(ffmpeg.parent), str(ffprobe.parent), os.environ.get("PATH", "")))
    filename = "casting-test.mp4"
    video = media / filename
    fonts = [Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
             Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf")]
    font = next((p for p in fonts if p.is_file()), None)
    filters = subprocess.run([str(ffmpeg), "-hide_banner", "-filters"],
                             capture_output=True, text=True, check=True, timeout=15).stdout
    picture_filter = None
    if font and "drawtext" in filters:
        picture_filter = (f"drawtext=fontfile={font}:text='JZMEDIA CAST TEST':x=20:y=20:"
                          "fontsize=28:fontcolor=white:box=1:boxcolor=black@0.7,"
                          f"drawtext=fontfile={font}:text='%{{pts\\:hms}}':x=20:y=h-55:"
                          "fontsize=28:fontcolor=white:box=1:boxcolor=black@0.7")
    command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin",
               "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=15",
               "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
               "-map", "0:v:0", "-map", "1:a:0", "-t", "180"]
    if picture_filter:
        command += ["-vf", picture_filter]
    command += ["-af", "volume=0.2", "-filter_threads", "1", "-c:v", "libx264",
                "-preset", "ultrafast", "-profile:v", "baseline", "-pix_fmt", "yuv420p",
                "-threads", "2", "-crf", "26", "-g", "30", "-c:a", "aac", "-b:a", "96k",
                "-ac", "2", "-movflags", "+faststart", str(video)]
    generated = subprocess.run(command, capture_output=True, text=True, timeout=240)
    if generated.returncode:
        raise SystemExit("Synthetic MP4 generation failed: " + generated.stderr[-2000:])
    probe = json.loads(subprocess.check_output([
        str(ffprobe), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(video)],
        text=True, timeout=20))
    sha256 = hashlib.sha256(video.read_bytes()).hexdigest()

    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
    from pydantic import BaseModel, Field
    from app import library_paths, store
    from app.db import ensure_dirs
    from app.casting import dlna
    from app.casting.service import manager
    from app.routers import cast

    ensure_dirs()
    store.init_db()
    library_id = library_paths.default_library()["id"]
    movie_id = store.upsert_movie_by_path(filename, library_id=library_id)
    store.update_movie_meta(movie_id, title="JZMEDIA CAST TEST · 180 秒合成视频")
    media_token = secrets.token_urlsafe(24)
    control_token = secrets.token_urlsafe(24)
    media_url = f"{base_url}/media/{media_token}.mp4"
    created = time.monotonic()
    revoked = False
    event_lock = threading.RLock()
    events_path = root / "events.jsonl"
    observations_path = root / "observations.jsonl"

    def append_event(event, path=events_path):
        event = {"at": datetime.now(timezone.utc).isoformat(), **event}
        with event_lock:
            with path.open("a", encoding="utf-8") as out:
                out.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def redacted_path(path):
        if path.startswith("/media/"):
            return "/media/<token>.mp4"
        if path.startswith("/api/cast/media/"):
            return "/api/cast/media/<session>"
        if path.startswith("/api/cast/sessions/"):
            return "/api/cast/sessions/<session>" + ("/control" if path.endswith("/control") else "")
        return path[:200]

    def strict_stop(sid):
        session = manager.get(sid)
        with session.lock:
            if not session.active:
                return {"stopped": False, "reason": "inactive"}
            try:
                track = dlna.action(session.device.transport, "GetPositionInfo", {"InstanceID": 0})
            except dlna.CastError:
                append_event({"event": "stop_skipped", "device": session.device.public(),
                              "reason": "unknown_ownership"})
                raise HTTPException(409, "无法确认电视仍在播放本次测试，未发送停止命令")
            if not track.get("TrackURI") or track["TrackURI"] != session.uri:
                append_event({"event": "stop_skipped", "device": session.device.public(),
                              "reason": "unknown_or_changed_ownership"})
                raise HTTPException(409, "电视当前资源不属于本次测试，未发送停止命令")
            result = manager.control(sid, "stop")
            append_event({"event": "stopped", "device": session.device.public()})
            return result

    def stop_browser_receivers():
        """Stop only the independent browser URL, never a different resource."""
        with manager.lock:
            devices = dict(manager.devices)
            for session in manager.sessions.values():
                devices.setdefault(session.device.id, session.device)
            locks = {key: manager.device_locks.setdefault(key, threading.RLock()) for key in devices}
        results = []
        for key, device in devices.items():
            with locks[key]:
                try:
                    track = dlna.action(device.transport, "GetPositionInfo", {"InstanceID": 0})
                    if not track.get("TrackURI") or track["TrackURI"] != media_url:
                        result = {"device": device.public(), "route": "browser", "result": "skipped",
                                  "reason": "unknown_or_changed_ownership"}
                    else:
                        dlna.action(device.transport, "Stop", {"InstanceID": 0})
                        result = {"device": device.public(), "route": "browser", "result": "stopped"}
                except dlna.CastError:
                    result = {"device": device.public(), "route": "browser", "result": "skipped",
                              "reason": "receiver_unreachable_or_rejected"}
            append_event({"event": "browser_cleanup", **result})
            results.append(result)
        return results

    @asynccontextmanager
    async def lifespan(_app):
        yield
        manager.shutdown()
        append_event({"event": "shutdown", "note": "No receiver Stop sent automatically"})

    app = FastAPI(title="Isolated LAN casting validation", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    class Capture:
        def __init__(self, target):
            self.target = target

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await self.target(scope, receive, send)
            headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope["headers"]}
            context = {"method": scope["method"], "path": redacted_path(scope["path"]),
                       "peer": (scope.get("client") or [None])[0],
                       "ua": headers.get("user-agent", "")[:400], "range": headers.get("range", "")[:200]}
            status, length, sent = None, None, 0
            started = time.monotonic()
            if scope["method"] not in ("GET", "HEAD"):
                origin = headers.get("origin")
                expected_origin = "http://" + headers.get("host", "")
                token = headers.get("x-fixture-control", "")
                if not secrets.compare_digest(token, control_token) or origin and origin != expected_origin:
                    response = JSONResponse({"detail": "Fixture control authorization required"}, status_code=403)
                    append_event({"event": "request_rejected", **context, "status": 403})
                    return await response(scope, receive, send)

            async def capture(message):
                nonlocal status, length, sent
                if message["type"] == "http.response.start":
                    status = message["status"]
                    response_headers = {k.decode("latin1").lower(): v.decode("latin1")
                                        for k, v in message.get("headers", [])}
                    length = response_headers.get("content-length")
                    append_event({"event": "response_start", **context, "status": status,
                                  "content_range": response_headers.get("content-range"),
                                  "content_length": length})
                elif message["type"] == "http.response.body":
                    sent += len(message.get("body", b""))
                await send(message)

            try:
                await self.target(scope, receive, capture)
            finally:
                append_event({"event": "response_end", **context, "status": status,
                              "content_length": length, "bytes_sent": sent,
                              "elapsed_ms": round((time.monotonic() - started) * 1000)})

    @app.post("/api/cast/sessions/{sid}/control")
    def guarded_control(sid: str, body: cast.ControlBody):
        if body.command == "stop":
            return strict_stop(sid)
        return cast.control(sid, body)

    app.include_router(cast.router)

    @app.get("/fixture/info")
    def info():
        with manager.lock:
            sessions = list(manager.sessions.values())
        return {"isolated": True, "movie_id": movie_id, "base_url": base_url,
                "media_url": media_url, "control_token": control_token,
                "media_authorized": not revoked and time.monotonic() - created < args.media_ttl,
                "media": {"size": video.stat().st_size, "sha256": sha256,
                          "duration": float(probe["format"]["duration"]), "visible_timer": bool(picture_filter),
                          "streams": [{key: item.get(key) for key in ("codec_type", "codec_name", "profile",
                                      "width", "height", "pix_fmt", "channels", "sample_rate")}
                                     for item in probe["streams"]]},
                "sessions": [s.public() for s in sessions]}

    @app.api_route("/media/{token}.mp4", methods=["GET", "HEAD"])
    def browser_media(token: str):
        if revoked or time.monotonic() - created >= args.media_ttl or not secrets.compare_digest(token, media_token):
            raise HTTPException(404, "测试资源授权不存在或已结束")
        return FileResponse(video, media_type="video/mp4", headers={
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    @app.get("/fixture/events")
    def events():
        with event_lock:
            lines = events_path.read_text(encoding="utf-8").splitlines() if events_path.exists() else []
        return {"events": [json.loads(line) for line in lines[-200:]]}

    class Observation(BaseModel):
        device: str = Field(min_length=1, max_length=120)
        route: str = Field(min_length=1, max_length=80)
        result: str = Field(min_length=1, max_length=80)
        notes: str = Field(default="", max_length=2000)

    @app.post("/fixture/observations")
    def observation(body: Observation):
        event = append_event({"event": "user_observation", **body.model_dump()}, observations_path)
        append_event(event)
        return {"saved": True}

    @app.get("/fixture/observations")
    def observations():
        with event_lock:
            lines = observations_path.read_text(encoding="utf-8").splitlines() if observations_path.exists() else []
        return {"items": [json.loads(line) for line in lines[-100:]]}

    @app.post("/fixture/revoke")
    def revoke():
        nonlocal revoked
        revoked = True
        append_event({"event": "browser_authorization_revoked"})
        return {"revoked": True}

    @app.post("/fixture/stop")
    def stop_all():
        with manager.lock:
            sessions = list(manager.sessions.values())
        results = []
        for session in sessions:
            try:
                strict_stop(session.id)
                results.append({"device": session.device.public(), "route": "dlna", "result": "stopped_or_inactive"})
            except (HTTPException, dlna.CastError) as exc:
                results.append({"device": session.device.public(), "route": "dlna", "result": "skipped", "reason": str(exc)})
        results.extend(stop_browser_receivers())
        return {"items": results}

    @app.post("/fixture/stop-browser")
    def stop_browser():
        return {"items": stop_browser_receivers()}

    @app.get("/", response_class=HTMLResponse)
    def page():
        bootstrap = json.dumps({"movie_id": movie_id, "base_url": base_url, "control_token": control_token})
        return HTMLResponse(PAGE.replace("__MEDIA_URL__", media_url).replace("__BOOTSTRAP__", bootstrap),
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                     "X-Content-Type-Options": "nosniff"})

    @app.get("/browser", response_class=HTMLResponse)
    def browser_page():
        return HTMLResponse(BROWSER_PAGE.replace("__MEDIA_URL__", media_url), headers={
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff"})

    append_event({"event": "ready", "base_url": base_url, "movie_id": movie_id,
                  "media_size": video.stat().st_size, "sha256": sha256})
    print(json.dumps({"ready": True, "base_url": base_url, "movie_id": movie_id,
                      "media_size": video.stat().st_size, "root": str(root)}), flush=True)
    import uvicorn
    uvicorn.run(Capture(app), host=args.host, port=args.port, log_level="warning",
                access_log=False, proxy_headers=False)


PAGE = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>JZMEDIA 投屏验证</title><style>
body{font:16px/1.5 system-ui,sans-serif;margin:0 auto;padding:20px;max-width:760px;color:#20242a;background:#f4f5f7}
section{background:white;border:1px solid #d9dce1;border-radius:12px;padding:16px;margin:18px 0}h1{font-size:24px}h2{font-size:20px}
button,select,input,textarea{font:inherit;box-sizing:border-box;min-height:44px;max-width:100%;padding:9px;border:1px solid #9299a3;border-radius:6px}
button{background:#fff;cursor:pointer;margin:4px 5px 4px 0}button:disabled{opacity:.5;cursor:wait}video{width:100%;background:#000}
select,input,textarea{width:100%;margin:6px 0}pre{font:13px/1.5 monospace;white-space:pre-wrap;overflow-wrap:anywhere}small{color:#525b66}
</style><h1>JZMEDIA 投屏验证</h1><p>仅播放 180 秒合成画面和轻声测试音，不读取真实媒体库。请先将电视停在桌面并开启内置投屏。</p>
<section id="dlna"><h2>① 服务器 DLNA</h2><button id="discover">搜索电视</button><label>选择设备<select id="devices"><option value="">请先搜索</option></select></label>
<button id="cast">播放合成视频</button><button data-command="pause">暂停</button><button data-command="play">恢复</button><button data-command="seek">跳转到 90 秒</button><button data-command="stop">停止本次测试</button><button id="refresh">刷新状态</button><pre id="status" role="status">尚未开始</pre>
<details><summary>搜索不到：手动添加有效 DLNA 描述地址</summary><input id="location" type="url" placeholder="http://电视IP:端口/description.xml"><button id="add">添加电视</button></details>
</section><section id="browser"><h2>② 小米全能播放器</h2><p><a href="/browser">打开仅含视频的小米测试页</a></p><p><a href="__MEDIA_URL__" rel="noreferrer">直接打开整片 MP4</a></p><small>请在独立视频页使用小米自带投屏入口。上方按钮属于服务器 DLNA，不用于记录小米播放器控制结果。</small></section>
<section><h2>③ 记录现场结果</h2><label>电视<input id="ob-device" placeholder="主卧的小米电视 / 客厅 Redmi"></label><label>测试方式<select id="ob-route"><option>服务器 DLNA</option><option>小米全能播放器</option></select></label>
<label>结果<select id="ob-result"><option>画面和声音正常</option><option>已完成暂停、恢复和跳转</option><option>手机锁屏后继续播放</option><option>切换应用后继续播放</option><option>退出网页后继续播放</option><option>失败或出现异常</option></select></label>
<label>补充情况<textarea id="ob-notes" rows="3" placeholder="例如：客厅未被发现；锁屏后停播；仅有声音无画面"></textarea></label><button id="save">保存观察结果</button><span id="saved" role="status"></span></section>
<script>
const cfg=__BOOTSTRAP__;let session=null,busy=false;
const el=id=>document.getElementById(id),status=el('status');
async function api(path,body){const r=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-Fixture-Control':cfg.control_token},body:body===undefined?undefined:JSON.stringify(body)});const data=await r.json();if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data));return data;}
async function perform(fn){if(busy)return;busy=true;document.querySelectorAll('#dlna button').forEach(b=>b.disabled=true);try{await fn()}catch(e){status.textContent=e.message}finally{busy=false;document.querySelectorAll('#dlna button').forEach(b=>b.disabled=false)}}
function show(data){status.textContent=JSON.stringify(data,null,2)}
function list(items){const select=el('devices'),previous=select.value;select.replaceChildren();for(const d of items){const o=document.createElement('option');o.value=d.id;o.textContent=d.name+' · '+d.address;select.append(o)}if(!items.length){const o=document.createElement('option');o.value='';o.textContent='未发现设备，请确认内置接收已开启';select.append(o)}if([...select.options].some(o=>o.value===previous))select.value=previous;}
el('discover').onclick=()=>perform(async()=>{status.textContent='正在搜索 NAS 所在局域网…';const d=await api('/api/cast/discover',{});list(d.items);show({发现设备:d.items})});
el('add').onclick=()=>perform(async()=>{await api('/api/cast/devices',{location:el('location').value});const d=await api('/api/cast/devices');list(d.items);show(d)});
el('cast').onclick=()=>perform(async()=>{if(!el('devices').value)throw Error('请先选择电视');session=await api('/api/cast/sessions',{device_id:el('devices').value,version_id:cfg.movie_id,kind:'movie',position:0,base_url:cfg.base_url});show(session)});
document.querySelectorAll('[data-command]').forEach(button=>button.onclick=()=>perform(async()=>{if(!session)throw Error('请先播放本次合成视频');const command=button.dataset.command;session=await api('/api/cast/sessions/'+session.id+'/control',{command,...(command==='seek'?{value:90}:{})});show(session)}));
el('refresh').onclick=()=>perform(async()=>{if(!session){const d=await api('/api/cast/current?version_id='+cfg.movie_id);session=d.session}if(session)session=await api('/api/cast/sessions/'+session.id);show(session||{状态:'没有当前测试会话'})});
el('save').onclick=async()=>{el('saved').textContent='保存中…';try{await api('/fixture/observations',{device:el('ob-device').value,route:el('ob-route').value,result:el('ob-result').value,notes:el('ob-notes').value});el('saved').textContent='已保存'}catch(e){el('saved').textContent=e.message}};
</script></html>'''

BROWSER_PAGE = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>小米播放器独立验证</title>
<style>body{font:16px/1.6 system-ui,sans-serif;max-width:760px;margin:0 auto;padding:20px}video{width:100%;background:#000}a{display:inline-block;padding:12px 0}</style>
<h1>小米播放器独立验证</h1><p>仅使用小米播放器自带的投屏入口和控件；若投屏后没有暂停、跳转按钮，请如实记录。</p>
<video controls preload="metadata" src="__MEDIA_URL__"></video>
<p><a href="__MEDIA_URL__" rel="noreferrer">直接打开整片 MP4</a></p>
<p>分别观察声画、暂停、跳转，以及手机锁屏、切应用和退出网页后的播放行为。</p>
<p><a href="/">返回服务器 DLNA 测试与结果记录</a></p></html>'''


if __name__ == "__main__":
    main()
