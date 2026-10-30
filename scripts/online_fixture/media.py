"""Fixed synthetic media, byte ranges and temporary-path guards for online tests."""

from pathlib import Path
import re
import subprocess

from scripts.preview_onboarding import installed_binary


DURATION = 24
EPISODES = (("ep-1", "第 1 集 · 蓝色测试片"), ("ep-2", "第 2 集 · 绿色测试片"))
MEDIA_MARKER = ".online-media-fixture"
MEDIA_MARKER_TEXT = "jzmedia online synthetic media v1\n"
ROOT_PREFIXES = ("jzmedia-online-player-", "jzmedia-online-catalog-", "jzmedia-online-tv-",
                 "jzmedia-online-prototype-")


def guarded_path(value):
    """Reject symlinks before resolving so ancestor links cannot bypass a guard."""
    candidate = Path(value).absolute()
    if any(path.is_symlink() for path in (candidate, *candidate.parents)):
        raise ValueError("fixture paths must not contain symlinks")
    return candidate.resolve()


def synthetic_media_root(value):
    root = guarded_path(value)
    if not root.is_relative_to(Path("/tmp")) or not any(
            path.name.startswith(ROOT_PREFIXES) for path in (root, *root.parents)):
        raise ValueError("synthetic media must stay inside a named /tmp online fixture")
    return root


def validate_fixture_root(value, *, prefix, marker, marker_text, require_media=False):
    """Validate one fixture profile without accepting other profiles' roots."""
    root = guarded_path(value)
    if not root.is_relative_to(Path("/tmp")) or not root.name.startswith(prefix):
        raise ValueError(f"root must use /tmp/{prefix}*")
    if (root / marker).is_symlink() or (root / "media").is_symlink():
        raise ValueError("fixture marker and media root must not be symlinks")
    if require_media:
        marker_path = root / marker
        if not marker_path.is_file() or marker_path.read_text() != marker_text:
            raise ValueError("generate the marked fixture before starting its server")
        for episode, _title in EPISODES:
            for relative in ("movie.mp4", "hls/master.m3u8"):
                asset = root / "media" / episode / relative
                if not asset.is_file() or guarded_path(asset) != asset:
                    raise ValueError("synthetic fixture media is incomplete")
    return root


def generate_media(root: Path) -> None:
    """Prepare fixed synthetic media under a guarded temporary fixture root."""
    root = synthetic_media_root(root)
    root.mkdir(parents=True, exist_ok=True)
    marker = root / MEDIA_MARKER
    if marker.exists():
        if marker.is_symlink() or marker.read_text() != MEDIA_MARKER_TEXT:
            raise ValueError("unrecognized synthetic media marker")
        return
    if any(root.iterdir()):
        raise ValueError("fixture root must be empty or an existing synthetic media fixture")
    ffmpeg = str(installed_binary("ffmpeg"))
    font = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    for number, (episode, _title) in enumerate(EPISODES, 1):
        folder = root / episode
        variants = folder / "hls" / "variants"
        variants.mkdir(parents=True)
        color = "blue" if number == 1 else "green"
        filter_text = f"drawbox=x=0:y=0:w=iw:h=44:color={color}:t=fill"
        if font.exists():
            filter_text += (f",drawtext=fontfile={font}:text='SYNTHETIC EPISODE {number}'"
                            ":x=12:y=10:fontsize=22:fontcolor=white")
        subprocess.run([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=480x270:rate=12",
            "-f", "lavfi", "-i", f"sine=frequency={330 + number * 110}:sample_rate=48000",
            "-t", str(DURATION), "-vf", filter_text, "-c:v", "libx264", "-threads", "1",
            "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", "-g", "24",
            "-keyint_min", "24", "-sc_threshold", "0", "-c:a", "aac", "-b:a", "64k",
            "-movflags", "+faststart", str(folder / "movie.mp4"),
        ], check=True, timeout=60)
        subprocess.run([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", str(folder / "movie.mp4"), "-c", "copy", "-f", "hls", "-hls_time", "4",
            "-hls_playlist_type", "vod", "-hls_list_size", "0", "-hls_segment_type", "fmp4",
            "-hls_fmp4_init_filename", "../init.mp4", "-hls_segment_filename", "part-%03d.m4s",
            "low.m3u8",
        ], cwd=variants, check=True, timeout=30)
        (folder / "hls" / "master.m3u8").write_text(
            '#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=480x270,'
            'CODECS="avc1.42c015,mp4a.40.2"\nvariants/low.m3u8\n', encoding="utf-8")
    marker.write_text(MEDIA_MARKER_TEXT, encoding="utf-8")


def byte_range(value: str | None, size: int) -> tuple[int, int, int]:
    """Return inclusive bounds and status; reject unsupported multipart/invalid ranges."""
    if value is None:
        return 0, size - 1, 200
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
    if not match or not any(match.groups()) or size <= 0:
        raise ValueError("invalid range")
    first, last = match.groups()
    if not first:
        count = int(last)
        if count <= 0:
            raise ValueError("empty suffix")
        return max(0, size - count), size - 1, 206
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or start > end:
        raise ValueError("range outside file")
    return start, end, 206
