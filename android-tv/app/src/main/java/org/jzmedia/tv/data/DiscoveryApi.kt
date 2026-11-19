package org.jzmedia.tv.data

import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.HttpUrl
import org.json.JSONArray
import org.json.JSONObject

internal const val DISCOVERY_PATH = "/api/ai/search/results"
const val DISCOVERY_QUERY_LIMIT = 500
private val sessionIdFormat = Regex("[A-Za-z0-9_-]{32}")

/** Only the results client uses this in-memory jar; changing server or viewing lease creates a new one. */
internal class DiscoveryCookies(private val results: HttpUrl) : CookieJar {
    private var device: Cookie? = null
    private val refresh = results.newBuilder().addPathSegment("refresh").build()

    @Synchronized override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) {
        if (url != results && url != refresh) return
        val cookie = cookies.lastOrNull { it.name == "jz_discovery_device" && it.domain == url.host } ?: return
        device = cookie.takeIf { it.value.length == 43 }
    }

    @Synchronized override fun loadForRequest(url: HttpUrl): List<Cookie> {
        if (url != results && url != refresh) return emptyList()
        val cookie = device ?: return emptyList()
        // Proxies may mount the API under a prefix without rewriting the server's Set-Cookie Path.
        val matchUrl = if (cookie.path == DISCOVERY_PATH) url.newBuilder().encodedPath(DISCOVERY_PATH).build() else url
        return if (cookie.expiresAt > System.currentTimeMillis() && cookie.matches(matchUrl)) listOf(cookie) else emptyList()
    }
}

fun supportsAiDiscovery(info: JSONObject): Boolean = when (val features = info.opt("features")) {
    is JSONArray -> (0 until features.length()).any { features.opt(it) == "ai_discovery" }
    is JSONObject -> features.opt("ai_discovery") == true
    else -> false
}

sealed interface DiscoveryTarget {
    data class Detail(val kind: String, val id: Long, val season: Int = 0, val versionId: Long? = null) : DiscoveryTarget
    data class Online(val title: String) : DiscoveryTarget
}

data class DiscoveryItem(val key: String, val title: String, val year: String, val kind: String,
                         val poster: String, val reason: String, val ratingLabel: String, val watchLabel: String,
                         val target: DiscoveryTarget?)
data class DiscoveryComparison(val title: String, val text: String, val key: String)
data class DiscoveryResult(val sessionId: String, val source: String, val status: String, val intent: String,
                           val summary: String, val clarification: String, val warnings: List<String>,
                           val comparison: List<DiscoveryComparison>, val items: List<DiscoveryItem>)

private fun JSONObject.strings(field: String): List<String> {
    val values = optJSONArray(field) ?: return emptyList()
    return (0 until values.length()).mapNotNull { (values.opt(it) as? String)?.takeIf(String::isNotBlank) }
}

private fun JSONObject.target(source: String): DiscoveryTarget? {
    if (!optBoolean("in_library")) {
        return if (source == "public" && text("title").isNotBlank()) DiscoveryTarget.Online(text("title")) else null
    }
    val id = optLong("id").takeIf { it > 0 } ?: return null
    if (!isNull("matched_episode_id")) {
        return optLong("matched_episode_id").takeIf { it > 0 }?.let { DiscoveryTarget.Detail("episode", it) }
    }
    // Local identity may represent several files across libraries. Details and playback
    // must open the exact movie/episode file whose tracks and runtime were verified.
    val version = when {
        !isNull("matched_version_id") -> optLong("matched_version_id")
        !isNull("version_id") -> optLong("version_id")
        else -> null
    }
    if (version != null && version <= 0) return null
    return when (text("kind")) {
        "movie" -> DiscoveryTarget.Detail("movie", version ?: id, versionId = version)
        "tv" -> DiscoveryTarget.Detail("show", id)
        "season" -> {
            val show = optLong("show_id")
            val season = optInt("season", -1)
            if (show > 0 && season >= 0) DiscoveryTarget.Detail("season", show, season) else null
        }
        "episode" -> DiscoveryTarget.Detail("episode", version ?: id)
        else -> null
    }
}

