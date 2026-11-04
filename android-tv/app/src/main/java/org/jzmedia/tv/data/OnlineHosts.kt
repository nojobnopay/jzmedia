package org.jzmedia.tv.data

import java.io.IOException
import okhttp3.HttpUrl
import org.json.JSONArray
import org.json.JSONObject

private const val MAX_ONLINE_HOSTS = 128
private val HOST_LABEL = Regex("[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
private val NUMERIC_HOST_LABEL = Regex("(?:[0-9]+|0x[0-9a-f]+)")
private val HOST_REPORT_FIELDS = setOf("source_id", "external_id", "missing_hosts", "allowed_hosts",
    "rejected_count", "configuration_fingerprint")

/** A review of one directory item, never an upstream URL or a persistent grant. */
data class OnlineHostReport(
    val sourceId: String,
    val externalId: String,
    val missingHosts: List<String>,
    val allowedHosts: List<String>,
    val rejectedCount: Int,
    val configurationFingerprint: String,
) {
    init { checked() }

    internal fun checked() {
        checkedOnlineSource(sourceId)
        checkedOnlineCatalogExternal(externalId)
        checkedOnlineScope(configurationFingerprint)
        require(rejectedCount >= 0)
        checkedHostList(missingHosts, candidate = true)
        checkedHostList(allowedHosts, candidate = false)
        require(missingHosts.none { it in allowedHosts })
    }

    companion object {
        fun fromJson(value: JSONObject): OnlineHostReport {
            require(value.keys().asSequence().toSet() == HOST_REPORT_FIELDS)
            val rejected = value.opt("rejected_count")
            require(rejected is Int || rejected is Long)
            val count = (rejected as Number).toLong()
            require(count in 0L..Int.MAX_VALUE)
            return OnlineHostReport(value.requiredHostText("source_id"), value.requiredHostText("external_id"),
                value.hosts("missing_hosts", candidate = true), value.hosts("allowed_hosts", candidate = false),
                count.toInt(), value.requiredHostText("configuration_fingerprint"))
        }
    }
}

private fun JSONObject.requiredHostText(name: String): String = opt(name) as? String
    ?: throw IllegalArgumentException("Invalid online host text")

private fun JSONObject.hosts(name: String, candidate: Boolean): List<String> {
    val array = opt(name) as? JSONArray ?: throw IllegalArgumentException("Invalid online host list")
    require(array.length() <= MAX_ONLINE_HOSTS)
    return (0 until array.length()).map { index ->
        array.opt(index) as? String ?: throw IllegalArgumentException("Invalid online host")
    }.also { checkedHostList(it, candidate) }
}

private fun checkedHostList(hosts: List<String>, candidate: Boolean) {
    require(hosts.size <= MAX_ONLINE_HOSTS && hosts.distinct().size == hosts.size)
    hosts.forEach { checkedHost(it, candidate) }
}

/** Pure syntax checks: HttpUrl canonicalizes hosts without looking up DNS. */
private fun checkedHost(host: String, candidate: Boolean) {
    require(host.isNotEmpty() && host.length <= 253 && host.all { it in 'a'..'z' || it in '0'..'9' || it in ".:-" })
    val canonical = HttpUrl.Builder().scheme("https").host(host).build().host
    // OkHttp collapses mapped IPv6 to IPv4. Keep existing IPv6 spellings from
    // the server so that reviewing a new host preserves the original list.
    if (candidate || ':' !in host) require(canonical == host)
    if (':' in host) {
        require(host.all { it in '0'..'9' || it in 'a'..'f' || it == ':' })
        if (candidate) {
            // Reject obvious local/mapped/special forms without maintaining a
            // second address registry. The server decides which native global
            // addresses are public, and checks every DNS answer and socket.
            val prefix = host.substringBefore(':').toIntOrNull(16) ?: 0
            require(prefix in 0x2000..0x3fff)
        }
        return
    }
    val labels = host.split('.')
    require(labels.all { HOST_LABEL.matches(it) })
    val ipv4 = labels.takeIf { it.size == 4 && it.all { label -> label.all(Char::isDigit) } }
        ?.map { it.toInt().also { octet -> require(octet in 0..255) } }
    if (ipv4 != null) {
        require(labels.all { it == "0" || !it.startsWith('0') })
        if (candidate) {
            val (a, b) = ipv4
            require(a !in setOf(0, 10, 127) && a < 224)
            require(!(a == 100 && b in 64..127) && !(a == 169 && b == 254))
            require(!(a == 172 && b in 16..31) && !(a == 192 && b == 168))
        }
    } else {
        require(!labels.all { NUMERIC_HOST_LABEL.matches(it) })
        if (candidate) require(labels.size > 1)
    }
}

internal fun checkedHostApproval(report: OnlineHostReport, hosts: List<String>): List<String> {
    report.checked()
    val selected = hosts.toList()
    require(selected.isNotEmpty() && selected.size <= MAX_ONLINE_HOSTS && selected.distinct().size == selected.size)
    require(selected.all { it in report.missingHosts })
    return selected
}

/** Administration replies contain private configuration; validate and discard. */
internal fun checkedHostApprovalResponse(value: JSONObject, report: OnlineHostReport, selected: List<String>) {
    require(value.requiredHostText("id") == report.sourceId)
    val allowed = value.hosts("allowed_hosts", candidate = false)
    require(allowed.containsAll(report.allowedHosts) && allowed.containsAll(selected))
}

/** Stable messages only: provider text and administrator replies may contain secrets. */
fun onlineHostError(error: Exception, saved: Boolean = false, applying: Boolean = false): String {
    if (saved) return "媒体主机已添加，但详情刷新失败。请重新加载详情，不必重复添加。"
    val api = error as? ApiException
    if (applying && (api?.status == 0 || api == null && error is IOException || (api?.status ?: 0) >= 500)) {
        return "暂时无法确认媒体主机是否已保存，请重新检查后核对。"
    }
    return when (api?.code) {
        "online_source_changed" -> "视频源配置已变化，请重新检查后再确认。"
        "management_required", "online_management_revoked" -> "请先验证管理权限，再检查媒体主机；儿童模式请先退出。"
        "online_host_check_unsupported" -> "此来源暂不支持媒体主机检查，请在网页端编辑来源的允许列表。"
        "online_host_candidate_limit" -> "此内容的媒体主机数量超过检查上限，暂时无法检查。"
        "online_allowed_host_limit" -> "允许的主机数量已达上限，请在网页端保留必要主机后重试。"
        "online_source_disabled" -> "此视频源已停用，请返回选择其他来源。"
        "online_disabled" -> "在线点播已停用。"
        "provider_entry_limit" -> "此内容的分集数量超过读取上限，暂时无法检查。"
        "provider_response_limit" -> "视频源返回的资料过大，暂时无法检查。"
        "provider_timeout", "network_timeout" -> "检查媒体主机超时，请稍后重新检查。"
        "provider_catalog_busy" -> "视频源正在处理其他请求，请稍后重新检查。"
        "invalid_online_response" -> "服务器返回了无法识别的媒体主机数据，请重新检查。"
        "viewing_locked" -> "观看模式已锁定，请重新进入。"
        else -> when (api?.status) {
            401 -> "无法执行此操作，请检查连接中的访问令牌或观看权限。"
            403 -> "请先验证管理权限，再检查媒体主机。"
            404 -> "服务器尚未支持媒体主机检查，请升级服务端，或在网页端添加。"
            409 -> "视频源配置已变化，请重新检查后再确认。"
            422 -> "媒体主机配置无效，请重新检查或在网页端调整允许列表。"
            else -> "暂时无法检查或添加媒体主机，请稍后重新检查。"
        }
    }
}
