package org.jzmedia.tv.ui

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import org.jzmedia.tv.data.OnlineDetail
import org.jzmedia.tv.data.OnlineEntry
import org.jzmedia.tv.data.OnlineHealth
import org.jzmedia.tv.data.OnlineIdentity
import org.jzmedia.tv.data.OnlineItemState
import org.jzmedia.tv.data.OnlineLibrary
import org.jzmedia.tv.data.OnlineSearchPage
import org.jzmedia.tv.data.OnlineSources
import org.jzmedia.tv.data.OnlineCategory

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
    var categories by mutableStateOf(emptyList<OnlineCategory>())
    var categoryId by mutableStateOf("")
    var categoryRoot by mutableStateOf("")
    internal var channelSelections by mutableStateOf(emptyMap<String, String>())
    var year by mutableStateOf("")
    var yearInput by mutableStateOf("")
    internal var loadedSelection by mutableStateOf<OnlineCatalogSelection?>(null)
    var cataloguePending by mutableStateOf(true)
    internal var resultColumns = 5
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
    internal val detailUi = OnlineDetailUiState()

    private var lifetime = 0L
    private val positions = linkedMapOf<String, OnlinePagePosition>()
    internal var pendingFocus: String? = null
    val showingDetail: Boolean get() = detailExternalId.isNotBlank()
    val contentKey: String get() = if (showingDetail) "detail:$detailSourceId:$detailExternalId"
        else if (section == "search") "catalogue:$sourceId:$loadedSelection:$searchPage" else "section:$section"

    internal fun attach(): Long = (++lifetime).also {
        pendingFocus = null
        sourcesLoading = true; searching = false; detailLoading = false
        libraryLoading = section != "search"; healthLoading = false; starting = false; mutating = emptySet()
    }
    internal fun current(ticket: Long): Boolean = lifetime == ticket
    internal fun detach(ticket: Long) {
        if (current(ticket)) {
            interruptCatalogue()
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

    internal fun interruptCatalogue() {
        if (searching) cataloguePending = true
        searching = false
    }

    /** Recheck bindings after returning from playback or explicitly refreshing sources. */
    internal fun acceptSources(value: OnlineSources): Boolean {
        val old = sources
        val previous = old?.items?.firstOrNull { it.id == sourceId }
        val selected = value.items.firstOrNull { it.id == sourceId && it.enabled }
            ?: value.items.firstOrNull { it.enabled }
        val invalidated = sourceId != selected?.id.orEmpty() || old != null && (
            old.scopeToken != value.scopeToken || old.enabled != value.enabled ||
            previous?.configurationToken != selected?.configurationToken ||
            previous?.browseSupported != selected?.browseSupported ||
            previous?.yearFilterSupported != selected?.yearFilterSupported)
        sources = value
        sourceId = selected?.id.orEmpty()
        if (invalidated) {
            results = null; categories = emptyList(); categoryId = ""; categoryRoot = ""; channelSelections = emptyMap()
            year = ""; yearInput = ""; query = ""; loadedQuery = ""
            loadedSelection = null; searchPage = 1; searching = false; searchError = ""; cataloguePending = true
            detail = null; detailSourceId = ""; detailExternalId = ""; detailError = ""; item = null
            detailLoading = false; starting = false; pendingFocus = null
            positions.clear()
            detailUi.reset()
            keyboard = false
        }
        return invalidated
    }
}

/** Pagination and retries use the submitted selection, never unfinished input. */
internal data class OnlineCatalogSelection(val sourceId: String, val query: String = "", val categoryId: String = "", val year: String = "") {
    val browsing: Boolean get() = query.isBlank()
}

/** A delayed focus request is valid only until the viewer moves focus elsewhere. */
internal data class OnlineFocusTicket(val version: Long, val memoryKey: String, val memoryRevision: Long) {
    fun isCurrent(version: Long, memoryKey: String, memoryRevision: Long): Boolean =
        this.version == version && this.memoryKey == memoryKey && this.memoryRevision == memoryRevision
}

internal fun onlineCategoryLabel(categories: List<OnlineCategory>, id: String): String {
    val byId = categories.associateBy { it.id }
    val labels = mutableListOf<String>()
    val seen = mutableSetOf<String>()
    var node = byId[id]
    while (node != null && seen.add(node.id)) {
        labels.add(node.name)
        node = byId[node.parentId]
    }
    return labels.asReversed().joinToString(" / ").ifBlank { "全部分类" }
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

/** A record resumes only its exact line and episode, and returns to the matching group. */
internal fun onlinePrepareRecordResume(ui: OnlineDetailUiState, detail: OnlineDetail, identity: OnlineIdentity): OnlineEntry? {
    val entry = detail.findEntry(identity) ?: return null
    val line = detail.lines.firstOrNull { it.id == identity.lineId } ?: return null
    val position = onlineEpisodeIndex(line.entries, identity)
    if (position < 0) return null
    ui.bind(detail.item.id, line.id)
    ui.selectGroup(position / ONLINE_EPISODE_GROUP_SIZE)
    ui.focus(onlineEpisodeKey(line.id, entry, position))
    return entry
}

internal fun onlineAvailabilityReason(reason: String): String = when (reason) {
    "online_disabled" -> "在线点播已关闭，记录仍会保留"
    "online_source_disabled" -> "视频源已停用，记录仍会保留"
    else -> "当前内容暂不可播放，记录仍会保留"
}

/** Lazy entries have to be brought into composition before their focus can be requested. */
internal fun onlineFocusItemIndex(state: OnlineScreenState, key: String): Int? {
    if (key.startsWith("online:tab:") || key in setOf("online:search", "online:query", "online:keyboard", "online:source-button")) return 0
    val body = 1 + if (state.notice.isNotBlank()) 1 else 0
    if (state.showingDetail) {
        if (key == "online:retry-detail") return body
        return null
    }
    if (state.section != "search") {
        if (key == "online:retry-library") return body
        val rows = state.library?.items.orEmpty()
        if (key == "online:previous-library" || key == "online:next-library") return body + rows.size
        return rows.indexOfFirst { key.startsWith("online:record:${it.item.id}:") }.takeIf { it >= 0 }?.let { body + it }
    }
    if (key == "online:retry-sources") return body
    val hasFilters = state.sources?.items?.firstOrNull { it.id == state.sourceId }?.browseSupported == true
    if (key.startsWith("online:root:") || key.startsWith("online:category:") || key.startsWith("online:year:") ||
        key == "online:more-roots" || key == "online:more-categories") return body.takeIf { hasFilters }
    val result = body + if (hasFilters) 1 else 0
    if (key == "online:retry-search") return result
    val rows = state.results?.items.orEmpty()
    val columns = state.resultColumns.coerceAtLeast(1)
    if (key == "online:previous-search" || key == "online:next-search") return result + 1 + (rows.size + columns - 1) / columns
    return rows.indexOfFirst { "online:result:${it.externalId}" == key }.takeIf { it >= 0 }?.let { result + 1 + it / columns }
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
