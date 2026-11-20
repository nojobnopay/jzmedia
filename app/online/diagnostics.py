"""Evidence-based decisions for the assistant. All network tools here are read-only."""

import re
from threading import BoundedSemaphore
import time

from ..ai.diagnostic_tools import choose_tools
from ..network.guard import NetworkOperation, NetworkOperationError, operation_context
from ..network.policy import NetworkPolicyError
from ..network.targets import TargetPolicyError
from ..store import online, online_network
from . import diagnostic_probes as probes
from . import health, maccms, open_catalog, operator_directory, source_migration, source_templates
from .providers import ProviderError


_SLOTS = BoundedSemaphore(4)
_SECONDS = 28
# Require two fresh failures plus independent positive evidence. Client access
# errors, 403s, rate limits, policy rejections and local budget errors are excluded.
_COMPARABLE = {"http_404", "http_410", "http_502", "http_503", "http_504", "network_timeout",
               "target_dns_failed", "target_dns_timeout", "invalid_provider_response", "network_request_failed"}
_ROUTE_ERRORS = {"proxy_connection_failed", "network_request_failed", "network_timeout", "target_dns_failed",
                 "target_dns_timeout", "http_403", "http_404", "http_429", "http_502", "http_503", "http_504"}
_EXPLANATIONS = {
    "invalid_provider_response": "响应没有通过 MacCMS JSON 格式与内容校验",
    "provider_upstream_error": "上游在 JSON 中返回业务错误",
    "network_timeout": "网络请求超时", "target_dns_timeout": "域名解析超时",
    "target_dns_failed": "域名解析失败", "network_request_failed": "连接未能完成",
    "proxy_connection_failed": "代理连接失败，请检查该来源使用的代理档案",
    "target_host_not_allowed": "所需主机尚未列入这个来源的允许列表，可在来源设置中检查当前影片的主机",
    "non_public_target_address": "目标地址未通过公网地址检查",
    "provider_not_found": "目录中找不到这个节目标识", "item_mismatch": "节目标识对应的片名或类型与原记录不符",
    "entry_missing": "详情中没有原线路或原分集", "entry_ambiguous": "需要选择具体线路和分集才能验证媒体",
    "unsupported_media": "当前线路未提供可直接检测的 MP4 或 HLS 地址",
    "invalid_media_sample": "响应没有提供预期的媒体字节", "no_media_sample": "清单中没有可检测的媒体段",
    "provider_response_limit": "目录响应超过读取上限", "response_body_limit": "响应超过读取上限",
    "response_byte_limit": "资源超出大小限制", "invalid_manifest_response": "响应未通过 HLS 清单检查",
    "upstream_ignored_range": "媒体服务器未按请求返回字节范围",
}


def _reason(code):
    if re.fullmatch(r"http_[1-5][0-9]{2}", code):
        extra = {"http_403": "，服务器拒绝访问", "http_401": "，需要身份验证", "http_429": "，请求受到限流"}
        return f"上游返回 HTTP {code[5:]}{extra.get(code, '')}"
    return _EXPLANATIONS.get(code, "响应未通过此项检测")


def _record(rows, tool, label, outcome, detail, **metadata):
    rows.append({"tool": tool, "label": label, "outcome": outcome, "detail": detail,
                 "checked_at": int(time.time()), **metadata})


def _result(rows, status, answer, **extra):
    return {"status": status, "answer": answer, "candidate_url": None, "evidence": rows, **extra}


def _history(state, rows):
    states = {"success": "最近一次读取成功", "failure": "最近一次读取失败", "stale": "上一次记录已过期"}
    for purpose, label in (("api", "目录"), ("media", "媒体")):
        record = health.registry.summary(state, purpose)
        outcome = {"success": "ok", "failure": "failed"}.get(record["state"], "unknown")
        detail = states.get(record["state"], "当前配置尚无可用记录")
        if record.get("age_seconds") is not None:
            detail += f"（{int(record['age_seconds'])} 秒前）"
        if record["state"] == "failure":
            detail += "，" + _reason(record["category"] or "")
        _record(rows, "source_history", f"{label}读取记录", outcome, detail)


