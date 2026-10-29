package org.jzmedia.tv.data

import org.json.JSONObject
import org.json.JSONArray

private val SOURCE_ID = Regex("src_[0-9a-f]{32}")
private val ITEM_ID = Regex("itm_[0-9a-f]{32}")
private val EPISODE_ID = Regex("ep_[0-9a-f]{32}")
private val LINE_ID = Regex("ln_[0-9a-f]{32}")
private const val MAX_CATALOG_ENTRIES = 2000
internal val ONLINE_OPAQUE = Regex("[A-Za-z0-9_-]{32,128}")
internal val ONLINE_SCOPE = Regex("[0-9a-f]{64}")

private fun JSONObject.string(name: String, max: Int = 4096): String = (opt(name) as? String)
    ?.also { require(it.length <= max) } ?: throw IllegalArgumentException("Invalid online text")
private fun JSONObject.boolean(name: String): Boolean = opt(name) as? Boolean
    ?: throw IllegalArgumentException("Invalid online boolean")
private fun JSONObject.number(name: String): Double = (opt(name) as? Number)?.toDouble()
    ?.also { require(it.isFinite()) } ?: throw IllegalArgumentException("Invalid online number")
private fun JSONObject.integer(name: String): Long {
    val raw = opt(name)
    require(raw is Int || raw is Long) { "Invalid online integer" }
    return (raw as Number).toLong().also { require(it >= 0) }
}
private fun JSONObject.optionalNumber(name: String): Double? = if (!has(name) || isNull(name)) null else number(name)
private fun JSONObject.optionalString(name: String): String? = if (!has(name) || isNull(name)) null else string(name)
private fun JSONObject.optionalObject(name: String): JSONObject? = if (!has(name) || isNull(name)) null else getJSONObject(name)
private fun JSONObject.list(name: String, max: Int): List<JSONObject> {
    val array = getJSONArray(name)
    require(array.length() <= max)
    return (0 until array.length()).map { array.getJSONObject(it) }
}
private fun JSONObject.only(allowed: Set<String>) { require(keys().asSequence().all { it in allowed }) }
internal fun checkedOnlineSource(value: String): String = value.also { require(SOURCE_ID.matches(it)) }
internal fun checkedOnlineItem(value: String): String = value.also { require(ITEM_ID.matches(it)) }
internal fun checkedOnlineScope(value: String): String = value.also { require(ONLINE_SCOPE.matches(it)) }
internal fun checkedOnlineSession(value: String): String = value.also { require(ONLINE_OPAQUE.matches(it)) }
internal fun checkedOnlineExternal(value: String): String {
    require(value.none { it.code in 0..31 || it.code == 127 })
    return value.trim().also { require(it.codePointCount(0, it.length) in 1..512) }
}
internal fun checkedOnlineCatalogExternal(value: String): String = value.also {
    require(Regex("[A-Za-z0-9_-]{1,128}").matches(it))
}

/** Absence is the old-server path; no application-version guess enables it. */
fun supportsOnlineVod(info: JSONObject): Boolean = when (val features = info.opt("features")) {
    is JSONArray -> (0 until features.length()).any { features.opt(it) == "online_vod" }
    is JSONObject -> features.opt("online_vod") == true
    else -> false
}

/** A server identity, never a local media ID or a caller-supplied stream URL. */
data class OnlineIdentity(val sourceId: String, val itemId: String, val kind: String,
                          val episodeId: String? = null, val lineId: String? = null) {
    init {
        checkedOnlineSource(sourceId); checkedOnlineItem(itemId)
        require(kind == "movie" || kind == "episode")
        require((kind == "episode") == (episodeId != null))
        require(episodeId == null || EPISODE_ID.matches(episodeId))
        require(lineId == null || LINE_ID.matches(lineId))
    }
    fun toJson(): JSONObject = JSONObject().put("source_id", sourceId).put("item_id", itemId).put("kind", kind)
        .put("episode_id", episodeId ?: JSONObject.NULL).also { if (lineId != null) it.put("line_id", lineId) }
    companion object {
        fun fromJson(value: JSONObject): OnlineIdentity {
            value.only(setOf("source_id", "item_id", "kind", "episode_id", "line_id"))
            return OnlineIdentity(value.string("source_id"), value.string("item_id"), value.string("kind"),
                value.optionalString("episode_id"), value.optionalString("line_id"))
        }
    }
}

