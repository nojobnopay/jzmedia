"""Reviewed source suggestions, listed locally without importing or probing them.

Community interfaces are third-party suggestions, not bundled content or a
promise of playback rights, regional reachability, or continuing availability.
Selecting one still goes through the normal explicit administrator save.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from . import open_catalog
from .models import OnlineProviderOptions, Provider


class SourceTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    id: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    name: str
    provider: Provider
    api_base_url: str
    allowed_hosts: list[str]
    provider_options: OnlineProviderOptions = Field(default_factory=OnlineProviderOptions)
    description: str
    homepage: str
    network_note: str
    status: Literal["unverified", "api_observed"]


class SourceTemplateList(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    items: list[SourceTemplate]


# Exact media/image hosts below were observed in a bounded JSON response on
# 2026-10-29. No wildcard, script/plugin, HTML extractor or arbitrary discovered
# host is trusted. TargetPolicy still verifies DNS and the actual socket peer.
_TEMPLATES = (
    SourceTemplate(
        id="blender-open-example", name="开放样例目录", provider="open_catalog",
        api_base_url=open_catalog.API_BASE_URL,
        allowed_hosts=["www.bigbuckbunny.org", open_catalog.MEDIA_HOST],
        description="Blender Foundation 的 Big Buck Bunny 开放影片，适合验证播放；不包含主流影视片库。",
        homepage="https://peach.blender.org/about/",
        network_note="目录在本地；影片来自 Blender 官方站点，需验证服务器的媒体访问速度。",
        status="unverified",
    ),
    SourceTemplate(
        id="baofeng", name="暴风资源", provider="maccms",
        api_base_url="https://bfzyapi.com/api.php/provide/vod/",
        allowed_hosts=["bfzyapi.com", "c1.rrcdnbf5.com", "c1.rrcdnbf6.com",
                       "p.bvvvvvvv7f.com", "p.bvvvvvvvvv1f.com", "s2.bfllvip.com",
                       "img.bfzypic.com", "p.bfvp26.com"],
        description="第三方影视目录，提供电影、剧集等 MacCMS JSON 数据；由站点运营者维护。",
        homepage="https://bfzy.tv/helps/",
        network_note="2026-10-29 开发机 API 小量直连读取通过；媒体速度与完整播放尚未验证。不同影片可能需要补充其精确媒体主机。",
        status="api_observed",
    ),
    SourceTemplate(
        id="liangzi", name="量子资源", provider="maccms",
        api_base_url="https://cj.lziapi.com/api.php/provide/vod/",
        allowed_hosts=["cj.lziapi.com"],
        description="第三方影视目录，接口地址取自站点帮助页；可作为候选，需在当前服务器验证。",
        homepage="https://lzizy.net/index.php/help.html",
        network_note="2026-10-29 开发机直连未通过；尚未核实媒体主机。可在来源网络中选择代理后验证，播放前需补充实际返回的精确媒体主机。",
        status="unverified",
    ),
    SourceTemplate(
        id="feifan", name="非凡资源", provider="maccms",
        api_base_url="http://api.ffzyapi.com/api.php/provide/vod/",
        allowed_hosts=["api.ffzyapi.com"],
        description="第三方影视目录，使用站点帮助页公布的 HTTP JSON 接口；可自行改为确认可达的 HTTPS 地址。",
        homepage="https://ffzy.tv/help/",
        network_note="2026-10-29 开发机 HTTP/HTTPS 读取均未通过；尚未核实媒体主机，需验证出口和实际返回的精确媒体主机。",
        status="unverified",
    ),
)


def list_source_templates() -> dict:
    """Return independent configuration suggestions; perform no DB or I/O work."""
    return {"items": [item.model_dump(mode="json") for item in _TEMPLATES]}
