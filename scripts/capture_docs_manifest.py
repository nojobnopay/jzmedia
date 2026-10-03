#!/usr/bin/env python3
"""登记明确拍摄的素材；单独运行只迁移来源格式并报告未核对变化。"""
from datetime import date
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs/assets"


def source_fingerprint():
    """Identify source at registration time, without reading config or runtime data."""
    patterns = ("app/**/*.py", "frontend/src/**/*", "frontend/public/**/*",
                "frontend/index.html", "frontend/package*.json", "frontend/vite.config.*",
                "requirements*.txt", "scripts/capture_docs.py", "scripts/preview_onboarding.py")
    files = sorted({path for pattern in patterns for path in ROOT.glob(pattern) if path.is_file()})
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(ROOT).as_posix().encode() + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest(), list(patterns)


def inherited_entry(entry, previous):
    """Make the old onboarding batch defaults explicit, without claiming a recapture."""
    entry = dict(entry)
    if "source" not in entry and previous.get("provenance_scope") != "per_asset":
        for key in ("app_version", "source_commit", "source_state", "source", "recording_script", "fixtures"):
            if key in previous:
                entry.setdefault(key, previous[key])
        entry["provenance_note"] = "来源由旧清单顶层默认迁入；没有因此重新拍摄或核对。"
        if entry["file"].startswith("diagrams/"):
            entry.update(source="可编辑业务概念图，由仓库脚本生成 SVG",
                         recording_script="scripts/capture_docs_diagrams.py", fixtures="无媒体数据")
            entry.pop("source_state", None)
    return entry


