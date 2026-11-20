"""Reviewed source suggestions, listed locally without importing or probing them.

Community interfaces are third-party suggestions, not bundled content or a
promise of playback rights, regional reachability, or continuing availability.
Selecting one still goes through the normal explicit administrator save.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from . import open_catalog
from .models import OnlineProviderOptions, Provider


_BAOFENG_APIS = ("https://apibfzy.com/api.php/provide/vod/", "https://bfzyapi.com/api.php/provide/vod/")
_LIANGZI_APIS = ("https://lzzyapi.com/api.php/provide/vod/", "https://cj.lziapi.com/api.php/provide/vod/")
_FEIFAN_API = "http://api.ffzyapi.com/api.php/provide/vod/"
_DEFAULT_HTTPS_PORT = re.compile(r"^(https://[a-z0-9.-]+):443/")

# Only the operator's reviewed HTTPS JSON endpoints share its CDN notices.
_REVIEWED_MIGRATIONS = (
    (_BAOFENG_APIS, "https://bfzy.tv/index.php/macrep.html", (
        ("p.bfvp26.com", "p.bfp261112.com", "image"),
    )),
    (_LIANGZI_APIS, "https://lzizy.net/index.php/macrep.html", (
        ("v.lzcdn28.com", "v.cdnlz28.com", "media"),
        ("p.lztutu.com", "p.lzp261118.com", "image"),
    )),
)


def reviewed_host_replacements(old_url: str, new_url: str) -> list[dict]:
    """List local operator notices applicable to a pair of saved/draft API URLs."""
    requested = {_DEFAULT_HTTPS_PORT.sub(r"\1/", url).removesuffix("/") for url in (old_url, new_url)}
    for urls, notice_url, replacements in _REVIEWED_MIGRATIONS:
        if requested <= {url.removesuffix("/") for url in urls}:
            return [{"old_host": old_host, "new_host": new_host, "kind": kind, "notice_url": notice_url}
                    for old_host, new_host, kind in replacements]
    return []


class SourceTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    id: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    name: str
    provider: Provider
    api_base_url: str
    known_api_urls: list[str]
    allowed_hosts: list[str]
    provider_options: OnlineProviderOptions = Field(default_factory=OnlineProviderOptions)
    description: str
    homepage: str
    network_note: str
    status: Literal["unverified", "api_observed"]


class SourceTemplateList(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    items: list[SourceTemplate]


# Exact media/image hosts come from bounded catalogue reads and published
# operator notices. TargetPolicy still verifies DNS and the actual socket peer.
_TEMPLATES = (
    SourceTemplate(
        id="blender-open-example", name="开放样例目录", provider="open_catalog",
        api_base_url=open_catalog.API_BASE_URL, known_api_urls=[open_catalog.API_BASE_URL],
        allowed_hosts=["www.bigbuckbunny.org", open_catalog.MEDIA_HOST],
        description="Blender Foundation 的 Big Buck Bunny 开放影片，适合验证播放；不包含主流影视片库。",
        homepage="https://peach.blender.org/about/",
        network_note="目录在本地；影片来自 Blender 官方站点，需验证服务器的媒体访问速度。",
        status="unverified",
    ),
    SourceTemplate(
        id="baofeng", name="暴风资源", provider="maccms",
        api_base_url=_BAOFENG_APIS[0], known_api_urls=list(_BAOFENG_APIS),
        allowed_hosts=["apibfzy.com", "c1.rrcdnbf5.com", "c1.rrcdnbf6.com",
                       "p.bvvvvvvv7f.com", "p.bvvvvvvvvv1f.com", "s2.bfllvip.com", "s3.bfllvip.com",
                       "v.fengbao8.com", "fengbao13.com", "v.fengbao14.com",
                       "img.bfzypic.com", "p.bfp261112.com"],
        description="第三方影视目录，提供电影、剧集等 MacCMS JSON 数据；由站点运营者维护。",
        homepage="https://bfzy.tv/index.php/help.html",
        network_note="2026-11-20 直连主接口读到首页 20 项。抽查两条播放地址，一条读到视频分片，另一条清单返回 404。整片播放仍需验证。",
        status="api_observed",
    ),
    SourceTemplate(
        id="liangzi", name="量子资源", provider="maccms",
        api_base_url=_LIANGZI_APIS[0], known_api_urls=list(_LIANGZI_APIS),
        allowed_hosts=["lzzyapi.com", "lzv34.com", "v.cdnlz28.com", "p.lzp261118.com"],
        description="第三方影视目录，主接口和备用接口由运营方帮助中心公布。",
        homepage="https://lzizy.net/index.php/help.html",
        network_note="2026-11-20 直连主接口读到首页 20 项，并读取了两条 lzm3u8 样本的清单和少量分片。使用新主接口时请选择 lzm3u8，实际播放仍需验证。",
        status="api_observed",
    ),
    SourceTemplate(
        id="feifan", name="非凡资源", provider="maccms",
        api_base_url=_FEIFAN_API, known_api_urls=[_FEIFAN_API],
        allowed_hosts=["api.ffzyapi.com"],
        description="第三方影视目录，使用站点帮助页公布的 HTTP JSON 接口；可自行改为确认可达的 HTTPS 地址。",
        homepage="https://ffzy.tv/help/",
        network_note="2026-11-20 开发机直连 HTTP 目录未通过。请在运营方帮助中心查找“app json接口”，并在运行服务的网络中测试。",
        status="unverified",
    ),
)


def list_source_templates() -> dict:
    """Return independent configuration suggestions; perform no DB or I/O work."""
    return {"items": [item.model_dump(mode="json") for item in _TEMPLATES]}
