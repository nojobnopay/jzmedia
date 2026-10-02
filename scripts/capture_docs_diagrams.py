#!/usr/bin/env python3
"""生成帮助站的可编辑 SVG 图；无第三方依赖。"""
from html import escape
from pathlib import Path


DEST = Path(__file__).resolve().parents[1] / "docs/assets/diagrams"


def text(x, y, value, size=20, color="#354153", weight=400):
    return f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}">{escape(value)}</text>'


def box(x, y, w, h, title, lines, accent="#c43b4c"):
    output = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" fill="#fff" stroke="#d8dfe8"/>'
    output += f'<rect x="{x}" y="{y+20}" width="5" height="{h-40}" rx="2" fill="{accent}"/>'
    output += text(x+24, y+39, title, 23, "#172236", 700)
    for i, line in enumerate(lines):
        output += text(x+24, y+76+i*28, line, 18)
    return output


def arrow(x1, y1, x2, y2):
    return f'<path d="M{x1} {y1} L{x2} {y2}" fill="none" stroke="#8f9bab" stroke-width="3" marker-end="url(#arrow)"/>'


def svg(name, title, desc, body, height=510):
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / f"{name}.svg").write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 {height}" role="img" aria-labelledby="title desc">\n'
        f'<title id="title">{escape(title)}</title><desc id="desc">{escape(desc)}</desc>\n'
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10z" fill="#8f9bab"/></marker></defs>\n'
        f'<rect width="1000" height="{height}" rx="22" fill="#f6f8fb"/>\n'
        '<g font-family="system-ui, Noto Sans CJK SC, sans-serif">\n'
        + text(36, 52, title, 28, "#172236", 700) + body + '</g></svg>\n', encoding="utf-8")


def main():
    svg("libraries", "先连接存储，再划分电影和剧集", "媒体库保存本地、SMB 或 NFS 的存储连接；视频库选择媒体库中的子目录和电影或剧集类型。",
        box(40, 110, 390, 186, "① 媒体库 · 文件在哪里", ["示例：家里的 NAS", "连接类型：本地 / SMB / NFS", "连接、地址和账号在这里设置"])
        + arrow(432, 160, 535, 160) + arrow(432, 250, 535, 325)
        + box(555, 105, 405, 150, "② 视频库 · 电影", ["子目录：电影/", "按电影方式扫描、观看和整理"])
        + box(555, 285, 405, 150, "② 视频库 · 剧集", ["子目录：剧集/", "按剧、季、集组织内容"], "#267b76")
        + text(42, 363, "一个媒体库可以包含多个视频库。", 21, "#172236", 600)
        + text(42, 399, "扫描、上传和整理时，请先确认目标视频库。", 18))
    svg("ingestion", "让第一部内容进入媒体库", "确认视频库后，服务器已有文件选择扫描，当前设备文件选择上传；核对识别结果并完成首播。目录整理另行预览确认。",
        box(35, 100, 265, 125, "1  确认视频库", ["检查目录连接与内容类型"])
        + arrow(302, 163, 355, 163)
        + box(375, 100, 270, 125, "2  添加内容", ["服务器已有 → 扫描"])
        + box(375, 245, 270, 125, "或：从设备上传", ["选择文件或文件夹"], "#267b76")
        + arrow(300, 175, 355, 310) + arrow(647, 160, 705, 160) + arrow(647, 309, 705, 175)
        + box(725, 100, 240, 170, "3  核对并播放", ["检查标题与待办", "打开详情开始播放"])
        + text(38, 437, "扫描 / 上传完成后，目录整理仍需单独预览和确认。", 23, "#172236", 600))
    svg("organizing", "整理之前，先核对每一条路径", "整理预览显示原目录与目标目录；确认后才执行移动和改名。正片与同茎字幕跟随，已存在的目标文件不覆盖。",
        box(38, 108, 405, 232, "整理前 · 示例", ["待归档演示/", "  Star.Voyage.2026.mp4", "  Star.Voyage.2026.chs.srt", "  movie.nfo"])
        + arrow(450, 222, 527, 222)
        + box(550, 108, 410, 232, "整理后 · 示例", ["待归档演示/星海漫游 (2026)/", "  星海漫游 (2026).mp4", "  星海漫游 (2026).chs.srt", "  movie.nfo"], "#267b76")
        + text(42, 396, "预览 → 核对目标 → 勾选项目 → 确认执行 → 检查结果", 24, "#172236", 600)
        + text(42, 439, "实际名称以应用预览为准；目标冲突不覆盖，需要撤回时使用还原位置。", 19))


if __name__ == "__main__":
    main()
