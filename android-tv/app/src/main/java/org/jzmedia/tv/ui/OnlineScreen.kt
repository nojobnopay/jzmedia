package org.jzmedia.tv.ui

import android.content.Context
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.ApiException
import org.jzmedia.tv.data.OnlineApi
import org.jzmedia.tv.data.OnlineEntry
import org.jzmedia.tv.data.OnlineIdentity
import org.jzmedia.tv.data.OnlineLibraryEntry
import org.jzmedia.tv.data.onlineErrorMessage
import org.jzmedia.tv.playback.OnlinePlaybackRequest
import org.jzmedia.tv.playback.playbackTime
import java.security.MessageDigest
import kotlin.coroutines.coroutineContext

private class OnlineRequestSlot {
    private var revision = 0L
    private var job: Job? = null
    fun cancel() { revision++; job?.cancel(); job = null }
    fun start(scope: CoroutineScope, work: suspend (Long) -> Unit): Long {
        cancel()
        val ticket = revision
        job = scope.launch { work(ticket) }
        return ticket
    }
    fun current(ticket: Long): Boolean = revision == ticket
}

/** Native TV catalogue: identities and server artwork paths stay scoped to this viewing session. */
@Composable
fun OnlineScreen(api: JzApi, memory: FocusMemory, refresh: Int = 0,
                 state: OnlineScreenState = remember(api) { OnlineScreenState() },
                 onPlay: (OnlinePlaybackRequest) -> Unit, back: () -> Unit) {
    val online = remember(api) { OnlineApi(api) }
    val scope = rememberCoroutineScope()
    val lifetime = remember(state, api) { state.attach() }
    val catalogue = remember { OnlineRequestSlot() }
    val sourceRead = remember { OnlineRequestSlot() }
    val itemRead = remember { OnlineRequestSlot() }
    val libraryRead = remember { OnlineRequestSlot() }
    val healthRead = remember { OnlineRequestSlot() }
    val playRead = remember { OnlineRequestSlot() }
    val list = rememberLazyListState(state.position(state.contentKey).index, state.position(state.contentKey).offset)
    val focus = remember { mutableMapOf<String, FocusRequester>() }
    val focusVersion = remember { longArrayOf(0L) }
    var pendingFocusTicket by remember { mutableStateOf<OnlineFocusTicket?>(null) }
    var restoreAttempt by remember { mutableStateOf(0) }
    var sourceDialog by remember { mutableStateOf(false) }
    var categoryDialog by remember { mutableStateOf<String?>(null) }
    var categoryReturn by remember { mutableStateOf("online:more-roots") }
    var yearDialog by remember { mutableStateOf(false) }
    var yearError by remember { mutableStateOf("") }
    var detailRetryFocusFrom by remember { mutableStateOf<OnlineFocusTicket?>(null) }
    var detailInitialFocusAllowed by remember { mutableStateOf(true) }
    val context = LocalContext.current
    val preferences = remember(context) { context.getSharedPreferences("online_preferences", Context.MODE_PRIVATE) }
    val mode = if (api.viewing?.mode == "private") "private" else "normal"
    val preferenceKey = remember(api, mode) {
        val origin = MessageDigest.getInstance("SHA-256").digest(api.baseUrl.toByteArray())
            .joinToString("") { "%02x".format(it) }
        "autoplay.$origin.$mode"
    }
    fun alive() = state.current(lifetime)
    fun savePosition() {
        val previous = state.position(state.contentKey)
        state.savePosition(state.contentKey, previous.copy(index = list.firstVisibleItemIndex, offset = list.firstVisibleItemScrollOffset))
    }
    fun target(key: String) {
        state.pendingFocus = key
        pendingFocusTicket = OnlineFocusTicket(focusVersion[0], memory.key, memory.revision)
        restoreAttempt++
    }
    fun Modifier.onlineFocus(key: String): Modifier = focusRequester(focus.getOrPut(key) { FocusRequester() })
        .onFocusChanged { if (it.isFocused) {
            focusVersion[0]++
            memory.focused(key)
            state.savePosition(state.contentKey, state.position(state.contentKey).copy(focus = key))
        } }

    fun loadHealth() {
        val source = state.sourceId
        healthRead.start(scope) { ticket ->
            state.health = null; state.healthError = ""
            if (source.isBlank() || state.sources?.enabled != true) { state.healthLoading = false; return@start }
            state.healthLoading = true
            try {
                val value = online.health(source)
                coroutineContext.ensureActive()
                if (alive() && healthRead.current(ticket) && source == state.sourceId) state.health = value
            } catch (e: CancellationException) { throw e }
            catch (_: Exception) { if (alive() && healthRead.current(ticket)) state.healthError = "状态暂不可读" }
            finally { if (alive() && healthRead.current(ticket)) state.healthLoading = false }
        }
    }
    fun loadItem() {
        val detail = state.detail ?: return
        val focusBeforeRequest = OnlineFocusTicket(focusVersion[0], memory.key, memory.revision)
        itemRead.start(scope) { ticket ->
            try {
                val value = online.itemState(detail.item.id)
                coroutineContext.ensureActive()
                if (alive() && itemRead.current(ticket) && state.detail?.item?.id == detail.item.id) {
                    val resumeLine = onlineResumeLineCandidate(detail, value, state.lineId, state.starting,
                        state.detailUi.userInteracted || !focusBeforeRequest.isCurrent(focusVersion[0], memory.key, memory.revision))
                    if (resumeLine != null) {
                        state.detailUi.bind(detail.item.id, resumeLine)
                        state.lineId = resumeLine
                    }
                    state.item = value
                }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { if (alive() && itemRead.current(ticket)) state.notice = onlineErrorMessage(e) }
        }
    }
    fun loadLibrary(offset: Int = state.libraryOffset) {
        val section = state.section
        if (section == "search") return
        libraryRead.start(scope) { ticket ->
            state.libraryLoading = true; state.libraryError = ""; state.libraryOffset = offset
            try {
                val value = online.library(section, offset)
                coroutineContext.ensureActive()
                if (alive() && libraryRead.current(ticket) && section == state.section) state.library = value
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { if (alive() && libraryRead.current(ticket)) state.libraryError = onlineErrorMessage(e) }
            finally { if (alive() && libraryRead.current(ticket)) state.libraryLoading = false }
        }
    }
    fun loadSources() {
        // A new authoritative source read supersedes directory work already in flight.
        if (!state.showingDetail) { catalogue.cancel(); state.interruptCatalogue() }
        sourceRead.start(scope) { ticket ->
            state.sourcesLoading = true; state.sourcesError = ""
            try {
                val value = online.sources()
                coroutineContext.ensureActive()
                if (alive() && sourceRead.current(ticket)) {
                    if (state.acceptSources(value)) { catalogue.cancel(); itemRead.cancel(); playRead.cancel() }
                    loadHealth()
                }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { if (alive() && sourceRead.current(ticket)) state.sourcesError = onlineErrorMessage(e) }
            finally { if (alive() && sourceRead.current(ticket)) state.sourcesLoading = false }
        }
    }
    fun resetDetail() {
        catalogue.cancel(); itemRead.cancel(); playRead.cancel()
        state.interruptCatalogue()
        state.detail = null; state.detailSourceId = ""; state.detailExternalId = ""; state.detailError = ""; state.item = null
        state.detailLoading = false; state.starting = false
        detailRetryFocusFrom = null
        state.detailUi.reset()
    }
    fun changeSource(id: String) {
        if (state.sourceId == id) return
        savePosition(); resetDetail(); catalogue.cancel()
        state.sourceId = id; state.results = null; state.searchError = ""; state.searchPage = 1; state.searching = false
        state.categories = emptyList(); state.categoryId = ""; state.year = ""; state.yearInput = ""; state.query = ""
        state.categoryRoot = ""; state.channelSelections = emptyMap()
        state.loadedSelection = null; state.cataloguePending = true
        loadHealth()
    }
    fun changeQuery(value: String) {
        val query = onlineSearchInput(value)
        if (query == state.query) return
        state.query = query
        state.pendingFocus = null
        if (state.searching) {
            catalogue.cancel(); state.searching = false; state.searchError = ""; state.results = null; state.searchPage = 1
        }
        // Completed results still name their submitted loadedQuery until an
        // explicit new search; typing itself never requests the provider.
    }
    fun search(page: Int = 1, selection: OnlineCatalogSelection? = null, focusResults: Boolean = true,
               returnFocus: String? = null) {
        val source = selection?.sourceId ?: state.sourceId
        val selected = state.sources?.items?.firstOrNull { it.id == source } ?: return
        val request = selection ?: OnlineCatalogSelection(source, state.query.trim(), state.categoryId, state.year)
        val query = request.query
        if (source != state.sourceId || !selected.enabled || state.sources?.enabled != true || state.sourcesLoading || state.sourcesError.isNotBlank() ||
            query.isBlank() && !selected.browseSupported && selected.provider != "open_catalog") return
        savePosition(); resetDetail(); state.keyboard = false
        state.cataloguePending = false
        if (returnFocus != null) target(returnFocus)
        val requestedFocus = OnlineFocusTicket(focusVersion[0], memory.key, memory.revision)
        catalogue.start(scope) { ticket ->
            state.searching = true; state.searchError = ""; state.searchPage = page; state.loadedQuery = query
            state.loadedSelection = request; state.results = null
            try {
                val value = if (request.browsing && selected.browseSupported)
                    online.browse(source, page, request.categoryId, request.year) else online.search(source, query, page)
                coroutineContext.ensureActive()
                if (alive() && catalogue.current(ticket) && source == state.sourceId) {
                    state.results = value
                    if (value.categories.isNotEmpty()) state.categories = value.categories
                    if (requestedFocus.isCurrent(focusVersion[0], memory.key, memory.revision)) {
                        if (focusResults) target(value.items.firstOrNull()?.let { "online:result:${it.externalId}" } ?: "online:query")
                        else if (returnFocus != null) target(returnFocus)
                    }
                }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { if (alive() && catalogue.current(ticket)) {
                state.searchError = onlineErrorMessage(e)
                if (requestedFocus.isCurrent(focusVersion[0], memory.key, memory.revision)) target(returnFocus ?: "online:retry-search")
            } }
            finally { if (alive() && catalogue.current(ticket)) { state.searching = false; loadHealth() } }
        }
    }
    fun play(entry: OnlineEntry) {
        val detail = state.detail ?: return
        val identity = entry.identity ?: return
        if (!entry.supported || state.starting || state.sourcesLoading || state.sources?.enabled != true ||
            state.sources?.items?.none { it.id == identity.sourceId && it.enabled } != false) return
        savePosition()
        playRead.start(scope) { ticket ->
            state.starting = true; state.notice = ""
            try {
                val current = try { online.playbackState(identity) }
                catch (e: CancellationException) { throw e }
                catch (e: ApiException) {
                    if (alive()) state.notice = onlineErrorMessage(e)
                    if (e.status == 401 || e.status == 403) return@start
                    null
                }
                catch (e: Exception) { if (alive()) state.notice = onlineErrorMessage(e); null }
                coroutineContext.ensureActive()
                if (!alive() || !playRead.current(ticket) || state.detail?.item?.id != detail.item.id) return@start
                if (current == null) state.notice = "暂时无法读取观看记录，本次从头播放。"
                val selection = onlinePlaybackSelection(entry, current)
                if (selection == null) { state.notice = onlineAvailabilityReason(current?.reason.orEmpty()); return@start }
                if (current != null) state.item = current
                onPlay(OnlinePlaybackRequest(identity, detail.item.title + if (identity.kind == "episode") " · ${entry.label}" else "",
                    state.detailExternalId, selection.position, selection.scopeToken, selection.writerRevision, state.autoplay))
            } finally { if (alive() && playRead.current(ticket)) state.starting = false }
        }
    }
    fun openDetail(source: String, externalId: String, resume: OnlineIdentity? = null) {
        savePosition()
        val focusBeforeRequest = OnlineFocusTicket(focusVersion[0], memory.key, memory.revision)
        detailRetryFocusFrom = null
        if (state.sourceId != source) changeSource(source)
        if (state.detailSourceId != source || state.detailExternalId != externalId) state.detailUi.reset()
        itemRead.cancel(); playRead.cancel()
        state.detailSourceId = source; state.detailExternalId = externalId; state.detail = null; state.item = null; state.detailError = ""; state.notice = ""
        state.pendingFocus = null
        catalogue.start(scope) { ticket ->
            state.detailLoading = true
            try {
                val value = online.detail(source, externalId)
                coroutineContext.ensureActive()
                if (!alive() || !catalogue.current(ticket) || state.detailSourceId != source || state.detailExternalId != externalId) return@start
                detailInitialFocusAllowed = focusBeforeRequest.isCurrent(focusVersion[0], memory.key, memory.revision)
                state.detail = value
                state.lineId = resume?.lineId?.takeIf { id -> value.lines.any { it.id == id } }
                    ?: value.lines.firstOrNull { it.entries.any { entry -> entry.supported } }?.id
                    ?: value.lines.firstOrNull()?.id.orEmpty()
                loadItem()
                val entry = resume?.let { onlinePrepareRecordResume(state.detailUi, value, it) }
                if (resume != null && entry == null) state.notice = "上次线路或分集已变化，请重新选择。"
                if (entry != null) play(entry)
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { if (alive() && catalogue.current(ticket)) {
                state.detailError = onlineErrorMessage(e)
                detailRetryFocusFrom = focusBeforeRequest
            } }
            finally { if (alive() && catalogue.current(ticket)) { state.detailLoading = false; loadHealth() } }
        }
    }
    fun favorite(id: String, value: Boolean, frozenScope: String) {
        if (id in state.mutating || frozenScope.isBlank()) return
        if (!state.showingDetail) target("online:tab:${state.section}")
        state.mutating += id
        scope.launch {
            try {
                online.favorite(id, value, frozenScope)
                coroutineContext.ensureActive()
                if (alive()) { state.notice = ""; loadItem(); loadLibrary() }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { if (alive()) state.notice = onlineErrorMessage(e) }
            finally { if (alive()) state.mutating -= id }
        }
    }
    fun deleteHistory(row: OnlineLibraryEntry, frozenScope: String) {
        val id = row.item.id
        if (id in state.mutating || frozenScope.isBlank()) return
        if (!state.showingDetail) target("online:tab:${state.section}")
        state.mutating += id
        scope.launch {
            try {
                val result = online.deleteHistory(id, frozenScope)
                coroutineContext.ensureActive()
                if (alive()) {
                    state.library = state.library?.copy(scopeToken = result.scopeToken)
                    state.item = state.item?.copy(scopeToken = result.scopeToken)
                    state.notice = "已清除当前模式下此内容的观看记录。旧播放页需重新播放后才能保存。"
                    loadItem(); loadLibrary()
                }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { if (alive()) state.notice = onlineErrorMessage(e) }
            finally { if (alive()) state.mutating -= id }
        }
    }
    fun selectSection(section: String) {
        if (state.section == section && !state.showingDetail) return
        savePosition(); resetDetail(); libraryRead.cancel()
        state.section = section; state.library = null; state.libraryOffset = 0; state.libraryError = ""; state.notice = ""
        target("online:tab:$section")
        if (section != "search") loadLibrary(0)
    }

    LaunchedEffect(api, refresh) {
        state.autoplay = preferences.getBoolean(preferenceKey, false)
        loadSources(); loadLibrary()
        if (state.showingDetail && state.detail == null && state.detailError.isBlank())
            openDetail(state.detailSourceId, state.detailExternalId)
        else loadItem()
    }
    LaunchedEffect(state.sourceId, state.sourcesLoading, state.cataloguePending, state.section, state.showingDetail) {
        if (!state.sourcesLoading && state.cataloguePending && state.section == "search" && !state.showingDetail && state.sourcesError.isBlank()) {
            state.cataloguePending = false
            val source = state.sources?.items?.firstOrNull { it.id == state.sourceId && it.enabled }
            if (state.sources?.enabled == true && source != null && (source.browseSupported || state.loadedSelection != null))
                search(state.searchPage, state.loadedSelection, focusResults = false)
        }
    }
    val contentKey = state.contentKey
    LaunchedEffect(contentKey) {
        if (state.showingDetail) return@LaunchedEffect
        val position = state.position(contentKey)
        withFrameNanos { }
        list.scrollToItem(position.index, position.offset)
    }
    LaunchedEffect(contentKey, list) {
        if (state.showingDetail) return@LaunchedEffect
        snapshotFlow { list.firstVisibleItemIndex to list.firstVisibleItemScrollOffset }.collect { (index, offset) ->
            state.savePosition(contentKey, state.position(contentKey).copy(index = index, offset = offset))
        }
    }
    LaunchedEffect(contentKey, restoreAttempt, state.detailLoading, state.searching, state.libraryLoading, state.sourcesLoading) {
        if (state.showingDetail) return@LaunchedEffect
        val wanted = state.pendingFocus ?: return@LaunchedEffect
        val ticket = pendingFocusTicket ?: run { state.pendingFocus = null; return@LaunchedEffect }
        fun stillCurrent() = state.pendingFocus == wanted && pendingFocusTicket === ticket &&
            ticket.isCurrent(focusVersion[0], memory.key, memory.revision)
        fun discard() { if (state.pendingFocus == wanted && pendingFocusTicket === ticket) { state.pendingFocus = null; pendingFocusTicket = null } }
        if (!stillCurrent()) { discard(); return@LaunchedEffect }
        val filter = wanted.startsWith("online:root:") || wanted.startsWith("online:category:") || wanted.startsWith("online:year:")
        if (state.detailLoading || state.libraryLoading || state.searching && !filter) return@LaunchedEffect
        withFrameNanos { }
        if (!stillCurrent()) { discard(); return@LaunchedEffect }
        val index = onlineFocusItemIndex(state, wanted)
        if (index != null && list.layoutInfo.visibleItemsInfo.none { it.index == index }) {
            list.scrollToItem(index)
            withFrameNanos { }
        }
        if (!stillCurrent()) { discard(); return@LaunchedEffect }
        val restored = runCatching { focus[wanted]?.requestFocus() == true }.getOrDefault(false)
        if (restored) { state.pendingFocus = null; pendingFocusTicket = null }
        else if (!state.sourcesLoading) {
            list.scrollToItem(0)
            withFrameNanos { }
            if (!stillCurrent()) { discard(); return@LaunchedEffect }
            if (runCatching { focus["online:tab:${state.section}"]?.requestFocus() == true }.getOrDefault(false)) {
                state.pendingFocus = null; pendingFocusTicket = null
            }
        }
    }
    DisposableEffect(state, api) {
        onDispose {
            if (!state.showingDetail) savePosition()
            state.detach(lifetime)
            catalogue.cancel(); sourceRead.cancel(); itemRead.cancel(); libraryRead.cancel(); healthRead.cancel(); playRead.cancel()
        }
    }
    BackHandler {
        when {
            sourceDialog -> { sourceDialog = false; target("online:source-button") }
            categoryDialog != null -> { categoryDialog = null; target(categoryReturn) }
            yearDialog -> { yearDialog = false; target("online:year:other") }
            state.showingDetail -> {
                savePosition(); resetDetail()
                val old = state.position(state.contentKey).focus
                if (old.isNotBlank()) target(old)
            }
            state.keyboard -> { state.keyboard = false; target("online:keyboard") }
            else -> back()
        }
    }

    val selectedSource = state.sources?.items?.firstOrNull { it.id == state.sourceId }
    val enabled = state.sources?.enabled == true
    val detail = state.detail
    val playableSource = enabled && !state.sourcesLoading && state.sourcesError.isBlank() &&
        state.sources?.items?.any { it.id == detail?.source?.id && it.enabled } == true
    if (state.showingDetail) {
        when {
            state.detailLoading -> Status("正在读取线路与分集…", icon = "loading")
            state.detailError.isNotBlank() -> {
                val originalFocus = detailRetryFocusFrom
                LaunchedEffect(state.detailError, originalFocus) {
                    if (originalFocus != null && originalFocus.isCurrent(focusVersion[0], memory.key, memory.revision)) {
                        withFrameNanos { }
                        if (originalFocus.isCurrent(focusVersion[0], memory.key, memory.revision))
                            runCatching { focus["online:retry-detail"]?.requestFocus() }
                    }
                }
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Status(state.detailError, icon = "error")
                    TvAction("重试详情", { openDetail(state.detailSourceId, state.detailExternalId) },
                        Modifier.onlineFocus("online:retry-detail"), icon = "refresh")
                }
            }
            detail != null -> OnlineDetailScreen(api, detail, state.item, state.lineId, state.autoplay, state.starting,
                playableSource, detail.item.id in state.mutating, state.notice, state.detailUi, memory, detailInitialFocusAllowed,
                onSelectLine = { id -> state.detailUi.markUserInteraction(); playRead.cancel(); state.starting = false; state.lineId = id },
                onPlay = ::play,
                onFavorite = { item -> favorite(detail.item.id, !item.favorite, item.scopeToken) },
                onAutoplayChange = { value -> state.autoplay = value; preferences.edit().putBoolean(preferenceKey, value).apply() })
            else -> Status("正在读取线路与分集…", icon = "loading")
        }
        return
    }
    val tree = remember(state.categories) { onlineTaxonomy(state.categories) }
    fun submitSearch() {
        val query = state.query.trim()
        if (query.isBlank() && !(selectedSource?.provider == "open_catalog" && !selectedSource.browseSupported)) return
        state.categoryId = ""; state.categoryRoot = ""; state.year = ""; state.yearInput = ""; state.keyboard = false
        search(selection = OnlineCatalogSelection(state.sourceId, query))
    }
    fun selectCategory(id: String, returnKey: String) {
        val selectedRoot = tree.rootByCategory[id].orEmpty()
        state.categoryId = id; state.categoryRoot = selectedRoot; state.query = ""
        if (selectedRoot.isNotBlank()) state.channelSelections = state.channelSelections + (selectedRoot to id)
        search(selection = OnlineCatalogSelection(state.sourceId, categoryId = id, year = state.year),
            focusResults = false, returnFocus = returnKey)
    }
    fun selectYear(year: String, returnKey: String) {
        state.year = year; state.yearInput = year; state.query = ""
        search(selection = OnlineCatalogSelection(state.sourceId, categoryId = state.categoryId, year = year),
            focusResults = false, returnFocus = returnKey)
    }
    BoxWithConstraints(Modifier.fillMaxSize()) {
    val fontScale = LocalDensity.current.fontScale.coerceAtLeast(1f)
    val columns = ((maxWidth.value - 16f) / (150f * fontScale + 16f)).toInt().coerceIn(1, 8)
    SideEffect { state.resultColumns = columns }
    LazyColumn(state = list, modifier = Modifier.fillMaxSize(), contentPadding = PaddingValues(8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)) {
        item(key = "toolbar") {
            OnlineBrowseToolbar(state.section, state.query, selectedSource,
                enabled && !state.sourcesLoading && state.sourcesError.isBlank() && selectedSource != null,
                onSection = ::selectSection, onQuery = ::changeQuery, onSearch = ::submitSearch,
                onKeyboard = { state.keyboard = true }, onSource = { sourceDialog = true },
                focus = { key -> Modifier.onlineFocus(key) })
        }
        if (state.notice.isNotBlank()) item(key = "notice") { Status(state.notice) }
        if (state.section == "search") {
            when {
                state.sourcesLoading -> item { Status("正在读取视频源…", icon = "loading") }
                state.sourcesError.isNotBlank() -> item {
                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Status(state.sourcesError, icon = "error")
                        TvAction("重试视频源", ::loadSources, Modifier.onlineFocus("online:retry-sources"), icon = "refresh")
                    }
                }
                !enabled -> item { Status("在线点播尚未启用。请在网页“设置 → 在线视频源”启用并配置；已有记录仍可管理。") }
                state.sources?.items?.none { it.enabled } != false -> item { Status("还没有可用的视频源，请先在网页添加来源。") }
                else -> {
                    if (selectedSource?.browseSupported == true) item(key = "filters") {
                        OnlineFilterRows(tree, state.categoryRoot, state.categoryId, state.year, selectedSource.yearFilterSupported,
                            onRoot = { id ->
                                if (id.isBlank()) selectCategory("", "online:root:all")
                                else tree.channels.firstOrNull { it.id == id }?.let { channel ->
                                    selectCategory(onlineChannelCategory(channel, state.channelSelections[id].orEmpty()), "online:root:$id")
                                }
                            },
                            onCategory = { id -> selectCategory(id, "online:category:$id") },
                            onMore = { root ->
                                categoryReturn = if (root.isBlank()) "online:more-roots" else "online:more-categories"
                                categoryDialog = root
                            },
                            onYear = { year -> selectYear(year, "online:year:${year.ifBlank { "all" }}") },
                            onOtherYear = { state.yearInput = state.year; yearError = ""; yearDialog = true },
                            focus = { key -> Modifier.onlineFocus(key) })
                    }
                    when {
                        state.searching -> item { Status(if (state.loadedSelection?.browsing == true) "正在加载第 ${state.searchPage} 页目录…" else "正在搜索当前来源…", icon = "loading") }
                        state.searchError.isNotBlank() -> item {
                            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                Status(state.searchError, icon = "error")
                                TvAction("重试加载", { search(state.searchPage, state.loadedSelection) }, Modifier.onlineFocus("online:retry-search"), icon = "refresh")
                            }
                        }
                        state.results == null -> item { Status(if (selectedSource?.provider == "open_catalog" && !selectedSource.browseSupported)
                            "选择“目录”查看开放样例及许可。" else "输入片名，搜索当前来源。") }
                        state.results!!.items.isEmpty() -> item { Status(if (state.loadedSelection?.browsing == true)
                            "当前分类或年份暂无内容，请更换筛选或来源。" else "没有找到相关内容，请更换片名或来源。") }
                        else -> {
                            item {
                                Text(listOf(state.loadedQuery.takeIf { it.isNotBlank() }?.let { "搜索：$it" }
                                    ?: onlineTaxonomyLabel(tree, state.loadedSelection?.categoryId.orEmpty()),
                                    state.loadedSelection?.year?.takeIf { it.isNotBlank() && state.loadedSelection?.browsing == true },
                                    "${state.results!!.total} 项", "第 ${state.results!!.page} 页")
                                    .filterNotNull().joinToString(" · "), color = Muted, maxLines = 2, overflow = TextOverflow.Ellipsis)
                            }
                            items(state.results!!.items.chunked(columns), key = { "result-row:${it.first().externalId}" }) { row ->
                                Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp), horizontalArrangement = Arrangement.spacedBy(16.dp), verticalAlignment = Alignment.Top) {
                                    row.forEach { item ->
                                        OnlineCatalogCard(api, item, { openDetail(state.sourceId, item.externalId) },
                                            Modifier.weight(1f).onlineFocus("online:result:${item.externalId}"))
                                    }
                                    repeat(columns - row.size) { androidx.compose.foundation.layout.Spacer(Modifier.weight(1f)) }
                                }
                            }
                        }
                    }
                    state.results?.takeIf { it.pageCount > 1 && !state.searching && state.searchError.isBlank() }?.let { result -> item(key = "search-pagination") {
                        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                            TvAction("上一页", { search(result.page - 1, state.loadedSelection) }, Modifier.onlineFocus("online:previous-search"), enabled = result.page > 1, icon = "back")
                            Text("第 ${result.page} / ${result.pageCount} 页", color = Muted)
                            TvAction("下一页", { search(result.page + 1, state.loadedSelection) }, Modifier.onlineFocus("online:next-search"), enabled = result.page < result.pageCount && result.page < 1000, icon = "forward")
                        }
                    } }
                }
            }
        } else {
            when {
                state.libraryLoading -> item { Status("正在读取在线记录…", icon = "loading") }
                state.libraryError.isNotBlank() -> item {
                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Status(state.libraryError, icon = "error")
                        TvAction("重试记录", { loadLibrary() }, Modifier.onlineFocus("online:retry-library"), icon = "refresh")
                    }
                }
                state.library?.items.isNullOrEmpty() -> item { Status(when (state.section) {
                    "favorites" -> "还没有收藏的在线内容；打开详情后可收藏到本电视。"
                    "history" -> "还没有观看历史；实际开始播放后会保存进度。"
                    else -> "暂时没有继续观看的内容。"
                }) }
                else -> items(state.library!!.items, key = { "record:${it.item.id}" }) { row ->
                    Column(Modifier.fillMaxWidth().background(Panel, RoundedCornerShape(10.dp)).padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(row.item.title, style = MaterialTheme.typography.titleMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        Text(row.source.name, color = Muted)
                        row.progress?.let { progress -> Text(if (progress.completed) "已播放完毕" else "上次播放到 ${playbackTime(progress.position)}" +
                            (progress.duration?.let { " / ${playbackTime(it)}" } ?: ""), color = Muted) }
                        if (!row.available) Text(onlineAvailabilityReason(row.reason), color = Muted)
                        LazyRow(horizontalArrangement = Arrangement.spacedBy(10.dp), contentPadding = PaddingValues(8.dp)) {
                            row.progress?.let { progress -> item {
                                TvAction(if (progress.completed) "重新播放" else "继续播放", { openDetail(row.source.id, row.item.externalId, progress.identity) },
                                    Modifier.onlineFocus("online:record:${row.item.id}:resume"), enabled = row.available && !state.sourcesLoading && !state.starting, icon = "play")
                            } }
                            item { TvAction("查看详情", { openDetail(row.source.id, row.item.externalId) }, Modifier.onlineFocus("online:record:${row.item.id}:detail"),
                                enabled = row.available && !state.sourcesLoading, icon = "forward") }
                            if (row.favorite) item { TvAction("取消收藏", { favorite(row.item.id, false, state.library!!.scopeToken) },
                                Modifier.onlineFocus("online:record:${row.item.id}:favorite"), enabled = row.item.id !in state.mutating, icon = "heart-filled") }
                            if (row.progress != null) item { TvAction("清除观看记录", { deleteHistory(row, state.library!!.scopeToken) },
                                Modifier.onlineFocus("online:record:${row.item.id}:clear"), enabled = row.item.id !in state.mutating, icon = "trash") }
                        }
                    }
                }
            }
            state.library?.takeIf { it.total > 20 && !state.libraryLoading && state.libraryError.isBlank() }?.let { library -> item(key = "library-pagination") {
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                    TvAction("上一页", { savePosition(); loadLibrary((state.libraryOffset - 20).coerceAtLeast(0)); target("online:previous-library") },
                        Modifier.onlineFocus("online:previous-library"), enabled = state.libraryOffset > 0, icon = "back")
                    Text("第 ${state.libraryOffset / 20 + 1} 页 · ${library.total} 项", color = Muted)
                    TvAction("下一页", { savePosition(); loadLibrary(state.libraryOffset + 20); target("online:next-library") },
                        Modifier.onlineFocus("online:next-library"), enabled = state.libraryOffset + 20 < library.total && state.libraryOffset + 20 <= 1000, icon = "forward")
                }
            } }
        }
    }
    }
    if (sourceDialog) OnlineSourceDialog(state.sources?.items.orEmpty(), state.sourceId, state.health, state.healthLoading,
        state.healthError, onSelect = { id -> sourceDialog = false; changeSource(id); target("online:source-button") },
        onRefresh = ::loadHealth, onClose = { sourceDialog = false; target("online:source-button") })
    categoryDialog?.let { root -> OnlineCategoryDialog(onlineAllCategoriesForChannel(tree, root), state.categoryId, onSelect = { id ->
        categoryDialog = null
        val categoryRoot = tree.rootByCategory[id].orEmpty()
        val channel = tree.channels.firstOrNull { it.id == categoryRoot }
        val wanted = when {
            id.isBlank() -> "online:root:all"
            channel?.children.isNullOrEmpty() -> "online:root:$categoryRoot"
            else -> "online:category:$id"
        }
        selectCategory(id, wanted)
    }, onClose = { categoryDialog = null; target(categoryReturn) }) }
    if (state.keyboard) OnlineSearchKeyboardDialog(state.query, ::changeQuery, onSubmit = ::submitSearch,
        onClose = { state.keyboard = false; target("online:keyboard") })
    if (yearDialog) OnlineYearDialog(state.yearInput,
        onValue = { value -> state.yearInput = value.filter { it in '0'..'9' }.take(4); yearError = "" },
        onApply = {
            if (state.yearInput.isNotEmpty() && !Regex("[1-9][0-9]{3}").matches(state.yearInput)) yearError = "请输入四位年份，或留空查看全部。"
            else { val year = state.yearInput; yearDialog = false; selectYear(year, if (year.isBlank()) "online:year:all" else "online:year:other") }
        }, onClose = { yearDialog = false; target("online:year:other") }, error = yearError)
}
