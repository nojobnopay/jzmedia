#!/usr/bin/env python3
"""更新本批帮助站素材来源清单；已有历史截图的来源继续见 assets/README.md。"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs/assets"


def main():
    groups = [
        ("previews/onboarding/*.webp", "user-guide/onboarding.md", "真实四步向导及首播；隔离临时库、固定本地 NFO 与合成片源"),
        ("screenshots/demo-subtitles.webp", "user-guide/subtitles.md", "真实播放器：换字幕、调整延迟、加载本地 SRT"),
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
    for pattern, page, scene in groups:
        for path in sorted(ASSETS.glob(pattern)):
            entry = {"file": path.relative_to(ASSETS).as_posix(), "page": page,
                     "scene": scene, "verified_at": "2026-10-02", "bytes": path.stat().st_size,
                     "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            if path.suffix == ".mp4" and probe:
                media = json.loads(subprocess.check_output([probe, "-v", "error", "-show_entries",
                    "format=duration:stream=codec_name,width,height", "-of", "json", str(path)]))
                entry["duration_seconds"] = float(media["format"]["duration"])
                entry.update(media["streams"][0])
            entries.append(entry)
    data = {"schema_version": 1, "app_version": json.loads((ROOT / "frontend/package.json").read_text())["version"],
            "source_commit": subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip(),
            "source_state": "包含 2026-10-02 工作区新手向导和移动端更新；以拍摄时实际 UI 为准",
            "source": "Chromium + Playwright 操作隔离演示实例；无真实片库、无 API 响应模拟；合成视频和虚构资料",
            "viewport": {"desktop": "1440×1000 CSS，1x", "mobile": "407×904 CSS，2x，Chromium 模拟"},
            "recording_script": "scripts/capture_docs.py", "fixtures": "scripts/preview_onboarding.py --docs-demo",
            "legacy_sources": "docs/assets/README.md", "assets": entries}
    (ASSETS / "manifest.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