data class OnlineSource(val id: String, val name: String, val provider: String,
                        val priority: Int = 100, val enabled: Boolean = true) {
    companion object {
        fun fromJson(value: JSONObject) = OnlineSource(checkedOnlineSource(value.string("id")), value.string("name", 128),
            value.string("provider").also { require(it in setOf("maccms", "open_catalog")) },
            if (value.has("priority")) value.integer("priority").also { require(it <= 1000) }.toInt() else 100,
            if (value.has("enabled")) value.boolean("enabled") else true)
    }
}
data class OnlineSources(val enabled: Boolean, val items: List<OnlineSource>) {
    companion object { fun fromJson(value: JSONObject) = OnlineSources(value.boolean("enabled"), value.list("items", 1000).map(OnlineSource::fromJson)) }
}
data class OnlineSearchItem(val externalId: String, val title: String, val year: String,
                            val kind: String, val description: String) {
    companion object { fun fromJson(value: JSONObject) = OnlineSearchItem(checkedOnlineCatalogExternal(value.string("external_id")),
        value.string("title", 512), value.string("year", 32), value.string("kind").also { require(it in setOf("movie", "tv", "unknown")) },
        value.string("description", 8192)) }
}
data class OnlineSearchPage(val source: OnlineSource, val items: List<OnlineSearchItem>,
                            val page: Int, val pageCount: Int, val total: Int) {
    companion object { fun fromJson(value: JSONObject) = OnlineSearchPage(OnlineSource.fromJson(value.getJSONObject("source")),
        value.list("items", 100).map(OnlineSearchItem::fromJson), value.integer("page").also { require(it in 1L..1000L) }.toInt(),
        value.integer("page_count").also { require(it <= Int.MAX_VALUE) }.toInt(), value.integer("total").also { require(it <= Int.MAX_VALUE) }.toInt()) }
}
data class OnlineCatalogItem(val id: String, val sourceId: String, val kind: String,
                             val title: String, val externalId: String = "") {
    companion object { fun fromJson(value: JSONObject) = OnlineCatalogItem(checkedOnlineItem(value.string("id")),
        checkedOnlineSource(value.string("source_id")), value.string("kind").also { require(it == "movie" || it == "tv") },
        value.string("title", 512), if (value.has("external_id")) checkedOnlineExternal(value.string("external_id")) else "") }
}
data class OnlineEntry(val label: String, val identity: OnlineIdentity?, val transport: String?,
                       val supported: Boolean, val reason: String) {
    companion object { fun fromJson(value: JSONObject): OnlineEntry {
        val identity = value.optionalObject("identity")?.let(OnlineIdentity::fromJson)
        val supported = value.boolean("supported")
        val transport = value.optionalString("transport")
        require(transport == null || transport in setOf("mp4", "hls"))
        require(!supported || identity != null && transport != null)
        return OnlineEntry(value.string("label", 512), identity, transport, supported, value.string("reason", 128))
    } }
}
data class OnlineLine(val id: String, val label: String, val entries: List<OnlineEntry>) {
    companion object { fun fromJson(value: JSONObject) = OnlineLine(value.string("id").also { require(LINE_ID.matches(it)) },
        value.string("label", 512), value.list("entries", MAX_CATALOG_ENTRIES).map(OnlineEntry::fromJson)) }
}
data class OnlineAttribution(val label: String, val url: String) {
    companion object { fun fromJson(value: JSONObject) = OnlineAttribution(value.string("label", 512), value.string("url", 2048)) }
}
data class OnlineDetail(val source: OnlineSource, val item: OnlineCatalogItem, val description: String,
                       val year: String, val lines: List<OnlineLine>, val attribution: List<OnlineAttribution> = emptyList()) {
    fun findEntry(identity: OnlineIdentity): OnlineEntry? = lines.firstOrNull { it.id == identity.lineId }
        ?.entries?.firstOrNull { it.supported && it.identity == identity }
    fun nextEpisode(identity: OnlineIdentity): OnlineEntry? {
        if (identity.kind != "episode" || item.kind != "tv" || item.id != identity.itemId || source.id != identity.sourceId) return null
        val entries = lines.firstOrNull { it.id == identity.lineId }?.entries ?: return null
        val index = entries.indexOfFirst { it.identity == identity }
        return entries.getOrNull(index + 1)?.takeIf { index >= 0 && it.supported && it.identity?.kind == "episode"
            && it.identity.sourceId == identity.sourceId && it.identity.itemId == identity.itemId && it.identity.lineId == identity.lineId }
    }
    companion object { fun fromJson(value: JSONObject): OnlineDetail {
        val source = OnlineSource.fromJson(value.getJSONObject("source"))
        val item = OnlineCatalogItem.fromJson(value.getJSONObject("item"))
        val rawLines = value.list("lines", 16)
        require(item.sourceId == source.id &&
            rawLines.sumOf { it.getJSONArray("entries").length().toLong() } <= MAX_CATALOG_ENTRIES.toLong())
        val lines = rawLines.map(OnlineLine::fromJson)
        require(lines.flatMap { it.entries }.all { it.identity == null || it.identity.sourceId == source.id && it.identity.itemId == item.id })
        require(lines.all { line -> line.entries.all { it.identity == null || it.identity.lineId == line.id } })
        return OnlineDetail(source, item, value.string("description", 8192), value.string("year", 32), lines,
            value.list("attribution", 32).map(OnlineAttribution::fromJson))
    } }
}