def _item(source_id, context):
    episode = item = None
    item_id = context.get("item_id")
    if context.get("episode_id"):
        episode = online.get_online_episode(context["episode_id"])
        if not episode or item_id and item_id != episode["item_id"]:
            raise probes.ProbeFailure("entry_missing")
        item_id = episode["item_id"]
    if item_id:
        item = online.get_online_item(item_id)
        if not item or item["source_id"] != source_id:
            raise probes.ProbeFailure("item_mismatch")
    external_id = context.get("external_id") or (item or {}).get("external_id")
    if not external_id:
        if context.get("line_id"):
            raise probes.ProbeFailure("entry_missing")
        return None
    try:
        external_id = maccms.external_id(external_id)
    except ProviderError:
        raise probes.ProbeFailure("item_mismatch") from None
    if item and item["external_id"] != external_id:
        raise probes.ProbeFailure("item_mismatch")
    return {**(item or {}), "external_id": external_id, "episode_key": (episode or {}).get("external_id"),
            "line_id": context.get("line_id")}


def _catalogue(source, url, api, rows, tool, *, candidate=False):
    route = "代理" if api.mode == "proxy" else "直连"
    label = {"current_catalogue": "当前目录", "retry_catalogue": "当前目录复查", "candidate_catalogue": "候选目录"}[tool]
    try:
        count = probes.catalogue(source, url, api, candidate=candidate)
    except probes.ProbeFailure as exc:
        _record(rows, tool, label, "failed", f"{route}出口，{_reason(exc.code)}")
        return exc.code
    _record(rows, tool, label, "ok" if count else "unknown", f"{route}出口，目录首页返回 {count} 条有效节目记录")
    return "ok" if count else "empty"


def _inspect(source, item, api, media, rows, *, candidate_url=None):
    label = "候选" if candidate_url else "当前"
    try:
        detail = probes.item_detail(source, candidate_url or source["api_base_url"], api, item, candidate=bool(candidate_url))
    except probes.ProbeFailure as exc:
        _record(rows, "inspect_item", f"{label}节目详情", "failed", _reason(exc.code))
        return None, exc.code
    _record(rows, "inspect_item", f"{label}节目详情", "ok", "已读取并核对所选节目")
    try:
        entry = probes.selected_entry(detail, item["line_id"], item["episode_key"])
    except probes.ProbeFailure as exc:
        _record(rows, "inspect_item", f"{label}播放线路", "unknown" if exc.code == "entry_ambiguous" else "failed",
                _reason(exc.code))
        return None, exc.code
    result = probes.media(source, entry, media)
    _media_record(rows, result, media.mode)
    return (entry, result), result.code


def _media_record(rows, result, mode):
    route = "直连" if mode == "direct" else "代理"
    name = "媒体清单" if result.stage == "manifest" else "媒体字节"
    _record(rows, "probe_media", f"{route}{name}", "ok" if result.code == "ok" else "failed",
            "已从该线路取得媒体字节样本" if result.code == "ok" else _reason(result.code))


def _playback(source, item, state, api, rows, context, verify_saved):
    media = probes.snapshot(state, "media")
    checked, code = _inspect(source, item, api, media, rows)
    if code == "ok":
        if context.get("error_code") == "online_decode_unsupported":
            return _result(rows, "device_problem", "服务器已读到原线路的媒体样本。播放器报告设备解码失败，可以在另一设备上重试这一集。")
        return _result(rows, "healthy", "服务器已读到当前目录、所选节目和媒体样本。请在原设备重试播放以验证首帧。")
    if checked is None:
        return _result(rows, "inconclusive" if code == "entry_ambiguous" else "media_problem",
                       f"当前目录可用。{_reason(code)}。")
    entry, original = checked
    answer = f"当前目录及节目详情可用。{'媒体清单' if original.stage == 'manifest' else '媒体字节'}检测失败，{_reason(code)}。"
    if verify_saved or media.mode != "proxy" or code not in _ROUTE_ERRORS:
        return _result(rows, "media_problem", answer)
    if context.get("compare_direct") is not True:
        return _result(rows, "media_problem", answer + "可以临时从直连出口检查同一线路。", can_compare_direct=True)
    policy = {**state["source"]["policy"], "media": {"mode": "direct", "profile_id": None}}
    direct_state = {**state, "source": {**state["source"], "policy": policy}}
    direct = probes.media(source, entry, probes.snapshot(direct_state, "media"))
    _media_record(rows, direct, "direct")
    if direct.code == "ok" and original.trail and direct.trail[:len(original.trail)] == original.trail:
        return _result(rows, "media_route_available", answer + "临时直连检查了同一地址并读到了媒体样本。可以将这个来源的视频播放改为直连，需要我帮你修改吗？")
    return _result(rows, "media_problem", answer + "直连比对没有取得同一地址可用的证据。")


