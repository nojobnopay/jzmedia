"""Explicitly enabled Blender open movie example; no remote catalogue import."""

from .maccms import Detail, Entry, Line
from .providers import ProviderError


API_BASE_URL = "https://www.bigbuckbunny.org/"
# The checked official original file is assigned here, never from API input.
MEDIA_URL = "https://video.blender.org/object-storage/web_videos/bf1f3fb5-b119-4f9f-9930-8e20e892b898-360.mp4"
MEDIA_HOST = "video.blender.org"
EXTERNAL_ID = "big-buck-bunny"
TITLE = "Big Buck Bunny"
DESCRIPTION = "Blender Foundation 开放电影（2008），官方发布的原版影片。保留原片尾署名；原项目使用 CC BY 3.0 许可。"
ATTRIBUTION = (("Blender Foundation · Big Buck Bunny 原项目", "https://peach.blender.org/"),
               ("原项目许可说明", "https://peach.blender.org/about/"),
               ("Creative Commons Attribution 3.0", "https://creativecommons.org/licenses/by/3.0/"),
               ("Blender 官方影片发布页", "https://video.blender.org/videos/watch/bf1f3fb5-b119-4f9f-9930-8e20e892b898"))


def _ready(source):
    # Old metadata-only configurations may use another URL. They cannot turn
    # this constant catalogue into a general HTTP or remote JSON reader.
    if source.get("api_base_url") != API_BASE_URL:
        raise ProviderError("provider_not_ready")


def search(source, query, page):
    _ready(source)
    matches = not query or query.casefold() in (TITLE + " 大雄兔 兔子 Blender 开放样例").casefold()
    items = [{"external_id": EXTERNAL_ID, "title": TITLE, "year": "2008", "kind": "movie",
              "description": DESCRIPTION}] if matches and page == 1 else []
    return {"items": items, "page": page, "page_count": 1 if matches else 0, "total": 1 if matches else 0}


def detail(source, selected_id):
    _ready(source)
    if selected_id != EXTERNAL_ID:
        raise ProviderError("provider_not_found")
    supported = MEDIA_HOST in source["allowed_hosts"]
    entry = Entry("完整影片", MEDIA_URL if supported else "", "mp4" if supported else None,
                  "" if supported else "target_host_not_allowed")
    return Detail(EXTERNAL_ID, TITLE, "movie", DESCRIPTION, "2008",
                  (Line("Blender 官方原片", (entry,)),), ATTRIBUTION, 596.0)
