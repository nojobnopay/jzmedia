"""Recognize local requests to prepare an existing online source edit."""

import ipaddress
import re
from urllib.parse import unquote, urlsplit

from ..network.targets import public_address
from ..online.models import validate_api_base_url
from .assistant_common import contains_private_text


_OPERATORS = {"量子": "liangzi", "暴风": "baofeng", "非凡": "feifan"}
_SOURCES = re.compile(r"视频源|片源|点播源|采集源|自定义源|在线(?:来源|资源|源)|(?:来源|资源)\s*(?:接口|地址|api)|MacCMS", re.I)
_ENDPOINT = re.compile(r"接口|域名|地址|换址|(?<![a-z])api(?![a-z])", re.I)
_CHANGE = re.compile(r"更新|更换|替换|修改|调整|迁移|切换|换址|(?:改|换|设)(?:成|为|到|用|一下|一个|一[改换]|(?=接口|域名|地址|源))")
_REQUEST_START = re.compile(r"^(?:小映[，,、\s]*)?(?:请|麻烦|劳驾|我想|我要|我需要|帮我|帮忙|把|将|给|更新|更换|替换|修改|调整|迁移|切换|换址)")
_INSTRUCTION = re.compile(r"帮我|帮忙|替我|(?:改|换|设)(?:成|为|到|用|一下)|一下(?:接口|地址)")
_GUIDE = re.compile(r"怎么|怎样|如何|为什么|哪里|在哪|是什么|是多少|是否|教程|文档|步骤|举例|比如|例如|示例|如果|假如|假设|会不会|要不要|想(?:问|知道|了解)|只(?:问|告诉我)|解释|说明一下")
_HOLD = re.compile(r"不(?:要|必|用|想|需要|是接口)|别(?:再|给|帮|把|改|换|动|更新|替换|修改)|暂缓|取消|先不|勿|停一下|还没决定")
_MEDIA_HOST = re.compile(r"(?:播放|媒体|图片|封面|海报|下载|代理)(?:的)?(?:域名|地址|主机|接口)|CDN", re.I)
_URL = re.compile(r"""[a-z][a-z\d+.\-]*://[^\s<>"'`，。；、！？（）「」“”]+""", re.I)
_MALFORMED_ADDRESS = re.compile(r"(?<![a-z\d-])(?:(?:https?|ftp|smb|file)\s*[:：/]|[a-z\d-]+(?:\.[a-z\d-]+)+)", re.I)

_CHOOSE = "请在卡片中选择要更新的视频源，核对新接口和域名替换，再确认保存。"
_CLARIFY = "请指明要更新的一个视频源。可以说“帮我更新量子资源接口”，或提供一个新的公网 HTTP 或 HTTPS 目录地址。"
_PRIVATE_ADDRESS = "请提供公网 HTTP 或 HTTPS 的 JSON 采集目录地址，省略账号、密码、查询参数和片段。也可直接说“帮我更新量子资源接口”。"

_FAILURE = re.compile(r"播(?:放)?不(?:了|出|动|开)|无法(?:播放|观看|打开|加载|连接)|不能(?:播放|看)|放不了|看不了|打不开|加载失败|播放失败|播放出错|起播失败|失效|卡(?:住|顿)|黑屏|一直(?:加载|转圈)|连不上|超时|没(?:有)?声音|报错|\b(?:403|404|429|502|503)\b", re.I)


def diagnosis_intent(question, *, online_context):
    """Only start a read when the user reports an online failure; retain operator hints locally."""
    if not _FAILURE.search(question) or _HOLD.search(question) or re.search(r"假如|假设|举例|如果|不要诊断|别查", question):
        return None
    operators = [key for name, key in _OPERATORS.items() if name in question]
    if not (online_context or operators or _SOURCES.search(question) or "在线" in question):
        return None
    return {"diagnose_online": True, "diagnosis_template_id": operators[0] if len(operators) == 1 else None}


def _public_endpoint(url):
    if url.count("://") != 1:
        raise ValueError("multiple addresses")
    normalized = validate_api_base_url(url)
    parts = urlsplit(normalized)
    host = parts.hostname
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or host.endswith((".internal", ".lan", ".home.arpa")):
            raise ValueError("local host")
    else:
        public_address(host)
    path = unquote(parts.path)
    if re.search(r"\.(?:html?|m3u8|mp4|mkv|jpe?g|png|webp|xml)/?$|/at/xml/?$", path, re.I):
        raise ValueError("expected a JSON catalogue endpoint")
    # Token-bearing paths must also stay in the regular management form.
    if re.search(r"/(?:api[-_]?key|token|bearer|password|secret)/", path, re.I) or any(
            contains_private_text(segment, ()) for segment in path.split("/")):
        raise ValueError("private path")
    return normalized


def online_source_request(question, history=(), *, online_section=False):
    """Return (reply, inert proposal or None), or None for an unrelated question.

    Addresses are only read from the current turn. This function does not fetch
    an endpoint, read saved configuration, or consult a language model.
    """
    urls = _URL.findall(question)
    text = _URL.sub(" ", question).strip()
    operators = [name for name in _OPERATORS if name in text]
    endpoint = bool(_ENDPOINT.search(text))
    source = bool(_SOURCES.search(text)) or any(name + "源" in text or name + "资源" in text for name in operators)
    if not (source or (operators and (endpoint or urls)) or (online_section and endpoint)):
        return None
    if not (source or operators) and re.search(r"模型|智能辅助|代理|TMDB|AI[^a-z]", text, re.I):
        return None
    if _GUIDE.search(text):
        return None
    if _HOLD.search(text):
        return "好的。需要核对视频源时，可以打开下方的换址教程。", None
    change = _CHANGE.search(text)
    if not change:
        return None
    if not (_REQUEST_START.search(text) or _INSTRUCTION.search(text)):
        return None
    if text.endswith(("吗", "吗？", "吗?", "？", "?")) and not re.search(r"帮我|帮忙|替我|麻烦|请", text[:change.start()]):
        return None
    if contains_private_text(text, history) or _MALFORMED_ADDRESS.search(text):
        return _PRIVATE_ADDRESS, None
    if _MEDIA_HOST.search(text) and not re.search(r"接口", _MEDIA_HOST.sub("", text)):
        return "更换播放或海报域名时，请在视频源设置中编辑允许访问的主机。", None
    if not endpoint and not urls and not re.search(r"更新|更换|替换|换址|换成最新|换到最新", text):
        return None
    if not endpoint and re.search(r"海报|封面|播放|缓存|名字|名称|排序|代理|密码|清单|目录|剧集|电影|影片", text):
        return None
    if len(operators) > 1 or len(urls) > 1 or re.search(r"全部|所有|批量|同时|一起|多个", text):
        return _CLARIFY, None
    url = None
    if not urls and re.search(r"备用|旧接口|原接口|备选|回退|回滚|自定义", text):
        return "请给出要换用的完整目录地址，以便预览这次换址。", None
    if urls:
        prefix, _, suffix = question.partition(urls[0])
        if re.search(r"(?:从|原接口|旧接口|旧地址|旧域名|原地址)(?:是|为|[：:\s])*$", prefix) or _CHANGE.search(suffix):
            return _CLARIFY, None
        try:
            value = urls[0].removesuffix(")") if prefix.endswith("(") else urls[0]
            url = _public_endpoint(value)
        except ValueError:
            return _PRIVATE_ADDRESS, None
    return _CHOOSE, {"kind": "online_source", "template_id": _OPERATORS[operators[0]] if operators else None, "api_base_url": url}