private fun discoveryItem(row: JSONObject, source: String): DiscoveryItem {
    val poster = row.text("poster_path")
    val localPoster = poster.isNotBlank() && !poster.startsWith("//") &&
        poster.none { it in ":\\?#%" || it.isISOControl() } && poster.split('/').none { it == "." || it == ".." }
    val rating = row.optDouble("rating").takeIf { it.isFinite() && it > 0 && it <= 10 }
    val ratingSource = when (row.text("rating_source")) { "tmdb" -> "TMDB"; "douban" -> "豆瓣"; "custom" -> "自评"; else -> "" }
    val ratingLabel = rating?.let {
        listOf(ratingSource, "评分", JSONObject.numberToString(it), "分").filter(String::isNotBlank).joinToString(" ")
    }.orEmpty()
    val reason = row.text("reason").ifBlank { row.strings("evidence").firstOrNull() ?: row.text("overview") }
    val watch = when (row.text("watch_state")) {
        "watched" -> "已看完"
        "in_progress" -> "在看"
        "unseen" -> "未开始"
        "unknown" -> "观看状态未知"
        else -> ""
    }
    return DiscoveryItem(row.text("key"), row.text("title"), row.text("year").takeUnless { it == "0" }.orEmpty(),
        row.text("kind"), if (localPoster) posterPath(row) else "",
        reason.takeUnless { it == ratingLabel }.orEmpty(), ratingLabel, watch, row.target(source))
}

internal fun readDiscoveryResult(value: JSONObject): DiscoveryResult {
    if (value.opt("ok") == false) {
        val code = value.text("code")
        val message = when (code) {
            "disabled", "not_configured", "invalid_config", "missing_key", "invalid_credentials" ->
                "请在 jzmedia 网页的设置中检查智能辅助配置"
            "tmdb_not_configured" -> "请在 jzmedia 网页的在线资料服务设置中配置 TMDB"
            "session_expired" -> "本次找片记录已过期或观看范围已改变，请重新找片"
            "empty_scope" -> "当前范围没有可搜索的视频库，请重新选择媒体库"
            "input_too_large" -> "请缩短找片要求后重试"
            "invalid_result", "invalid_response" -> "模型返回的结果未通过核验，请重试或换个说法"
            "rate_limited", "busy" -> "模型服务繁忙或已达到额度，请稍后重试"
            "daily_limit" -> "今日智能辅助调用次数已达上限，可继续使用普通搜索"
            "timeout" -> "模型响应超时，请稍后重试"
            "tmdb_unavailable" -> "TMDB 公开片目暂时不可用，可稍后重试或搜索当前片库"
            "upstream_error", "connection_error" -> "无法从模型服务取得结果，请检查服务端的智能辅助配置或稍后重试"
            else -> "智能找片暂时不可用，请重试"
        }
        throw ApiException(200, message, code)
    }
    val sessionId = value.text("session_id")
    val source = value.text("source")
    val items = value.rows().map { discoveryItem(it, source) }
    if (value.opt("ok") != true || !sessionIdFormat.matches(sessionId) || source !in listOf("library", "public") ||
        value.optJSONArray("items")?.length() != items.size || items.any { it.key.isBlank() } ||
        items.map { it.key }.distinct().size != items.size) {
        throw ApiException(0, "服务器返回了无法识别的找片结果")
    }
    return DiscoveryResult(sessionId, source, value.text("status"), value.text("intent"),
        value.text("summary"), value.text("clarification"), value.strings("warnings"),
        value.rows("comparison").map { DiscoveryComparison(it.text("title"), it.text("text"), it.text("key")) },
        items)
}

class DiscoveryApi(private val api: JzApi) {
    private fun scope(library: Long, entryKind: String): JSONObject {
        val kinds = when (entryKind) {
            "movie", "movies" -> listOf("movie")
            "show", "shows", "tv" -> listOf("tv")
            else -> listOf("movie", "tv")
        }
        return JSONObject().put("kind", kinds.first()).put("kinds", JSONArray(kinds)).also {
            if (library > 0) it.put("media_library_id", library)
        }
    }

    suspend fun search(library: Long, entryKind: String, query: String, sessionId: String? = null,
                       source: String = "library", reusePlan: Boolean = false): DiscoveryResult {
        val text = query.trim()
        require(text.codePointCount(0, text.length) in 1..DISCOVERY_QUERY_LIMIT) { "请输入 1–500 字的找片要求" }
        require(source == "library" || source == "public")
        require(sessionId == null || sessionIdFormat.matches(sessionId))
        require(!reusePlan || sessionId != null)
        val body = scope(library, entryKind).put("q", text).put("source", source).put("allow_query_source", false)
        if (sessionId != null) body.put("session_id", sessionId)
        if (reusePlan) body.put("reuse_plan", true)
        return readDiscoveryResult(api.discoveryResults(body, refresh = false))
    }

    suspend fun refresh(library: Long, entryKind: String, sessionId: String): DiscoveryResult {
        require(sessionIdFormat.matches(sessionId))
        val body = scope(library, entryKind).put("session_id", sessionId)
        return readDiscoveryResult(api.discoveryResults(body, refresh = true))
    }
}
