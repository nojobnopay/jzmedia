"""预览独立的视频时间轴；不修改播放器使用的容器时长或媒体探测缓存。"""
import json
import math
import re

from ... import media
from ...log import get_logger
from .preview_log import ExtractionError


logger = get_logger("stream.previews")
_TAIL_SECONDS = 300.0
_DURATION_TAG = re.compile(r"^(\d{1,3}):([0-5]\d):([0-5]\d(?:\.\d{1,9})?)$")


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _tag_seconds(value):
    match = _DURATION_TAG.fullmatch(str(value or ""))
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _video_stream(streams):
    videos = [v for v in streams if isinstance(v, dict) and v.get("codec_type") == "video"]
    for video in videos:
        if _number((video.get("disposition") or {}).get("attached_pic")):
            continue
        if video.get("codec_name") in ("png", "mjpeg", "bmp") and len(videos) > 1:
            continue
        index = _number(video.get("index"))
        if index is not None and index >= 0 and index.is_integer():
            return video, int(index)
    return None, None


def _json_probe(cmd, jid, run_probe, *, timeout):
    try:
        result = json.loads(run_probe(cmd, jid, timeout=timeout))
    except (ExtractionError, OSError, ValueError, UnicodeError, TypeError):
        # 取消及播放让行的异常由调用方处理，不能降级后继续占用播放器资源。
        return None
    return result if isinstance(result, dict) else None


def _metadata_duration(video, origin, container_duration):
    start = _number(video.get("start_time"))
    start = origin if start is None else start
    duration = _number(video.get("duration"))
    if duration is not None and duration > 0:
        endpoint = start + duration - origin
        if 0 < endpoint <= container_duration + 1:
            return min(endpoint, container_duration), "stream_duration"

    tags = {str(k).upper(): v for k, v in (video.get("tags") or {}).items()}
    endpoint = _tag_seconds(tags.get("DURATION"))
    if endpoint is None:
        return None
    # Matroska 的 DURATION 标签是源时间轴上的结束时刻，可能包含非零 start_time。
    duration = endpoint - origin
    if not 0 < duration <= container_duration + 1:
        return None
    # 统计标签可以过时。拒绝明显短于容器的孤立标签，或与帧数矛盾的标签，
    # 再以尾部包确认；不依据损坏标签直接丢弃影片后段。
    frames = _number(tags.get("NUMBER_OF_FRAMES"))
    fps = media._frame_rate(video.get("avg_frame_rate"))
    if frames is not None and frames > 0 and fps:
        measured = endpoint - start
        if measured <= 0 or abs(frames / fps - measured) > max(2.0, measured * 0.01):
            return None
    elif container_duration - duration > _TAIL_SECONDS:
        return None
    return min(duration, container_duration), "video_tag"


def resolve_timeline(src, info, *, run_probe, jid):
    """返回 duration / video_stream_index / source；所有子进程复用可取消的回调。

    已有完整缓存应由调用方直接复用，避免每次 GET 或生成请求都再次访问片源。
    无可信流时长时，仅读取容器末尾 300 秒的压缩包，不解码、不全片扫描。
    无法确认尾部时保留原时长，让正常抽帧报告失败，不拿早期画面填满尾页。
    """
    duration = _number(info.get("duration"))
    if duration is None or not 0 < duration <= 86400:
        raise RuntimeError("无法取得有效时长（支持 24 小时以内的视频）")
    result = {"duration": duration, "video_stream_index": None, "source": "container"}
    base = [media.ffprobe_bin(), "-v", "error", *media.probe_limit_args(src.input)]
    metadata = _json_probe(
        base + ["-show_entries",
                "stream=index,codec_type,codec_name,start_time,duration,avg_frame_rate:"
                "stream_disposition=attached_pic:stream_tags=DURATION,NUMBER_OF_FRAMES:format=start_time",
                "-of", "json", src.input], jid, run_probe, timeout=30)
    if not metadata:
        return result
    video, index = _video_stream(metadata.get("streams") or [])
    if video is None:
        return result
    result["video_stream_index"] = index
    origin = _number((metadata.get("format") or {}).get("start_time")) or 0.0
    resolved = _metadata_duration(video, origin, duration)
    if resolved:
        result.update(duration=resolved[0], source=resolved[1])
        return result

    start = origin + max(0.0, duration - _TAIL_SECONDS)
    packets = _json_probe(
        base + ["-select_streams", str(index), "-read_intervals", f"{start:.6f}%{origin + duration + 1:.6f}",
                "-show_entries", "packet=pts_time,duration_time", "-of", "json", src.input],
        jid, run_probe, timeout=30)
    if not packets:
        return result
    fps = media._frame_rate(video.get("avg_frame_rate")) or media._frame_rate(info.get("fps"))
    frame_duration = 1 / fps if fps else 0.0
    endpoints = []
    for packet in packets.get("packets") or []:
        if not isinstance(packet, dict):
            continue
        pts = _number(packet.get("pts_time"))
        if pts is None or pts < start or pts > origin + duration + 1:
            continue
        span = _number(packet.get("duration_time"))
        span = span if span is not None and 0 < span <= 10 else frame_duration
        endpoints.append(pts + span - origin)
    if endpoints and 0 < max(endpoints) <= duration + 1:
        result.update(duration=min(max(endpoints), duration), source="tail_packets")
    else:
        logger.info("preview video tail could not be confirmed; retaining container duration")
    return result
