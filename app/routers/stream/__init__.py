"""在线播放路由门面（评审 B9/R12-Q1：原 1571 行单文件拆 common/media/session/prewarm/subtitles）。"""
from . import common, media, prewarm, session, subtitles, previews  # noqa: F401
from .common import *
from .media import *
from .session import *
from .prewarm import *
from .subtitles import *

__all__ = []
__all__ += list(common.__all__)
__all__ += list(media.__all__)
__all__ += list(session.__all__)
__all__ += list(prewarm.__all__)
__all__ += list(subtitles.__all__)

from .previews import shutdown_previews as shutdown_previews
__all__ += list(previews.__all__)
