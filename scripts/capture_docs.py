#!/usr/bin/env python3
"""重拍文档：仅连接 preview_onboarding.py --docs-demo 创建的 /tmp 隔离实例。

准备：在拍摄 Python 环境安装 playwright，运行 python -m playwright install chromium。
启动：.venv/bin/python scripts/preview_onboarding.py --port 18128 --docs-demo
拍摄：python scripts/capture_docs.py /tmp/jzmedia-preview-<启动器打印的目录>

截图、三段真实 UI 录屏及中文 WebVTT 写入 docs/assets。不会连接主实例。
源视频来自合成测试图案；浏览器操作全走应用 UI，不伪造 API 响应。
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs/assets"
CAPTURED = set()


def timestamp(seconds):
    milliseconds = round(seconds * 1000)
    return f"{milliseconds // 3600000:02d}:{milliseconds // 60000 % 60:02d}:{milliseconds // 1000 % 60:02d}.{milliseconds % 1000:03d}"


def binary(name):
    found = shutil.which(name)
    if found:
        return found
    candidates = list((ROOT / ".venv/lib").glob(f"python*/site-packages/static_ffmpeg/bin/*/{name}"))
    if candidates:
        return str(candidates[0])
    raise SystemExit(f"请先安装 {name}，或在应用 .venv 中安装并准备 static-ffmpeg。")


def screenshot(page, rel, work):
    destination = ASSETS / rel
    destination.parent.mkdir(parents=True, exist_ok=True)
    png = work / (destination.stem + ".png")
    page.screenshot(path=str(png))
    subprocess.run([binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(png), "-quality", "86", str(destination)], check=True)
    CAPTURED.add(rel)


class Recording:
    def __init__(self, browser, name, work):
        self.name, self.work = name, work
        self.context = browser.new_context(viewport={"width": 1440, "height": 1000},
            record_video_dir=str(work), record_video_size={"width": 1440, "height": 1000},
            locale="zh-CN", color_scheme="dark")
        self.page = self.context.new_page()
        self.page.set_default_timeout(20000)
        self.started = time.monotonic()
        self.cues = []

    def say(self, line):
        self.cues.append((time.monotonic() - self.started, line))
        print(f"{self.name}: {line}", flush=True)

    def hold(self, seconds=4):
        self.page.wait_for_timeout(seconds * 1000)

    def click(self, locator):
        locator.scroll_into_view_if_needed()
        locator.hover()
        self.hold(.6)
        locator.click()

    def screenshot(self, rel):
        screenshot(self.page, rel, self.work)

    def finish(self):
        duration = time.monotonic() - self.started
        if duration < 32:
            self.hold(32 - duration)
            duration = time.monotonic() - self.started
        self.context.close()
        source = self.page.video.path()
        dest = ASSETS / "videos" / self.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                        "-i", source, "-c:v", "libx264", "-preset", "slow", "-crf", "27",
                        "-vf", "fps=20", "-pix_fmt", "yuv420p", "-an", "-threads", "2",
                        "-movflags", "+faststart", str(dest.with_suffix(".mp4"))], check=True)
        subprocess.run([binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                        "-ss", "4", "-i", str(dest.with_suffix(".mp4")), "-frames:v", "1",
                        "-quality", "86", str(dest.with_suffix(".webp"))], check=True)
        lines = ["WEBVTT", ""]
        for index, (start, line) in enumerate(self.cues):
            end = self.cues[index + 1][0] if index + 1 < len(self.cues) else duration
            lines += [str(index + 1), f"{timestamp(start)} --> {timestamp(end)}", line, ""]
        dest.with_suffix(".vtt").write_text("\n".join(lines), encoding="utf-8")
        CAPTURED.update(f"videos/{self.name}.{ext}" for ext in ("mp4", "webp", "vtt"))
        info = json.loads(subprocess.check_output([binary("ffprobe"), "-v", "error",
            "-show_entries", "format=duration,size:stream=codec_name,width,height", "-of", "json",
            str(dest.with_suffix(".mp4"))]))
        assert 30 <= float(info["format"]["duration"]) <= 90, info
        assert int(info["format"]["size"]) < 10_000_000, info
        assert info["streams"][0]["codec_name"] == "h264", info
        print(json.dumps({self.name: info}, ensure_ascii=False), flush=True)
        return info


def play(recording, base):
    page = recording.page
    page.goto(base + "/m/1")
    button = page.locator(".play-main")
    button.wait_for(state="visible")
    recording.click(button)
    page.wait_for_function("() => document.querySelector('video')?.readyState >= 2 || document.querySelector('.resume-bar')")
    restart = page.get_by_role("button", name="从头开始", exact=True)
    if restart.is_visible():
        recording.click(restart)
    page.wait_for_function("() => { const v = document.querySelector('video'); return v && v.readyState >= 2 && !v.paused }")


def onboarding(browser, base, work):
    r = Recording(browser, "onboarding", work)
    p = r.page
    p.goto(base)
    p.get_by_text("欢迎使用 jzmedia", exact=True).wait_for()
    r.say("首次使用：从欢迎卡片进入四步配置。演示数据和视频均为虚构。")
    r.hold(3)
    r.screenshot("previews/onboarding/00-welcome.webp")
    r.click(p.get_by_role("link", name="开始配置", exact=True))
    p.get_by_role("button", name="稍后配置 TMDB", exact=True).wait_for()
    r.say("第一步：配置资料来源。本次使用本地 NFO，选择稍后配置 TMDB。")
    r.hold(4)
    r.screenshot("previews/onboarding/01-source.webp")
    r.click(p.get_by_role("button", name="稍后配置 TMDB", exact=True))
    p.get_by_role("button", name="检查并使用此视频库", exact=True).wait_for()
    r.say("第二步：核对存储位置与视频库。这里使用隔离环境预设的本地电影库。")
    r.hold(5)
    r.screenshot("previews/onboarding/02-library.webp")
    r.click(p.get_by_role("button", name="检查并使用此视频库", exact=True))
    p.get_by_role("button", name="扫描此视频库", exact=True).wait_for()
    r.say("第三步：扫描服务器已有文件；从当前设备添加文件时可改选上传。")
    r.hold(4)
    r.screenshot("previews/onboarding/03-import.webp")
    r.click(p.get_by_role("button", name="扫描此视频库", exact=True))
    p.get_by_role("button", name="查看入库结果", exact=True).wait_for(state="visible")
    p.wait_for_function("() => [...document.querySelectorAll('button')].some(b => b.textContent === '查看入库结果' && !b.disabled)")
    r.say("等待扫描完成：出现已登记的电影后，查看入库结果。扫描不会自动整理目录。")
    r.hold(4)
    r.click(p.get_by_role("button", name="查看入库结果", exact=True))
    r.hold(2)
    r.say("第四步：核对影片名称及待办，点击完成引导。")
    r.screenshot("previews/onboarding/04-result.webp")
    r.hold(3)
    r.click(p.get_by_role("button", name="完成引导", exact=True))
    p.get_by_role("heading", name="基本配置已完成").wait_for()
    r.screenshot("previews/onboarding/05-complete.webp")
    r.hold(3)
    r.click(p.get_by_role("link", name=re.compile("查看详情与播放")))
    p.locator(".play-main").wait_for()
    r.say("打开影片详情，点击播放影片，确认文件能够播放。")
    r.hold(3)
    r.click(p.locator(".play-main"))
    p.wait_for_function("() => { const v = document.querySelector('video'); return v && v.readyState >= 2 && !v.paused }")
    r.hold(4)
    r.screenshot("previews/onboarding/07-play.webp")
    r.say("首部内容已入库并开始播放。之后可在设置中补配资料来源或添加其他视频库。")
    r.hold(4)
    return r.finish()


def subtitles(browser, base, preview, work):
    r = Recording(browser, "subtitles", work)
    p = r.page
    r.say("播放影片后，点击右下角齿轮，打开播放设置。")
    play(r, base)
    r.hold(3)
    r.click(p.get_by_role("button", name="播放设置", exact=True))
    p.get_by_role("combobox", name="字幕轨道").wait_for()
    r.hold(3)
    r.say("在字幕轨道中选择英文或中文；切换字幕不会重新开始播放。")
    track = p.get_by_role("combobox", name="字幕轨道")
    english = track.locator("option").evaluate_all(
        "options => options.find(o => /英文|eng|English/i.test(o.textContent))?.value")
    assert english is not None, track.inner_text()
    track.select_option(value=english)
    r.hold(4)
    p.get_by_role("combobox", name="字幕轨道").select_option(index=1)
    r.hold(3)
    r.screenshot("screenshots/player-subtitles.webp")
    r.say("字幕与声音不同步时，展开字幕调整；每次提前或延后 0.5 秒。")
    r.click(p.get_by_text("字幕调整", exact=True))
    r.click(p.get_by_role("button", name="字幕延后 0.5 秒", exact=True))
    r.hold(4)
    r.say("加载自己的字幕：点击加载字幕文件，选择本机的 SRT、VTT、ASS 或 SSA 文件。")
    with p.expect_file_chooser() as chooser:
        r.click(p.get_by_role("button", name="加载字幕文件", exact=True))
    chooser.value.set_files(str(preview / "本地临时字幕.srt"))
    p.get_by_role("button", name="移除本地字幕", exact=True).wait_for()
    r.hold(4)
    r.screenshot("screenshots/demo-subtitles.webp")
    r.say("本地字幕已选中并显示；仅本次播放器有效，不会写入媒体库。")
    r.click(p.get_by_role("button", name="关闭设置", exact=True))
    r.hold(6)
    return r.finish()


def organizing(browser, base, work):
    r = Recording(browser, "organizing", work)
    p = r.page
    p.goto(base + "/settings?sec=sec-libtools&library=1")
    r.say("进入设置的扫描与整理，核对当前视频库。本次仅操作临时演示文件。")
    p.get_by_role("button", name=re.compile("③.*目录整理")).wait_for()
    r.hold(4)
    heading = p.get_by_role("button", name=re.compile("③.*目录整理"))
    if heading.get_attribute("aria-expanded") != "true":
        r.click(heading)
    p.get_by_role("button", name="刷新预览", exact=True).wait_for()
    r.click(p.get_by_role("button", name="刷新预览", exact=True))
    p.locator(".plan-item").first.wait_for()
    r.say("先看预览：就地整理保留上层目录；逐行核对原路径和目标路径。")
    r.hold(6)
    r.screenshot("screenshots/demo-organize-preview.webp")
    r.say("确认标题、目录和文件名正确，保留需要执行的勾选，再点击整理选中。")
    r.hold(5)
    r.click(p.get_by_role("button", name="整理选中 (1)", exact=True))
    p.get_by_text(re.compile("执行完毕.*已移动")).wait_for()
    r.say("等待执行结束，核对已移动的数量和结果。预览后才会实际移动文件。")
    r.hold(5)
    r.screenshot("screenshots/demo-organize-result.webp")
    r.click(p.get_by_role("button", name="管理文件", exact=True))
    p.get_by_role("heading", name="文件管理", exact=True).wait_for()
    r.say("打开管理文件，检查规范后的电影目录。需要撤回时使用还原位置。")
    r.hold(7)
    return r.finish()


def player_details(browser, base, work):
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
    try:
        page = context.new_page()
        page.goto(base + "/m/1")
        page.locator(".play-main").click()
        page.wait_for_function("() => document.querySelector('video')?.readyState >= 2 || document.querySelector('.resume-bar')")
        restart = page.get_by_role("button", name="从头开始", exact=True)
        if restart.is_visible():
            restart.click()
        page.wait_for_function("() => document.querySelector('video')?.readyState >= 2")
        page.get_by_role("button", name="播放设置", exact=True).click()
        page.locator("summary").filter(has_text="更多选项").click()
        page.get_by_role("button", name="复制诊断信息", exact=True).scroll_into_view_if_needed()
        page.wait_for_timeout(1000)
        screenshot(page, "screenshots/player-info.webp", work)
        page.get_by_label("画质", exact=True).select_option("720p")
        page.wait_for_function("() => document.querySelector('video')?.readyState >= 2 && document.querySelector('.quality-badge')?.textContent === '720p'", timeout=60000)
        page.get_by_text("实际输出 720p", exact=True).wait_for()
        page.get_by_role("button", name="复制诊断信息", exact=True).scroll_into_view_if_needed()
        page.wait_for_timeout(1000)
        screenshot(page, "screenshots/player-transcode.webp", work)
        page.get_by_role("button", name="关闭播放器", exact=True).click()
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("preview", type=Path)
    parser.add_argument("--only", choices=["onboarding", "subtitles", "organizing", "player"])
    args = parser.parse_args()
    preview = args.preview.resolve()
    if preview.parent != Path("/tmp") or not preview.name.startswith("jzmedia-preview-"):
        parser.error("只允许 preview_onboarding.py 创建的 /tmp/jzmedia-preview-* 目录")
    config = json.loads((preview / "preview.json").read_text(encoding="utf-8"))
    base = config["url"]
    parsed = urlparse(base)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or parsed.port in {80, 443, 8080}:
        parser.error("拍摄仅允许独立的本机演示端口")
    for key in ("data", "media", "runtime"):
        if not Path(config[key]).resolve().is_relative_to(preview):
            parser.error("演示配置包含临时目录以外的路径")
    if not (preview / "本地临时字幕.srt").is_file():
        parser.error("请用 --docs-demo 启动新的隔离实例")
    work = Path(tempfile.mkdtemp(prefix="jzmedia-docs-capture-", dir="/tmp"))
    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            if args.only in (None, "onboarding"):
                results["onboarding"] = onboarding(browser, base, work)
                mobile = browser.new_context(viewport={"width": 407, "height": 904},
                    device_scale_factor=2, is_mobile=True, has_touch=True, locale="zh-CN")
                page = mobile.new_page()
                page.goto(base + "/setup")
                page.get_by_role("heading", name="基本配置已完成").wait_for()
                page.screenshot(path=str(work / "mobile.png"))
                subprocess.run([binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-i",
                    str(work / "mobile.png"), "-quality", "86", str(ASSETS / "previews/onboarding/06-mobile.webp")], check=True)
                CAPTURED.add("previews/onboarding/06-mobile.webp")
                mobile.close()
            if args.only in (None, "subtitles"):
                results["subtitles"] = subtitles(browser, base, preview, work)
            if args.only in (None, "organizing"):
                results["organizing"] = organizing(browser, base, work)
            if args.only in (None, "player"):
                player_details(browser, base, work)
        finally:
            browser.close()
    (work / "verification.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    from capture_docs_manifest import main as update_manifest
    update_manifest(captured=CAPTURED)
    print(f"录屏原件及验证记录：{work}")


if __name__ == "__main__":
    main()