def _template(source):
    template = operator_directory.recognize_template(source)
    if template:
        return template
    operator = online_network.verified_source_operator(source["id"], source["api_base_url"])
    return next((value for value in source_templates.list_source_templates()["items"]
                 if value["provider"] == "maccms" and value["id"] == operator), None)


def _directory(template, api, rows, deadline):
    probes.check_access()
    discovery = operator_directory.lookup_candidates(template, api, deadline=deadline)
    live = discovery["origin"] == "operator_page"
    label = "运营方公告" if live else "随版本收录的候选"
    if live:
        detail = f"已从「{discovery['section']}」读取候选接口"
    else:
        reason = "未能识别公告中的接口栏目" if discovery["reason"] == "page_layout_changed" else "运营方公告暂无法读取"
        detail = f"{reason}，使用随版本收录的接口"
    _record(rows, "operator_directory", label, "ok" if live else "unknown", detail,
            page_url=discovery["page_url"], checked_at=discovery["checked_at"])
    return discovery["candidates"]


def _decide(source, context, state, rows, operation, verify_saved):
    error_code = context.get("error_code") or ""
    if context.get("cache_only") or context.get("cached") or error_code.startswith("online_cache_"):
        detail = {"online_cache_corrupt": "播放器报告本地缓存文件缺失或损坏",
                  "online_cache_not_ready": "播放器报告缓存尚未就绪或已经删除"}.get(error_code, "这次故障涉及本地缓存")
        _record(rows, "playback_context", "本地缓存", "unknown", detail)
        return _result(rows, "inconclusive", detail + "。请在本地缓存页面检查这个节目，或从在线详情重新播放并诊断。")
    if not state["online_enabled"] or not source["enabled"]:
        return _result(rows, "disabled", "在线资源功能或当前来源已停用，可在在线资源设置中查看。")
    item = _item(source["id"], context)
    if source["provider"] == "open_catalog":
        try:
            open_catalog.search(source, "", 1)
        except ProviderError:
            return _result(rows, "inconclusive", "此来源的地址与内置开放影片目录不匹配，请检查配置。")
        _record(rows, "local_catalogue", "内置目录", "ok", "已从内置目录读取开放影片")
        if item:
            return _playback(source, item, state, None, rows, context, verify_saved)
        _history(state, rows)
        return _result(rows, "healthy", "内置目录可读。请从开放影片的详情或播放器打开小映，检查实际媒体线路。")
    api = probes.snapshot(state, "api")
    url = source["api_base_url"]
    code = _catalogue(source, url, api, rows, "current_catalogue")
    if code in _COMPARABLE and not verify_saved:
        code = _catalogue(source, url, api, rows, "retry_catalogue")
    template = None if verify_saved else _template(source)
    options = {"source_history": "查阅此来源近期目录和媒体读取记录"}
    defaults = list(options)
    if item and code == "ok":
        options["inspect_item"] = "读取指定节目详情和播放样本"
        defaults.insert(0, "inspect_item")
    if template:
        options["operator_directory"] = "查阅已核实运营方的接口公告"
        if code in _COMPARABLE:
            defaults.insert(0, "operator_directory")
    if verify_saved:
        selected = ["inspect_item"] if item and code == "ok" else []
    else:
        # The model sees a symptom vocabulary, tool names and coarse outcomes.
        # It cannot supply a URL or authority, or bypass an evidence gate.
        question = str(context.get("question") or "").lower()
        vocabulary = ("解码", "格式", "代理", "网络", "超时", "分集", "这集", "线路", "媒体", "分片", "目录", "接口", "缓存")
        symptoms = " ".join(word for word in vocabulary if word in question)
        if item:
            symptoms += " 分集"
        if context.get("error_code") == "online_decode_unsupported":
            symptoms += " 解码"
        evidence = [{k: row[k] for k in ("tool", "outcome")} for row in rows]
        selected = defaults
        if context.get("allow_model") is not False and operation.remaining() > 5:
            probes.check_access()
            selected = choose_tools(symptoms, evidence, options, defaults)
    playback, candidates = None, []
    for tool in selected:
        operation.check()
        if tool == "source_history":
            _history(state, rows)
        elif tool == "inspect_item" and item and code == "ok":
            playback = _playback(source, item, state, api, rows, context, verify_saved)
        elif tool == "operator_directory" and template:
            candidates = _directory(template, api, rows, operation.deadline)
    if playback:
        return playback
    if code == "ok":
        if item:
            return _playback(source, item, state, api, rows, context, verify_saved)
        message = "当前目录已返回有效节目。请从出问题的影片或分集打开小映，以检查原播放线路。"
        if context.get("error_code") == "online_decode_unsupported":
            message = "当前目录可用。播放器报告设备解码失败，可从原节目打开小映继续检查服务器取流。"
        return _result(rows, "healthy", message)
    if code == "empty":
        return _result(rows, "inconclusive", "接口返回了有效但为空的目录，目前无法验证可用节目。")
    if code not in _COMPARABLE or verify_saved:
        return _result(rows, "inconclusive", f"当前目录检测失败，{_reason(code)}。")
    if template and "operator_directory" not in selected:
        candidates = _directory(template, api, rows, operation.deadline)
    addresses = dict.fromkeys(row["url"] for row in candidates
                              if row["url"].rstrip("/") != source["api_base_url"].rstrip("/"))
    candidate_item_failure = None
    for url in list(addresses)[:2]:
        operation.check()
        if _catalogue(source, url, api, rows, "candidate_catalogue", candidate=True) != "ok":
            continue
        scope = "候选目录已返回有效节目"
        if item:
            saved_hosts = source_migration.hosts_for_update(source, {"api_base_url": url}, None)
            retained = [host for host in source["allowed_hosts"] if host in saved_hosts]
            if not retained:
                _record(rows, "inspect_item", "候选播放线路", "unknown", "更换后没有已授权且保留的媒体主机可供比对")
                candidate_item_failure = "target_host_not_allowed"
                continue
            media_source = {**source, "allowed_hosts": retained}
            _, matched = _inspect(media_source, item, api, probes.snapshot(state, "media"), rows, candidate_url=url)
            if matched == "entry_ambiguous" and not (item["line_id"] or item["episode_key"]):
                scope = "候选目录已核对原节目。还需在原设备选择线路和分集，才能验证播放"
            elif matched != "ok":
                candidate_item_failure = matched
                continue
            else:
                scope = "已在候选接口核对原节目，并从所选线路取得媒体样本"
        return _result(rows, "update_available", f"当前接口在本机出口连续两次未通过检测，{_reason(code)}。{scope}。需要我帮你更换接口吗？",
                       candidate_url=url, operator_id=template["id"])
    if candidate_item_failure:
        return _result(rows, "inconclusive", f"当前目录接连失败。候选目录可读，但核对原节目或线路时发现，{_reason(candidate_item_failure)}。")
    return _result(rows, "inconclusive", f"当前目录连续两次未通过检测，{_reason(code)}。尚无经过本机验证、适合替换的同运营方接口。")


