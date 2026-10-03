"""Shared completion rule for progress lists and episode continuation."""
import math


def is_playback_complete(position, duration) -> bool:
    """At least 95%, or at least 80% with no more than five minutes left."""
    try:
        pos, dur = float(position), float(duration)
    except (TypeError, ValueError, OverflowError):
        return False
    if not math.isfinite(pos) or not math.isfinite(dur) or pos < 0 or dur <= 0:
        return False
    ratio = pos / dur
    return ratio >= 0.95 or (ratio >= 0.80 and dur - pos <= 300)
