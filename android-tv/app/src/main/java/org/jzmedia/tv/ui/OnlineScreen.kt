package org.jzmedia.tv.ui

import android.content.Context
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
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
import org.jzmedia.tv.data.TV_SEARCH_KEYS
import org.jzmedia.tv.data.deleteSearchInput
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

/** Native TV catalogue: only stable identities leave the page; no source artwork is fetched. */
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
    val sourceStrip = rememberLazyListState()
    val lineStrip = rememberLazyListState()
    val focus = remember { mutableMapOf<String, FocusRequester>() }
    var restoreAttempt by remember { mutableStateOf(0) }
    var descriptionDialog by remember { mutableStateOf(false) }
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
    fun target(key: String) { state.pendingFocus = key; restoreAttempt++ }
    fun Modifier.onlineFocus(key: String): Modifier = focusRequester(focus.getOrPut(key) { FocusRequester() })
        .onFocusChanged { if (it.isFocused) {
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
        itemRead.start(scope) { ticket ->
            try {
                val value = online.itemState(detail.item.id)
                coroutineContext.ensureActive()
                if (alive() && itemRead.current(ticket) && state.detail?.item?.id == detail.item.id) state.item = value
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
        sourceRead.start(scope) { ticket ->
            state.sourcesLoading = true; state.sourcesError = ""
            try {
                val value = online.sources()
                coroutineContext.ensureActive()
                if (alive() && sourceRead.current(ticket)) {
                    state.sources = value
                    if (value.items.none { it.id == state.sourceId && it.enabled }) {
                        val previous = state.sourceId
                        state.sourceId = value.items.firstOrNull { it.enabled }?.id.orEmpty()
                        if (previous != state.sourceId) state.results = null
                    }
                    loadHealth()
                }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { if (alive() && sourceRead.current(ticket)) state.sourcesError = onlineErrorMessage(e) }
            finally { if (alive() && sourceRead.current(ticket)) state.sourcesLoading = false }
        }
    }
    fun resetDetail() {
        catalogue.cancel(); itemRead.cancel(); playRead.cancel()
        state.detail = null; state.detailSourceId = ""; state.detailExternalId = ""; state.detailError = ""; state.item = null
        state.detailLoading = false; state.starting = false
    }
    fun changeSource(id: String) {
        if (state.sourceId == id) return
        savePosition(); resetDetail(); catalogue.cancel()
        state.sourceId = id; state.results = null; state.searchError = ""; state.searchPage = 1; state.searching = false
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
    fun search(page: Int = 1) {
        val source = state.sourceId
        val selected = state.sources?.items?.firstOrNull { it.id == source } ?: return
        val query = state.query.trim()
        if (state.sources?.enabled != true || state.sourcesLoading || query.isBlank() && selected.provider != "open_catalog") return
        savePosition(); resetDetail(); state.keyboard = false
        catalogue.start(scope) { ticket ->
            state.searching = true; state.searchError = ""; state.searchPage = page; state.loadedQuery = query; state.results = null
            try {
                val value = online.search(source, query, page)
                coroutineContext.ensureActive()
                if (alive() && catalogue.current(ticket) && source == state.sourceId) {
                    state.results = value
                    target(value.items.firstOrNull()?.let { "online:result:${it.externalId}" } ?: "online:search")
                }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { if (alive() && catalogue.current(ticket)) { state.searchError = onlineErrorMessage(e); target("online:retry-search") } }
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
        if (state.sourceId != source) changeSource(source)
        itemRead.cancel(); playRead.cancel()
        state.detailSourceId = source; state.detailExternalId = externalId; state.detail = null; state.item = null; state.detailError = ""; state.notice = ""
        state.pendingFocus = null
        catalogue.start(scope) { ticket ->
            state.detailLoading = true
            try {
                val value = online.detail(source, externalId)
                coroutineContext.ensureActive()
                if (!alive() || !catalogue.current(ticket) || state.detailSourceId != source || state.detailExternalId != externalId) return@start
                state.detail = value
                state.lineId = resume?.lineId?.takeIf { id -> value.lines.any { it.id == id } }
                    ?: value.lines.firstOrNull { it.entries.any { entry -> entry.supported } }?.id
                    ?: value.lines.firstOrNull()?.id.orEmpty()
                loadItem()
                val entry = resume?.let(value::findEntry)
                if (resume != null && entry == null) state.notice = "上次线路或分集已变化，请重新选择。"
                val first = value.lines.firstOrNull { it.id == state.lineId }?.entries?.firstOrNull { it.supported }
                target(first?.identity?.let { "online:entry:${it.episodeId ?: it.lineId}" } ?: "online:detail-favorite")
                if (entry != null) play(entry)
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { if (alive() && catalogue.current(ticket)) { state.detailError = onlineErrorMessage(e); target("online:retry-detail") } }
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
    val contentKey = state.contentKey
    LaunchedEffect(contentKey) {
        descriptionDialog = false
        val position = state.position(contentKey)
        withFrameNanos { }
        list.scrollToItem(position.index, position.offset)
        if (state.hasMounted && position.focus.isNotBlank()) target(position.focus)
        state.hasMounted = true
    }
    LaunchedEffect(contentKey, list) {
        snapshotFlow { list.firstVisibleItemIndex to list.firstVisibleItemScrollOffset }.collect { (index, offset) ->
            state.savePosition(contentKey, state.position(contentKey).copy(index = index, offset = offset))
        }
    }
    LaunchedEffect(contentKey, restoreAttempt, state.detailLoading, state.searching, state.libraryLoading, state.sourcesLoading) {
        val wanted = state.pendingFocus ?: return@LaunchedEffect
        if (state.detailLoading || state.searching || state.libraryLoading) return@LaunchedEffect
        withFrameNanos { }
        val index = onlineFocusItemIndex(state, wanted)
        if (index != null && list.layoutInfo.visibleItemsInfo.none { it.index == index }) {
            list.scrollToItem(index)
            withFrameNanos { }
        }
        if (state.showingDetail && wanted.startsWith("online:line:")) {
            val line = state.detail?.lines?.indexOfFirst { "online:line:${it.id}" == wanted } ?: -1
            if (line >= 0) { lineStrip.scrollToItem(line); withFrameNanos { } }
        } else if (!state.showingDetail && state.section == "search" && wanted.startsWith("online:source:")) {
            val source = state.sources?.items?.filter { it.enabled }?.indexOfFirst { "online:source:${it.id}" == wanted } ?: -1
            if (source >= 0) { sourceStrip.scrollToItem(source); withFrameNanos { } }
        }
        val restored = runCatching { focus[wanted]?.requestFocus() == true }.getOrDefault(false)
        if (restored) state.pendingFocus = null
        else if (!state.sourcesLoading) {
            list.scrollToItem(1)
            withFrameNanos { }
            if (runCatching { focus["online:tab:${state.section}"]?.requestFocus() == true }.getOrDefault(false)) state.pendingFocus = null
        }
    }
    DisposableEffect(state, api) {
        onDispose {
            savePosition()
            state.detach(lifetime)
            catalogue.cancel(); sourceRead.cancel(); itemRead.cancel(); libraryRead.cancel(); healthRead.cancel(); playRead.cancel()
        }
    }
    BackHandler {
        when {
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
    LazyColumn(state = list, modifier = Modifier.fillMaxSize(), contentPadding = PaddingValues(8.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp)) {
        item(key = "heading") {
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text("在线", style = MaterialTheme.typography.headlineMedium)
                Text("本电视 · ${if (mode == "private") "隐私模式" else "普通模式"}；与本地媒体记录分开。", color = Muted)
            }
        }
        item(key = "tabs") {
            LazyRow(horizontalArrangement = Arrangement.spacedBy(10.dp), contentPadding = PaddingValues(8.dp)) {
                items(listOf("search" to "查找内容", "continue" to "继续观看", "favorites" to "我的收藏", "history" to "观看历史")) { (key, label) ->
                    TvAction(label, { selectSection(key) }, Modifier.onlineFocus("online:tab:$key"), selected = state.section == key)
                }
                item { TvAction("刷新", { loadSources(); loadLibrary(); loadItem() }, Modifier.onlineFocus("online:refresh"), icon = "refresh") }
            }
        }
        if (state.notice.isNotBlank()) item(key = "notice") { Status(state.notice) }
        if (state.showingDetail) {
            when {
                state.detailLoading -> item { Status("正在读取线路与分集…", icon = "loading") }
                state.detailError.isNotBlank() -> item {
                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Status(state.detailError, icon = "error")
                        TvAction("重试详情", { openDetail(state.detailSourceId, state.detailExternalId) }, Modifier.onlineFocus("online:retry-detail"), icon = "refresh")
                    }
                }
                detail != null -> {
                    item(key = "detail-header") {
                        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text(detail.item.title, style = MaterialTheme.typography.headlineMedium)
                            Text(listOf(detail.source.name, if (detail.item.kind == "tv") "剧集" else "电影", detail.year)
                                .filter(String::isNotBlank).joinToString(" · "), color = Muted)
                            if (detail.description.isNotBlank()) Text(detail.description, maxLines = 5, overflow = TextOverflow.Ellipsis)
                            if (detail.attribution.isNotEmpty()) Text("内容来源与许可：" + detail.attribution.joinToString(" · ") { it.label },
                                style = MaterialTheme.typography.bodySmall, color = Muted)
                            if (detail.description.isNotBlank() || detail.attribution.isNotEmpty()) TvAction("简介与来源", { descriptionDialog = true },
                                Modifier.onlineFocus("online:description"), icon = "info")
                            if (!playableSource && !state.sourcesLoading) Text("来源尚未启用或暂不可读；可返回管理已存记录。", color = Muted)
                        }
                    }
                    item(key = "detail-actions") {
                        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            val item = state.item
                            TvAction(if (item?.favorite == true) "取消收藏" else "收藏", {
                                if (item != null) favorite(detail.item.id, !item.favorite, item.scopeToken)
                            }, Modifier.onlineFocus("online:detail-favorite"), enabled = item != null && detail.item.id !in state.mutating,
                                icon = if (item?.favorite == true) "heart-filled" else "heart")
                            onlineResumeEntry(detail, state.lineId, item)?.let { entry ->
                                TvAction(if (item?.lastProgress?.completed == true) "重新播放" else "继续播放", { play(entry) },
                                    Modifier.onlineFocus("online:detail-resume"), enabled = playableSource && !state.starting, icon = "play")
                            }
                        }
                    }
                    item(key = "lines") {
                        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text("播放线路", style = MaterialTheme.typography.headlineSmall)
                            LazyRow(state = lineStrip, horizontalArrangement = Arrangement.spacedBy(10.dp), contentPadding = PaddingValues(8.dp)) {
                                items(detail.lines, key = { it.id }) { line ->
                                    TvAction(line.label, { playRead.cancel(); state.starting = false; state.lineId = line.id },
                                        Modifier.onlineFocus("online:line:${line.id}"), selected = state.lineId == line.id)
                                }
                            }
                            if (detail.item.kind == "tv") TvAction("自动播放下一集：${if (state.autoplay) "开启" else "关闭"}（本电视·当前模式）", {
                                state.autoplay = !state.autoplay
                                preferences.edit().putBoolean(preferenceKey, state.autoplay).apply()
                            }, Modifier.onlineFocus("online:autoplay"), selected = state.autoplay)
                            Text("由本电视解码；同源同线连续播放，不跳过片尾或不支持的下一集。", style = MaterialTheme.typography.bodySmall, color = Muted)
                            if (state.starting) Status("正在读取本分集的续播位置…", icon = "loading")
                        }
                    }
                    val line = detail.lines.firstOrNull { it.id == state.lineId }
                    if (line == null || line.entries.isEmpty()) item { Status("当前线路暂无可播放内容，请选择其他线路。") }
                    else items(line.entries.withIndex().toList(), key = { (index, entry) -> "entry:${line.id}:${entry.identity?.episodeId ?: index}" }) { (_, entry) ->
                        Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp), horizontalArrangement = Arrangement.spacedBy(16.dp), verticalAlignment = Alignment.CenterVertically) {
                            val key = entry.identity?.let { "online:entry:${it.episodeId ?: it.lineId}" } ?: "online:unsupported:${line.id}:${entry.label}"
                            TvAction(entry.label, { play(entry) }, Modifier.weight(1f).onlineFocus(key),
                                enabled = entry.supported && playableSource && !state.starting, icon = if (entry.supported) "play" else null, fillContent = true)
                            Text(if (entry.supported) "${entry.transport?.uppercase()} · 本电视播放" else if (entry.reason in listOf("host_not_allowed", "target_host_not_allowed"))
                                "请在网页为此来源允许媒体主机" else "此播放项暂不支持直链播放", color = Muted, modifier = Modifier.weight(1f))
                        }
                    }
                }
            }
        } else if (state.section == "search") {
            when {
                state.sourcesLoading -> item { Status("正在读取视频源…", icon = "loading") }
                state.sourcesError.isNotBlank() -> item {
                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Status(state.sourcesError, icon = "error")
                        TvAction("重试视频源", ::loadSources, Modifier.onlineFocus("online:retry-sources"), icon = "refresh")
                    }
                }
                !enabled -> item { Status("在线点播尚未启用。请在网页“设置 → 在线视频源”启用并配置；已有记录仍可管理。") }
                state.sources?.items.isNullOrEmpty() -> item { Status("还没有可用的视频源，请先在网页添加来源。") }
                else -> {
                    item(key = "sources") {
                        LazyRow(state = sourceStrip, horizontalArrangement = Arrangement.spacedBy(10.dp), contentPadding = PaddingValues(8.dp)) {
                            items(state.sources!!.items.filter { it.enabled }, key = { it.id }) { source ->
                                TvAction(source.name, { changeSource(source.id) }, Modifier.onlineFocus("online:source:${source.id}"), selected = source.id == state.sourceId)
                            }
                        }
                    }
                    item(key = "search") {
                        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Row(Modifier.fillMaxWidth().padding(8.dp), horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.Bottom) {
                                TvInput("片名（仅查询当前来源）", state.query, ::changeQuery, Modifier.weight(1f))
                                TvAction(if (selectedSource?.provider == "open_catalog" && state.query.isBlank()) "浏览目录" else "搜索", { search() },
                                    Modifier.onlineFocus("online:search"), enabled = !state.searching && (state.query.isNotBlank() || selectedSource?.provider == "open_catalog"), icon = "search")
                                TvAction("遥控字母键盘", { state.keyboard = !state.keyboard }, Modifier.onlineFocus("online:keyboard"), selected = state.keyboard)
                            }
                            Text("输入完整片名；可使用电视输入法。免费样例可直接浏览。", style = MaterialTheme.typography.bodySmall, color = Muted)
                        }
                    }
                    if (state.keyboard) item(key = "keyboard") {
                        Column(Modifier.padding(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                                TvAction("清空", { changeQuery("") }, Modifier.onlineFocus("online:key:clear"), icon = "trash")
                                TvAction("退格", { changeQuery(deleteSearchInput(state.query)) }, Modifier.onlineFocus("online:key:delete"), icon = "backspace")
                                TvAction("收起键盘", { state.keyboard = false; target("online:keyboard") }, Modifier.onlineFocus("online:key:close"))
                            }
                            TV_SEARCH_KEYS.chunked(6).forEach { keys ->
                                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                                    keys.forEach { value ->
                                        TvAction(value, { changeQuery(state.query + value) }, Modifier.weight(1f).onlineFocus("online:key:$value"), centerLabel = true)
                                    }
                                }
                            }
                        }
                    }
                    item(key = "health") {
                        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Row(horizontalArrangement = Arrangement.spacedBy(14.dp), verticalAlignment = Alignment.CenterVertically) {
                                Text(if (state.healthLoading) "正在读取最近状态…" else state.health?.let { "API：${it.api.label}　媒体：${it.media.label}" }
                                    ?: state.healthError.ifBlank { "API／媒体：未观察" }, modifier = Modifier.weight(1f), color = Muted)
                                TvAction("刷新状态", ::loadHealth, Modifier.onlineFocus("online:health"), enabled = !state.healthLoading, icon = "refresh")
                            }
                            Text("只读最近实际请求，不主动探测；取流成功不等于本电视已可解码。", style = MaterialTheme.typography.bodySmall, color = Muted)
                        }
                    }
                    when {
                        state.searching -> item { Status("正在查询当前来源…", icon = "loading") }
                        state.searchError.isNotBlank() -> item {
                            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                Status(state.searchError, icon = "error")
                                TvAction("重试搜索", { search(state.searchPage) }, Modifier.onlineFocus("online:retry-search"), icon = "refresh")
                            }
                        }
                        state.results == null -> item { Status(if (selectedSource?.provider == "open_catalog") "选择“浏览目录”查看开放样例及许可。" else "输入片名，仅向所选视频源发起查询。") }
                        state.results!!.items.isEmpty() -> item { Status("没有找到相关内容，请更换片名或来源。") }
                        else -> {
                            item { Text(listOf(state.loadedQuery.takeIf { it.isNotBlank() }, "${state.results!!.total} 项", "第 ${state.results!!.page} 页")
                                .filterNotNull().joinToString(" · "), color = Muted, maxLines = 2, overflow = TextOverflow.Ellipsis) }
                            items(state.results!!.items, key = { "result:${it.externalId}" }) { row ->
                                Column(Modifier.fillMaxWidth().background(Panel, RoundedCornerShape(10.dp)).padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                    Text(row.title, style = MaterialTheme.typography.titleMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
                                    if (row.year.isNotBlank()) Text(row.year, color = Muted)
                                    if (row.description.isNotBlank()) Text(row.description, maxLines = 2, overflow = TextOverflow.Ellipsis, color = Muted)
                                    TvAction("查看详情", { openDetail(state.sourceId, row.externalId) }, Modifier.onlineFocus("online:result:${row.externalId}"), icon = "forward")
                                }
                            }
                        }
                    }
                    state.results?.takeIf { it.pageCount > 1 }?.let { result -> item(key = "search-pagination") {
                        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                            TvAction("上一页", { search(result.page - 1) }, Modifier.onlineFocus("online:previous-search"), enabled = result.page > 1, icon = "back")
                            Text("第 ${result.page} / ${result.pageCount} 页", color = Muted)
                            TvAction("下一页", { search(result.page + 1) }, Modifier.onlineFocus("online:next-search"), enabled = result.page < result.pageCount && result.page < 1000, icon = "forward")
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
    if (descriptionDialog && detail != null) TextDialog(detail.item.title,
        listOf(detail.description).filter(String::isNotBlank) + detail.attribution.map { "${it.label}\n${it.url}" },
        onClose = { descriptionDialog = false; target("online:description") })
}
