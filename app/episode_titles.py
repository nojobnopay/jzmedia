"""Derived file-level episode titles; upstream titles remain unchanged."""
import re


_RANGE_SEPARATOR = r"[-–—~～至]"
_NUMBERED_TITLES = (
    re.compile(rf"第\s*([0-9]+)(?:\s*{_RANGE_SEPARATOR}\s*(?:第\s*)?([0-9]+))?\s*[集话話]"),
    re.compile(rf"episodes?\s*([0-9]+)(?:\s*{_RANGE_SEPARATOR}\s*(?:episodes?\s*)?([0-9]+))?", re.I),
)


def numbered_episode_title(title: str) -> tuple[int, int | None] | None:
    """Parse only whole generic numbered titles, never numbers in story titles."""
    for pattern in _NUMBERED_TITLES:
        match = pattern.fullmatch(str(title or "").strip())
        if match:
            start = int(match[1])
            return start, int(match[2]) if match[2] is not None else None
    return None


def episode_display_title(title: str | None, episode, episode_end=0) -> str:
    """Use the file's range for generic titles, preserving single/story titles."""
    original = str(title or "")
    try:
        start, end = int(episode or 0), int(episode_end or 0)
    except (TypeError, ValueError, OverflowError):
        return original
    if not 0 < start < end <= 9999:
        return original
    if not original.strip() or numbered_episode_title(original) is not None:
        return f"第{start}-{end}集"
    return original