def diagnose(source: dict, context: dict, *, verify_saved=False, guard=None, operation=None) -> dict:
    rows = []
    if not _SLOTS.acquire(blocking=False):
        return _result(rows, "inconclusive", "当前诊断名额已满，请稍后重试。")
    owns_operation = operation is None
    operation = operation or NetworkOperation(time.monotonic() + _SECONDS)
    fingerprint = None
    try:
        with operation_context(operation), probes.guarded_reads(guard):
            state = online_network.load_online_network_state(source["id"])
            fingerprint = online_network.online_network_fingerprint(state)
            if state["source_config"] != source:
                result = _result(rows, "inconclusive", "来源配置刚刚改变，请重新诊断。")
            else:
                result = _decide(source, context, state, rows, operation, verify_saved)
            operation.check()
    except online.OnlineNotFound:
        result = _result(rows, "inconclusive", "来源已删除，请重新选择来源。")
    except (NetworkPolicyError, TargetPolicyError, online_network.NetworkConflict):
        result = _result(rows, "inconclusive", "当前来源的网络配置未通过检查，请查看它使用的网络档案。")
    except NetworkOperationError:
        result = _result(rows, "inconclusive", "本轮诊断的时间已用完，以上为已取得的证据。")
    except probes.ProbeFailure as exc:
        result = _result(rows, "inconclusive", _reason(exc.code) + "。")
    finally:
        if owns_operation:
            operation.close()
        _SLOTS.release()
    return {**result, "network_fingerprint": fingerprint}