def main(captured=(), reviewed=None):
    """captured is supplied by the capture tool; reviewed maps unchanged files to evidence."""
    captured = set(captured)
    reviewed = reviewed or {}
    manifest = ASSETS / "manifest.json"
    previous = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
    previous_assets = {entry["file"]: inherited_entry(entry, previous) for entry in previous.get("assets", [])}
    groups = [
        ("previews/onboarding/*.webp", "user-guide/onboarding.md", "真实四步向导及首播；隔离临时库、固定本地 NFO 与合成片源"),
        ("screenshots/demo-subtitles.webp", "user-guide/subtitles.md", "真实播放器：换字幕、调整延迟、加载本地 SRT"),
        ("screenshots/player-subtitles.webp", "user-guide/subtitles.md", "真实播放器选择合成片源的中英文外挂字幕"),
        ("screenshots/player-info.webp", "user-guide/playback-previews.md", "真实播放器的预览入口、播放信息与诊断；1080p 合成片源"),
        ("screenshots/player-transcode.webp", "user-guide/playback-controls.md", "1080p H.264 合成片源实际转码为 720p；隔离软件转码会话"),
        ("screenshots/demo-organize-*.webp", "user-guide/organizing.md", "真实整理预览及执行结果；仅临时合成文件"),
        ("videos/onboarding.*", "user-guide/onboarding.md", "四步向导 → 真实扫描入库 → 完成 → 首播，未经速度变更的真实 UI 录屏"),
        ("videos/subtitles.*", "user-guide/subtitles.md", "换字幕 → 延后 0.5 秒 → 文件选择器加载本地 SRT，真实 UI 录屏"),
        ("videos/organizing.*", "user-guide/organizing.md", "预览 → 点击整理选中确认执行 → 结果核对 → 文件管理，真实 UI 录屏"),
        ("diagrams/libraries.svg", "user-guide/libraries.md", "媒体库与视频库关系；代码生成 SVG，可编辑源为 scripts/capture_docs_diagrams.py"),
        ("diagrams/ingestion.svg", "user-guide/onboarding.md", "入库任务流；代码生成 SVG，可编辑源为 scripts/capture_docs_diagrams.py"),
        ("diagrams/organizing.svg", "user-guide/organizing.md", "就地整理前后路径示例；代码生成 SVG，可编辑源为 scripts/capture_docs_diagrams.py"),
    ]
    probe = shutil.which("ffprobe")
    if not probe:
        matches = list((ROOT / ".venv/lib").glob("python*/site-packages/static_ffmpeg/bin/*/ffprobe"))
        probe = str(matches[0]) if matches else None
    entries = []
    pending = {}
    supported = set()
    source_hash, source_scope = source_fingerprint() if captured else (None, None)
    for pattern, page, scene in groups:
        for path in sorted(ASSETS.glob(pattern)):
            entry = {"file": path.relative_to(ASSETS).as_posix(), "page": page,
                     "scene": scene, "bytes": path.stat().st_size,
                     "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            supported.add(entry["file"])
            old = previous_assets.get(entry["file"])
            if entry["file"] not in captured:
                # Even changed bytes are not evidence that this capture tool produced them.
                if old:
                    entries.append(old)
                if not old or old.get("sha256") != entry["sha256"] or old.get("bytes") != entry["bytes"]:
                    pending[entry["file"]] = {"file": entry["file"], "bytes": entry["bytes"], "sha256": entry["sha256"],
                                              "reason": "文件内容已变更，保留上次拍摄记录" if old else "素材尚无拍摄来源记录"}
                continue
            if path.suffix == ".mp4" and probe:
                media = json.loads(subprocess.check_output([probe, "-v", "error", "-show_entries",
                    "format=duration:stream=codec_name,width,height", "-of", "json", str(path)]))
                entry["duration_seconds"] = float(media["format"]["duration"])
                entry.update(media["streams"][0])
            entry.update({
                "verified_at": date.today().isoformat(),
                "captured_at": date.today().isoformat(),
                "app_version": json.loads((ROOT / "frontend/package.json").read_text())["version"],
                "source_commit": subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip(),
                "source": "Chromium + Playwright 操作隔离演示实例；无真实片库、无 API 响应模拟；合成视频和虚构资料",
                "recording_script": "scripts/capture_docs.py",
                "fixtures": "scripts/preview_onboarding.py --docs-demo",
                "source_state": "拍摄完成后登记时的工作区；真实应用与临时数据库、1080p 合成片源",
                "source_sha256": source_hash,
                "source_digest_scope": source_scope,
                "viewport": {"width": 407, "height": 904, "device_scale_factor": 2}
                    if entry["file"] == "previews/onboarding/06-mobile.webp"
                    else {"width": 1440, "height": 1000, "device_scale_factor": 1},
            })
            if path.suffix == ".svg":
                entry.update(source="可编辑业务概念图，由仓库脚本生成 SVG",
                             recording_script="scripts/capture_docs_diagrams.py", fixtures="无媒体数据")
                entry.pop("viewport", None)
                entry["generated_at"] = entry.pop("captured_at")
                entry["source_state"] = "生成后登记时的 SVG 生成脚本"
                entry["source_sha256"] = hashlib.sha256((ROOT / "scripts/capture_docs_diagrams.py").read_bytes()).hexdigest()
                entry["source_digest_scope"] = ["scripts/capture_docs_diagrams.py"]
            entries.append(entry)
    if captured - supported:
        raise ValueError(f"未找到受本脚本管理的已拍摄文件：{sorted(captured - supported)}")
    data = dict(previous)
    for key in ("app_version", "source_commit", "source_state", "viewport", "recording_script", "fixtures"):
        data.pop(key, None)
    data.update(schema_version=1, provenance_scope="per_asset",
                source="混合拍摄与生成批次；版本、日期、来源、脚本和视口均以 assets 中的逐项记录为准。",
                legacy_sources="docs/assets/README.md")
    regenerated = {entry["file"] for entry in entries}
    data["assets"] = entries + [entry for entry in previous_assets.values()
                               if entry["file"] not in regenerated]
    for entry in data["assets"]:
        path = ASSETS / entry["file"]
        if not path.is_file():
            pending[entry["file"]] = {"file": entry["file"], "reason": "清单记录的文件不存在"}
        else:
            content = path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            if entry.get("bytes") != len(content) or entry.get("sha256") != digest:
                pending[entry["file"]] = {"file": entry["file"], "bytes": len(content), "sha256": digest,
                                          "reason": "文件内容已变更，保留上次拍摄记录"}
        if entry["file"] in reviewed:
            if entry["file"] in pending or not reviewed[entry["file"]]:
                raise ValueError(f"核对记录要求文件与清单一致且注明依据：{entry['file']}")
            entry.update(reviewed_at=date.today().isoformat(), review_basis=reviewed[entry["file"]])
    unknown_reviews = set(reviewed) - {entry["file"] for entry in data["assets"]}
    if unknown_reviews:
        raise ValueError(f"核对记录没有对应素材：{sorted(unknown_reviews)}")
    data["unverified_assets"] = [pending[name] for name in sorted(pending)]
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for entry in data["unverified_assets"]:
        print(f"待核对：{entry['file']} — {entry['reason']}", file=sys.stderr)
    return data


if __name__ == "__main__":
    main()
