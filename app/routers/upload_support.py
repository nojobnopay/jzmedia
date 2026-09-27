"""Upload destinations: validate scope before creating directories or writing bytes."""
import os

from fastapi import HTTPException

from .. import library_paths, scanner, store
from ..scanner import tv_parse
from ..scanner.tv_organize import _resolve_show_dir
from .files import _safe_component


def upload_library(library_id, media_type=None):
    lib = library_paths.get_library(library_id)
    if not lib:
        raise HTTPException(404, "视频库不存在")
    kind = lib.get("kind") or "movie"
    if kind not in ("movie", "tv") or (media_type and kind != media_type):
        raise HTTPException(422, "上传类型与视频库类型不一致")
    if not lib.get("enabled", True) or not lib.get("media_enabled", True):
        raise HTTPException(409, "视频库已停用，请先启用")
    if lib.get("read_only"):
        raise HTTPException(409, "视频库只读，无法上传")
    return lib


def tv_destination(lid, rel, mode, show_id=None, show_title="", season=None):
    if mode == "dir":
        parts = rel.split("/")
        if len(parts) < 2 or tv_parse.season_from_dir(parts[0]) is not None:
            raise HTTPException(422, "请选择包含剧名的完整文件夹；散文件请指定所属剧和季")
        root = parts[0]
        return rel, root, store.find_show_by_dir_prefix(root, lid)
    if season is None or not (show_id or show_title.strip()):
        raise HTTPException(422, "散文件上传必须指定所属剧和季")
    if "/" in rel:
        raise HTTPException(422, "散文件模式只接受文件名")
    if show_id:
        show = store.get_show_meta(show_id)
        if not show or int(show["library_id"]) != lid:
            raise HTTPException(422, "所选剧集不属于目标视频库")
        root = _resolve_show_dir(store.list_episodes(show_id), show_id=show_id, library_id=lid)
        if not root:
            raise HTTPException(422, "无法确定已有剧集目录，请使用文件夹上传")
    else:
        if any(x in show_title for x in ("/", "\\", "..")):
            raise HTTPException(422, "剧名不能包含路径分隔符或 ..")
        root = _safe_component(show_title)
        if not root or root.startswith(".") or tv_parse.season_from_dir(root) is not None:
            raise HTTPException(422, "请输入有效剧名")
        show_id = store.find_show_by_dir_prefix(root, lid)
    if os.path.splitext(rel)[1].lower() in scanner.VIDEO_EXTS:
        parsed = tv_parse.parse_episode(rel)
        if parsed.get("season") is not None and int(parsed["season"]) != season:
            raise HTTPException(422, "文件名季号与选择的季号不一致，请核对后上传")
    return f"{root}/Season {season:02d}/{rel}", root, show_id


def register_tv_upload(backend, rel, root, show_id=None):
    if os.path.splitext(rel)[1].lower() not in scanner.VIDEO_EXTS:
        return {"status": "stored", "show_id": show_id, "episode_id": None}
    return scanner.scan_tv_file(backend, rel, show_root=root, target_show_id=show_id)