data class OnlineDescriptor(val identity: OnlineIdentity, val sessionId: String, val mediaUrl: String,
                            val transport: String, val durationSeconds: Double?, val expiresAt: Long,
                            val seekable: Boolean = true) {
    companion object { fun fromJson(value: JSONObject, expectedIdentity: OnlineIdentity? = null): OnlineDescriptor {
        value.only(setOf("identity", "session_id", "media_url", "transport", "processing", "seekable", "duration_seconds", "time_offset_seconds", "expires_at"))
        val identity = OnlineIdentity.fromJson(value.getJSONObject("identity"))
        require(expectedIdentity == null || identity == expectedIdentity)
        val session = checkedOnlineSession(value.string("session_id"))
        val media = value.string("media_url")
        require(media == "/api/online/sessions/$session/media")
        require(value.string("processing") == "passthrough" && value.number("time_offset_seconds") == 0.0)
        val transport = value.string("transport").also { require(it in setOf("mp4", "hls")) }
        val duration = value.optionalNumber("duration_seconds")?.also { require(it > 0) }
        return OnlineDescriptor(identity, session, media, transport, duration,
            value.integer("expires_at").also { require(it > 0) }, value.boolean("seekable"))
    } }
}

data class OnlineProgress(val identity: OnlineIdentity, val position: Double, val duration: Double?,
                          val paused: Boolean, val completed: Boolean, val updatedAt: Double, val writerRevision: Long) {
    companion object { fun fromJson(value: JSONObject) = OnlineProgress(OnlineIdentity.fromJson(value.getJSONObject("identity")),
        value.number("position").also { require(it >= 0) }, value.optionalNumber("duration")?.also { require(it > 0) },
        value.boolean("paused"), value.boolean("completed"), value.number("updated_at").also { require(it >= 0) },
        value.integer("writer_revision").also { require(it > 0) }) }
}
data class OnlineItemState(val itemId: String, val favorite: Boolean, val lastProgress: OnlineProgress?,
                           val selectedProgress: OnlineProgress?, val available: Boolean, val reason: String,
                           val generation: Long, val scopeToken: String) {
    companion object { fun fromJson(value: JSONObject) = OnlineItemState(checkedOnlineItem(value.string("item_id")),
        value.boolean("favorite"), value.optionalObject("last_progress")?.let(OnlineProgress::fromJson),
        value.optionalObject("selected_progress")?.let(OnlineProgress::fromJson), value.boolean("available"),
        value.string("reason", 128), value.integer("generation"), checkedOnlineScope(value.string("scope_token"))).also {
            require(it.lastProgress == null || it.lastProgress.identity.itemId == it.itemId)
            require(it.selectedProgress == null || it.selectedProgress.identity.itemId == it.itemId)
        } }
}
data class OnlineLibraryEntry(val item: OnlineCatalogItem, val source: OnlineSource, val favorite: Boolean,
                              val progress: OnlineProgress?, val available: Boolean, val reason: String) {
    companion object { fun fromJson(value: JSONObject) = OnlineLibraryEntry(OnlineCatalogItem.fromJson(value.getJSONObject("item")),
        OnlineSource.fromJson(value.getJSONObject("source")), value.boolean("favorite"),
        value.optionalObject("progress")?.let(OnlineProgress::fromJson), value.boolean("available"), value.string("reason", 128)).also {
            require(it.item.sourceId == it.source.id)
            require(it.progress == null || it.progress.identity.sourceId == it.source.id && it.progress.identity.itemId == it.item.id)
        } }
}
data class OnlineLibrary(val items: List<OnlineLibraryEntry>, val total: Int, val limit: Int, val offset: Int,
                        val generation: Long, val scopeToken: String) {
    companion object { fun fromJson(value: JSONObject) = OnlineLibrary(value.list("items", 100).map(OnlineLibraryEntry::fromJson),
        value.integer("total").also { require(it <= Int.MAX_VALUE) }.toInt(), value.integer("limit").also { require(it in 1L..100L) }.toInt(),
        value.integer("offset").also { require(it <= 1000) }.toInt(), value.integer("generation"), checkedOnlineScope(value.string("scope_token"))) }
}
data class OnlineClaim(val identity: OnlineIdentity, val writerId: String, val writerRevision: Long,
                       val expiresAt: Double, val progress: OnlineProgress?, val generation: Long, val scopeToken: String) {
    companion object { fun fromJson(value: JSONObject) = OnlineClaim(OnlineIdentity.fromJson(value.getJSONObject("identity")),
        checkedOnlineSession(value.string("writer_id")), value.integer("writer_revision").also { require(it > 0) },
        value.number("expires_at").also { require(it > 0) }, value.optionalObject("progress")?.let(OnlineProgress::fromJson),
        value.integer("generation"), checkedOnlineScope(value.string("scope_token"))).also {
            require(it.progress == null || it.progress.identity == it.identity && it.progress.writerRevision == it.writerRevision)
        } }
}
data class OnlineProgressResult(val progress: OnlineProgress, val generation: Long, val scopeToken: String) {
    companion object { fun fromJson(value: JSONObject) = OnlineProgressResult(OnlineProgress.fromJson(value.getJSONObject("progress")),
        value.integer("generation"), checkedOnlineScope(value.string("scope_token"))) }
}
data class OnlineFavoriteResult(val itemId: String, val favorite: Boolean, val generation: Long, val scopeToken: String) {
    companion object { fun fromJson(value: JSONObject) = OnlineFavoriteResult(checkedOnlineItem(value.string("item_id")),
        value.boolean("favorite"), value.integer("generation"), checkedOnlineScope(value.string("scope_token"))) }
}
data class OnlineDeleteResult(val deleted: Int, val generation: Long, val scopeToken: String) {
    companion object { fun fromJson(value: JSONObject) = OnlineDeleteResult(value.integer("deleted").also { require(it <= Int.MAX_VALUE) }.toInt(),
        value.integer("generation"), checkedOnlineScope(value.string("scope_token"))) }
}
data class OnlineHealthObservation(val state: String, val mode: String, val stage: String? = null,
                                 val category: String? = null, val ageSeconds: Double? = null,
                                 val observedAt: Long? = null, val configurationFingerprint: String = "", val reason: String = "") {
    val label: String get() = when (state) {
        "disabled" -> "已停用"; "local" -> "本地目录"; "stale" -> "观察已过期"; "failure" -> "近期失败"
        "success" -> when (stage) { "catalogue" -> "查询通过"; "manifest" -> "清单已读"; else -> "取流通过" }
        else -> "未观察"
    } + if (mode == "proxy") " · 代理" else " · 直连"
    companion object { fun fromJson(value: JSONObject) = OnlineHealthObservation(
        value.string("state").also { require(it in setOf("unknown", "success", "failure", "stale", "disabled", "local")) },
        value.string("mode").also { require(it in setOf("direct", "proxy")) }, value.optionalString("stage"),
        value.optionalString("category"), value.optionalNumber("age_seconds"),
        if (value.isNull("observed_at")) null else value.integer("observed_at"),
        checkedOnlineScope(value.string("configuration_fingerprint")), value.string("reason", 128)) }
}
data class OnlineHealth(val sourceId: String, val api: OnlineHealthObservation, val media: OnlineHealthObservation) {
    companion object { fun fromJson(value: JSONObject): OnlineHealth {
        require(value.string("cache_scope") == "process" && value.integer("max_age_seconds") == 1800L)
        val purposes = value.getJSONObject("purposes")
        return OnlineHealth(checkedOnlineSource(value.string("source_id")), OnlineHealthObservation.fromJson(purposes.getJSONObject("api")),
            OnlineHealthObservation.fromJson(purposes.getJSONObject("media")))
    } }
}

