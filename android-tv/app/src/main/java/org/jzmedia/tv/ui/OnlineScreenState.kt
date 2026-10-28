package org.jzmedia.tv.ui

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import org.jzmedia.tv.data.OnlineDetail
import org.jzmedia.tv.data.OnlineEntry
import org.jzmedia.tv.data.OnlineHealth
import org.jzmedia.tv.data.OnlineItemState
import org.jzmedia.tv.data.OnlineLibrary
import org.jzmedia.tv.data.OnlineSearchPage
import org.jzmedia.tv.data.OnlineSources

/** Content stays in memory for this server/viewing identity, never SavedState or preferences. */
class OnlineScreenState {
    var section by mutableStateOf("search")
    var query by mutableStateOf("")
    var sourceId by mutableStateOf("")
    var sources by mutableStateOf<OnlineSources?>(null)
    var sourcesLoading by mutableStateOf(true)
    var sourcesError by mutableStateOf("")
    var results by mutableStateOf<OnlineSearchPage?>(null)
    var loadedQuery by mutableStateOf("")
    var searching by mutableStateOf(false)
    var searchError by mutableStateOf("")
    var searchPage by mutableStateOf(1)
    var detail by mutableStateOf<OnlineDetail?>(null)
    var detailSourceId by mutableStateOf("")
    var detailExternalId by mutableStateOf("")
    var detailLoading by mutableStateOf(false)
    var detailError by mutableStateOf("")
    var lineId by mutableStateOf("")
    var item by mutableStateOf<OnlineItemState?>(null)
    var library by mutableStateOf<OnlineLibrary?>(null)
    var libraryOffset by mutableStateOf(0)
    var libraryLoading by mutableStateOf(false)
    var libraryError by mutableStateOf("")
    var health by mutableStateOf<OnlineHealth?>(null)
    var healthLoading by mutableStateOf(false)
    var healthError by mutableStateOf("")
    var notice by mutableStateOf("")
    var starting by mutableStateOf(false)
    var mutating by mutableStateOf(emptySet<String>())
    var keyboard by mutableStateOf(false)
    var autoplay by mutableStateOf(false)

    private var lifetime = 0L
    private val positions = linkedMapOf<String, OnlinePagePosition>()
    internal var pendingFocus: String? = null
    internal var hasMounted = false
    val showingDetail: Boolean get() = detailExternalId.isNotBlank()
    val contentKey: String get() = if (showingDetail) "detail:$detailSourceId:$detailExternalId" else "section:$section"

    internal fun attach(): Long = (++lifetime).also {
        sourcesLoading = true; searching = false; detailLoading = false
        libraryLoading = section != "search"; healthLoading = false; starting = false; mutating = emptySet()
    }
    internal fun current(ticket: Long): Boolean = lifetime == ticket
    internal fun detach(ticket: Long) {
        if (current(ticket)) {
            lifetime++
            sourcesLoading = false; searching = false; detailLoading = false
            libraryLoading = false; healthLoading = false; starting = false; mutating = emptySet()
        }
    }
    internal fun position(key: String): OnlinePagePosition = positions[key] ?: OnlinePagePosition()
    internal fun savePosition(key: String, position: OnlinePagePosition) {
        positions[key] = position
        while (positions.size > 128) positions.remove(positions.keys.first())
    }
}

internal data class OnlinePagePosition(val index: Int = 0, val offset: Int = 0, val focus: String = "")

internal data class OnlinePlaybackSelection(val position: Double, val scopeToken: String, val writerRevision: Long)

/** A saved version/line is never borrowed for another selected entry. */
internal fun onlinePlaybackSelection(entry: OnlineEntry, state: OnlineItemState?): OnlinePlaybackSelection? {
    val identity = entry.identity ?: return null
    if (!entry.supported || state?.available == false || state != null && state.itemId != identity.itemId) return null
    val progress = state?.selectedProgress?.takeIf { it.identity == identity }
    return OnlinePlaybackSelection(if (progress?.completed == false) progress.position else 0.0,
        state?.scopeToken.orEmpty(), progress?.writerRevision ?: 0)
}

internal fun onlineResumeEntry(detail: OnlineDetail, lineId: String, state: OnlineItemState?): OnlineEntry? =
    state?.lastProgress?.takeIf { it.identity.lineId == lineId }?.let { detail.findEntry(it.identity) }

internal fun onlineAvailabilityReason(reason: String): String = when (reason) {
    "online_disabled" -> "在线点播已关闭，记录仍会保留"
    "online_source_disabled" -> "视频源已停用，记录仍会保留"
    else -> "当前内容暂不可播放，记录仍会保留"
}

/** Lazy entries have to be brought into composition before their focus can be requested. */
internal fun onlineFocusItemIndex(state: OnlineScreenState, key: String): Int? {
    if (key.startsWith("online:tab:") || key == "online:refresh") return 1
    val body = 2 + if (state.notice.isNotBlank()) 1 else 0
    if (state.showingDetail) {
        if (key == "online:retry-detail") return body
        if (key == "online:description") return body
        if (key in setOf("online:detail-favorite", "online:detail-resume")) return body + 1
        if (key.startsWith("online:line:") || key == "online:autoplay") return body + 2
        if (key.startsWith("online:entry:")) {
            val entries = state.detail?.lines?.firstOrNull { it.id == state.lineId }?.entries.orEmpty()
            return entries.indexOfFirst { entry -> entry.supported && entry.identity?.let {
                "online:entry:${it.episodeId ?: it.lineId}" == key
            } == true }.takeIf { it >= 0 }?.let { body + 3 + it }
        }
        return null
    }
    if (state.section != "search") {
        if (key == "online:retry-library") return body
        val rows = state.library?.items.orEmpty()
        if (key == "online:previous-library" || key == "online:next-library") return body + rows.size
        return rows.indexOfFirst { key.startsWith("online:record:${it.item.id}:") }.takeIf { it >= 0 }?.let { body + it }
    }
    if (key.startsWith("online:source:") || key == "online:retry-sources") return body
    if (key == "online:search" || key == "online:keyboard") return body + 1
    if (key.startsWith("online:key:")) return (body + 2).takeIf { state.keyboard }
    val keyboard = if (state.keyboard) 1 else 0
    if (key == "online:health") return body + 2 + keyboard
    if (key == "online:retry-search") return body + 3 + keyboard
    val rows = state.results?.items.orEmpty()
    if (key == "online:previous-search" || key == "online:next-search") return body + 4 + keyboard + rows.size
    return rows.indexOfFirst { "online:result:${it.externalId}" == key }.takeIf { it >= 0 }?.let { body + 4 + keyboard + it }
}

/** Keep a remote/system-IME query bounded without cutting a surrogate pair. */
internal fun onlineSearchInput(value: String): String {
    val result = StringBuilder()
    var index = 0
    while (index < value.length) {
        val point = value.codePointAt(index)
        index += Character.charCount(point)
        if (Character.isISOControl(point)) continue
        if (result.length + Character.charCount(point) > 100) break
        result.appendCodePoint(point)
    }
    return result.toString()
}
