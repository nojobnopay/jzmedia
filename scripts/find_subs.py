#!/usr/bin/env python3
"""找出可用于字幕功能验证的片源（只读，不探测、不改库）。

列出：内嵌 ASS/SSA / PGS / VobSub 轨、字体附件、以及正片同名外挂字幕。
库与媒体路径与主程序同源：环境变量 DATA_DIR（默认 ./data）、MEDIA_ROOT（默认 ./media）。

用法：
    python scripts/find_subs.py            # 全库扫描
    python scripts/find_subs.py --limit 50 # 只看前 50 行
    python scripts/find_subs.py --json     # 机器可读
    docker compose exec -T mymedia python scripts/find_subs.py   # NAS 容器内（需镜像含 scripts/）
"""
import argparse
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.config import settings          # noqa: E402
from app import media as _media          # noqa: E402
from app.log import get_logger           # noqa: E402
from app.scanner import sidecar_subtitles  # noqa: E402

logger = get_logger("find_subs")

ASS = {"ass", "ssa"}
TEXT = {"srt", "subrip", "mov_text", "webvtt", "vtt"}


def _load(s: str) -> list:
    try:
        v = json.loads(s or "[]")
        return v if isinstance(v, list) else []
    except Exception:
        return []


def scan(limit: int = 0) -> dict:
    db = os.path.join(settings.data_dir, "jzmedia.db")
    if not os.path.isfile(db):
        raise SystemExit(f"库不存在：{db}（先启动一次服务或指对 DATA_DIR）")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    q = ("SELECT m.id, m.file_path FROM media_info mi JOIN movies m ON m.id = mi.movie_id "
         "WHERE mi.playable = 1 ORDER BY m.id")
    if limit:
        q += f" LIMIT {int(limit)}"
    items = []
    for r in conn.execute(q):
        mi = conn.execute("SELECT sub_json, attachments_json FROM media_info WHERE movie_id=?",
                          (r["id"],)).fetchone()
        subs = _load(mi["sub_json"])
        atts = _load(mi["attachments_json"])
        fonts = [a.get("name") for a in atts
                 if str(a.get("name", "")).lower().endswith((".ttf", ".otf", ".ttc", ".woff", ".woff2"))]
        emb = {"ass": [], "pgs": [], "vobsub": [], "text": []}
        for s in subs:
            codec = _media.norm_codec(str(s.get("codec") or ""))
            if codec in ASS:
                emb["ass"].append(s)
            elif codec == "pgs":
                emb["pgs"].append(s)
            elif codec == "vobsub":
                emb["vobsub"].append(s)
            elif codec in TEXT:
                emb["text"].append(s)
        try:
            side = sidecar_subtitles(os.path.join(settings.media_root, r["file_path"]))
        except Exception as e:
            logger.debug("sidecar enumerate failed file=%s: %s", r["file_path"], e)
            side = []
        if not any(emb.values()) and not fonts and not side:
            continue
        items.append({
            "movie_id": r["id"], "file_path": r["file_path"],
            "embedded": {k: [{"index": s.get("index"), "lang": s.get("lang"),
                              "title": s.get("title")} for s in v] for k, v in emb.items()},
            "fonts": fonts,
            "sidecars": [{"name": s["name"], "codec": s["codec"],
                          "image": int(s.get("image") or 0), "suffix": s.get("suffix")}
                         for s in side],
        })
    unprobed = conn.execute(
        "SELECT COUNT(*) FROM movies WHERE id NOT IN (SELECT movie_id FROM media_info)"
    ).fetchone()[0]
    conn.close()
    return {"items": items, "unprobed": int(unprobed),
            "media_root": settings.media_root, "data_dir": settings.data_dir}


def _fmt(it: dict) -> str:
    e = it["embedded"]
    tag = []
    if e["ass"]:
        tag.append("内嵌ASS" + str([s["lang"] or s["title"] or "?" for s in e["ass"]])[:60])
    if e["pgs"]:
        tag.append(f"内嵌PGS×{len(e['pgs'])}")
    if e["vobsub"]:
        tag.append(f"内嵌VobSub×{len(e['vobsub'])}")
    if e["text"]:
        tag.append(f"内嵌文本×{len(e['text'])}")
    if it["fonts"]:
        tag.append("字体附件" + str(it["fonts"])[:60])
    if it["sidecars"]:
        tag.append("外挂" + str([f"{s['name']}({s['codec']})" for s in it["sidecars"]])[:80])
    return f"#{it['movie_id']:<5} {it['file_path']}\n        " + "；".join(tag)


def main() -> int:
    ap = argparse.ArgumentParser(description="找出可测字幕片源（只读）")
    ap.add_argument("--limit", type=int, default=0, help="最多扫描多少行（0=全库）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()
    res = scan(limit=args.limit)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    its = res["items"]
    n_ass = sum(1 for i in its if i["embedded"]["ass"])
    n_pgs_emb = sum(1 for i in its if i["embedded"]["pgs"])
    n_side = sum(1 for i in its if i["sidecars"])
    n_side_ass = sum(1 for i in its if any(s["codec"] in ASS for s in i["sidecars"]))
    n_font = sum(1 for i in its if i["fonts"])
    print(f"MEDIA_ROOT={res['media_root']}  DATA_DIR={res['data_dir']}")
    print(f"命中 {len(its)} 片：内嵌ASS {n_ass} / 内嵌PGS {n_pgs_emb} / 字体附件 {n_font} / "
          f"有外挂字幕 {n_side}（其中 ASS/SSA {n_side_ass}）；未探测行 {res['unprobed']}")
    if res["unprobed"]:
        print("提示：未探测行先跑 POST /api/stream/probe-missing {\"limit\":200}（反复到 total=0）")
    print("-" * 88)
    for i in its:
        print(_fmt(i))
    print("-" * 88)
    print("验证建议：内嵌ASS→样式/JASSUB；内嵌PGS→切换不重开（客户端渲染）；"
          "外挂ASS/SUP→同客户端渲染；外挂VobSub→烧录")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