fun onlineErrorMessage(error: Throwable): String = when ((error as? ApiException)?.code) {
    "online_progress_writer_conflict" -> "另一播放页已更新此进度，本次播放不再覆盖它；重新播放可再次保存。"
    "online_state_scope_changed", "online_state_scope_required" -> "观看身份已变化，请重新打开在线页面。"
    "online_access_revoked", "viewing_locked", "viewing_session_required", "viewing_session_invalid" -> "观看权限已变化，请重新连接服务。"
    "online_children_unavailable" -> "儿童模式暂不支持在线内容。"
    "online_source_disabled", "online_disabled" -> "视频源或在线功能已停用，已有记录仍会保留。"
    "online_favorite_limit" -> "当前设备和模式的收藏已达上限，请先取消部分收藏。"
    "online_progress_limit" -> "当前设备和模式的进度已达上限，请先清除旧观看记录。"
    "provider_entry_limit" -> "视频源返回的分集数量超过读取上限，请选择其他内容或来源。"
    "provider_response_limit" -> "视频源返回的资料过大，暂时无法读取。"
    "provider_not_found", "online_identity_not_found" -> "内容或播放线路已变化，请返回详情重新选择。"
    "invalid_online_response" -> "服务器返回了无法识别的在线数据，请更新服务端后重试。"
    else -> when ((error as? ApiException)?.status) {
        401, 403 -> "访问被拒绝，请检查观看模式或连接令牌。"
        404 -> if (error.code.isBlank()) "当前服务器尚未支持在线功能，请升级服务端。" else "内容或来源已变化，请返回刷新。"
        409 -> "在线状态已变化，请返回刷新后重试。"
        429 -> "当前请求较多，请稍后重试。"
        502, 503 -> "视频源暂不可用，可尝试其它线路或稍后重试。"
        else -> "在线内容暂时不可用，请检查连接后重试。"
    }
}
