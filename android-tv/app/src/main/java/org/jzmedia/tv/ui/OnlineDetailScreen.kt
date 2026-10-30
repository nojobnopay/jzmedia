package org.jzmedia.tv.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.gestures.BringIntoViewSpec
import androidx.compose.foundation.gestures.LocalBringIntoViewSpec
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.tv.material3.Button
import androidx.tv.material3.LocalContentColor
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.OnlineDetail
import org.jzmedia.tv.data.OnlineEntry
import org.jzmedia.tv.data.OnlineIdentity
import org.jzmedia.tv.data.OnlineItemState
import org.jzmedia.tv.ui.generated.DesignTokens

internal const val ONLINE_EPISODE_GROUP_SIZE = 50

internal fun onlineDetailPosterWidthDp(widthDp: Float): Float = if (widthDp < 700f) 96f else 120f
internal fun onlineDetailInitialFocusKey(hasPrimary: Boolean, playableSource: Boolean, starting: Boolean): String =
    if (hasPrimary && playableSource && !starting) "detail:play" else "detail:more"

internal fun onlineDetailFocusAllowed(lastKey: String, pendingKey: String?, allowInitialFocus: Boolean): Boolean =
    pendingKey != null || lastKey.isNotBlank() || allowInitialFocus

internal fun onlineDetailFocusCurrent(expectedDetail: Long, actualDetail: Long, expectedShared: Long, actualShared: Long): Boolean =
    expectedDetail == actualDetail && expectedShared == actualShared

internal fun onlineDetailScrollDistance(offset: Float, size: Float, viewport: Float, headerHeight: Float,
                                        margin: Float, focusKey: String): Float = seasonFocusScrollDistance(
    offset, size, viewport, headerHeight, margin,
    focusKey.startsWith("detail:entry:") || focusKey.startsWith("detail:unavailable:"),
)

/** Ranges always refer to original provider positions, including unavailable entries. */
internal data class OnlineEpisodeGroup(val index: Int, val start: Int, val end: Int, val indices: List<Int>) {
    val label: String get() = "$start–$end"
}

internal fun onlineEpisodeGroups(entries: List<OnlineEntry>, reversed: Boolean): List<OnlineEpisodeGroup> {
    val groups = entries.indices.chunked(ONLINE_EPISODE_GROUP_SIZE).mapIndexed { index, positions ->
        OnlineEpisodeGroup(index, positions.first() + 1, positions.last() + 1,
            if (reversed) positions.reversed() else positions)
    }
    return if (reversed) groups.reversed() else groups
}

internal fun onlineEpisodeIndex(entries: List<OnlineEntry>, identity: OnlineIdentity?): Int =
    if (identity == null) -1 else entries.indexOfFirst { it.supported && it.identity == identity }

internal fun onlineEpisodeKey(lineId: String, entry: OnlineEntry, index: Int): String =
    entry.identity?.let { "detail:entry:$lineId:${it.episodeId ?: "movie"}" }
        ?: "detail:unavailable:$lineId:$index"

internal fun onlineEntryUnavailableReason(entry: OnlineEntry): String = when (entry.reason) {
    "host_not_allowed", "target_host_not_allowed" -> "媒体主机尚未加入此来源允许列表"
    else -> "当前线路不支持直接播放，请选择其他线路"
}

/** A late record may suggest a line only while the viewer has not acted. */
internal fun onlineResumeLineCandidate(
    detail: OnlineDetail, item: OnlineItemState?, currentLine: String, starting: Boolean, userInteracted: Boolean,
): String? {
    val record = item ?: return null
    if (starting || userInteracted || !record.available || record.itemId != detail.item.id) return null
    val identity = record.lastProgress?.identity ?: return null
    if (identity.sourceId != detail.source.id || identity.itemId != detail.item.id ||
        identity.lineId == null || identity.lineId == currentLine ||
        (detail.item.kind == "tv") != (identity.kind == "episode")) return null
    return identity.lineId.takeIf { detail.findEntry(identity) != null }
}

/** Owned by the page, not SavedState; survives closing the local player. */
internal class OnlineDetailUiState {
    var groupIndex by mutableIntStateOf(0)
        private set
    var reversed by mutableStateOf(false)
        private set
    var focusedLabel by mutableStateOf("")
    var message by mutableStateOf("")
    var userInteracted by mutableStateOf(false)
        private set
    var lastFocusKey: String = ""
    var scrollIndex: Int = 0
    var scrollOffset: Int = 0
    private var boundItem: String = ""
    private var boundLine: String = ""
    internal var pendingFocus: String? = null
    internal var focusRevision by mutableIntStateOf(0)
        private set
    internal var focusVersion: Long = 0
    private var automaticFocusKey: String? = null
    private var lastFocusMemory: FocusMemory? = null
    private var lastSharedRevision: Long = 0
    private var pendingFocusMemory: FocusMemory? = null
    private var pendingSharedRevision: Long = 0

    fun reset() {
        groupIndex = 0; reversed = false; focusedLabel = ""; message = ""
        lastFocusKey = ""; scrollIndex = 0; scrollOffset = 0
        boundItem = ""; boundLine = ""; pendingFocus = null; focusRevision++; focusVersion++
        userInteracted = false; automaticFocusKey = null; lastFocusMemory = null; pendingFocusMemory = null
    }

    fun bind(itemId: String, lineId: String) {
        if (boundItem != itemId) {
            reset(); boundItem = itemId; boundLine = lineId
        } else if (boundLine != lineId) {
            boundLine = lineId; groupIndex = 0; focusedLabel = ""; message = ""
        }
    }

    fun selectGroup(index: Int) { groupIndex = index.coerceAtLeast(0); focusedLabel = ""; message = "" }
    fun updateReversed(value: Boolean) { reversed = value }
    fun focus(key: String, memory: FocusMemory? = null) {
        pendingFocus = key; pendingFocusMemory = memory; pendingSharedRevision = memory?.revision ?: 0; focusRevision++
    }
    fun clearPendingFocus() { pendingFocus = null; pendingFocusMemory = null }
    fun focusContextCurrent(memory: FocusMemory): Boolean = when {
        pendingFocus != null && pendingFocusMemory === memory -> pendingSharedRevision == memory.revision
        pendingFocus != null -> true // A record may prepare a focus before the detail view is mounted.
        lastFocusMemory === memory -> lastSharedRevision == memory.revision
        else -> true // Returning from playback creates a fresh page focus owner.
    }
    fun markUserInteraction() { userInteracted = true }
    fun expectAutomaticFocus(key: String) { automaticFocusKey = key }
    fun cancelAutomaticFocus(key: String) { if (automaticFocusKey == key) automaticFocusKey = null }
    fun isExpectedAutomaticFocus(key: String): Boolean = automaticFocusKey == key
    fun focused(key: String, memory: FocusMemory? = null) {
        val automatic = isExpectedAutomaticFocus(key)
        if (!automatic) { userInteracted = true; memory?.focused(key) }
        automaticFocusKey = null
        lastFocusKey = key; focusVersion++
        if (memory != null) { lastFocusMemory = memory; lastSharedRevision = memory.revision }
    }
}

/** Native online detail has its own focus/scroll lifecycle while playback retains the owner state. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
internal fun OnlineDetailScreen(
    api: JzApi,
    detail: OnlineDetail,
    item: OnlineItemState?,
    lineId: String,
    autoplay: Boolean,
    starting: Boolean,
    playableSource: Boolean,
    mutationBusy: Boolean,
    notice: String,
    ui: OnlineDetailUiState,
    focusMemory: FocusMemory,
    allowInitialFocus: Boolean,
    onSelectLine: (String) -> Unit,
    onPlay: (OnlineEntry) -> Unit,
    onFavorite: (OnlineItemState) -> Unit,
    onAutoplayChange: (Boolean) -> Unit,
) {
    val list = rememberLazyListState(ui.scrollIndex, ui.scrollOffset)
    val lines = rememberLazyListState()
    val groupStrip = rememberLazyListState()
    val requesters = remember { mutableMapOf<String, FocusRequester>() }
    var descriptionDialog by remember { mutableStateOf(false) }
    var moreDialog by remember { mutableStateOf(false) }
    var entryInfoDialog by remember { mutableStateOf(false) }
    var focusedOverflow by remember { mutableStateOf(false) }
    var headerHeightPx by remember { mutableIntStateOf(0) }
    val line = detail.lines.firstOrNull { it.id == lineId }
    val entries = line?.entries.orEmpty()
    val groups = remember(entries, ui.reversed) { onlineEpisodeGroups(entries, ui.reversed) }
    val selectedGroup = groups.firstOrNull { it.index == ui.groupIndex } ?: groups.firstOrNull()
    val positions = selectedGroup?.indices.orEmpty()
    val progress = item?.lastProgress?.takeIf { it.identity.sourceId == detail.source.id && it.identity.itemId == detail.item.id }
    val progressLine = progress?.identity?.lineId?.let { id -> detail.lines.firstOrNull { it.id == id } }
    val progressIndex = progressLine?.let { onlineEpisodeIndex(it.entries, progress?.identity) } ?: -1
    val resume = progress?.takeIf { it.identity.lineId == lineId }?.let { detail.findEntry(it.identity) }
    val primary = resume ?: entries.firstOrNull { it.supported && it.identity != null }
    val primaryLabel = when {
        primary == null -> "暂无可播放内容"
        resume != null && progress?.completed == true -> "重新播放 · ${primary.label}"
        resume != null -> "继续播放 · ${primary.label}"
        detail.item.kind == "tv" -> "播放 · ${primary.label}"
        else -> "播放"
    }
    fun Modifier.detailFocus(key: String): Modifier = focusRequester(requesters.getOrPut(key) { FocusRequester() })
        .onFocusChanged { if (it.isFocused) ui.focused(key, focusMemory) }

    LaunchedEffect(detail.item.id, lineId) { ui.bind(detail.item.id, lineId) }
    DisposableEffect(ui, detail.item.id) {
        onDispose { ui.scrollIndex = list.firstVisibleItemIndex; ui.scrollOffset = list.firstVisibleItemScrollOffset }
    }

    val parentScrollSpec = LocalBringIntoViewSpec.current
    val marginPx = with(LocalDensity.current) { 16.dp.toPx() }
    val scrollSpec = remember(parentScrollSpec, headerHeightPx, marginPx, ui) {
        object : BringIntoViewSpec {
            override fun calculateScrollDistance(offset: Float, size: Float, containerSize: Float): Float =
                onlineDetailScrollDistance(offset, size, containerSize, headerHeightPx.toFloat(), marginPx,
                    ui.pendingFocus ?: ui.lastFocusKey)
        }
    }

    BoxWithConstraints(Modifier.fillMaxSize().onPreviewKeyEvent { event ->
        if (event.type == KeyEventType.KeyDown) ui.markUserInteraction()
        false
    }) {
        val scale = LocalDensity.current.fontScale.coerceAtLeast(1f)
        val columns = ((maxWidth.value - 16f + 12f) / (100f * scale + 12f)).toInt().coerceIn(1, 12)
        val buttonWidth = ((maxWidth.value - 16f - (columns - 1) * 12f) / columns).coerceAtMost(100f).dp
        val rows = positions.chunked(columns)
        fun itemIndex(key: String): Int? = when {
            key in setOf("detail:play", "detail:favorite", "detail:description", "detail:more") -> 0
            key.startsWith("detail:line:") -> 1
            key.startsWith("detail:group:") || key == "detail:reverse" || key == "detail:locate" || key == "detail:full-name" -> 2
            else -> positions.indexOfFirst { onlineEpisodeKey(lineId, entries[it], it) == key }
                .takeIf { it >= 0 }?.let { 3 + it / columns }
        }
        LaunchedEffect(ui.focusRevision, detail.item.id, lineId, ui.groupIndex, ui.reversed, allowInitialFocus) {
            val pending = ui.pendingFocus
            val requestRevision = ui.focusRevision
            fun discard() {
                if (ui.focusRevision == requestRevision && ui.pendingFocus == pending) ui.clearPendingFocus()
            }
            if (!onlineDetailFocusAllowed(ui.lastFocusKey, pending, allowInitialFocus) || !ui.focusContextCurrent(focusMemory)) {
                discard(); return@LaunchedEffect
            }
            val wanted = pending ?: ui.lastFocusKey.ifBlank {
                onlineDetailInitialFocusKey(primary != null, playableSource, starting)
            }
            val before = ui.focusVersion
            val sharedBefore = focusMemory.revision
            fun stillCurrent(): Boolean = ui.focusRevision == requestRevision && ui.pendingFocus == pending &&
                ui.focusContextCurrent(focusMemory) && onlineDetailFocusCurrent(before, ui.focusVersion, sharedBefore, focusMemory.revision)
            withFrameNanos { }
            if (!stillCurrent()) { discard(); return@LaunchedEffect }
            if (descriptionDialog || moreDialog || entryInfoDialog) return@LaunchedEffect
            val index = itemIndex(wanted)
            if (index != null && list.layoutInfo.visibleItemsInfo.none { it.index == index }) {
                list.scrollToItem(index)
                if (!stillCurrent()) { discard(); return@LaunchedEffect }
                withFrameNanos { }
                if (!stillCurrent()) { discard(); return@LaunchedEffect }
            }
            if (wanted.startsWith("detail:line:")) {
                val selected = detail.lines.indexOfFirst { "detail:line:${it.id}" == wanted }
                if (selected >= 0) {
                    lines.scrollToItem(selected)
                    if (!stillCurrent()) { discard(); return@LaunchedEffect }
                    withFrameNanos { }
                    if (!stillCurrent()) { discard(); return@LaunchedEffect }
                }
            } else if (wanted.startsWith("detail:group:")) {
                val selected = groups.indexOfFirst { "detail:group:${it.index}" == wanted }
                if (selected >= 0) {
                    groupStrip.scrollToItem(selected)
                    if (!stillCurrent()) { discard(); return@LaunchedEffect }
                    withFrameNanos { }
                    if (!stillCurrent()) { discard(); return@LaunchedEffect }
                }
            }
            if (!stillCurrent()) { discard(); return@LaunchedEffect }
            ui.expectAutomaticFocus(wanted)
            val restored = runCatching { requesters[wanted]?.requestFocus() == true }.getOrDefault(false)
            if (!restored) ui.cancelAutomaticFocus(wanted)
            if (restored) ui.clearPendingFocus()
            else if (ui.pendingFocus != null || ui.lastFocusKey.isNotBlank()) {
                if (!stillCurrent()) { discard(); return@LaunchedEffect }
                list.scrollToItem(0)
                if (!stillCurrent()) { discard(); return@LaunchedEffect }
                withFrameNanos { }
                if (!stillCurrent()) { discard(); return@LaunchedEffect }
                ui.expectAutomaticFocus("detail:more")
                if (runCatching { requesters["detail:more"]?.requestFocus() == true }.getOrDefault(false)) ui.clearPendingFocus()
                else ui.cancelAutomaticFocus("detail:more")
            }
        }
        CompositionLocalProvider(LocalBringIntoViewSpec provides scrollSpec) {
        LazyColumn(state = list, modifier = Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 32.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            item(key = "detail:hero") {
                val posterWidth = onlineDetailPosterWidthDp(maxWidth.value).dp
                Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 8.dp), horizontalArrangement = Arrangement.spacedBy(18.dp)) {
                    Poster(api, detail.posterPath.orEmpty(), detail.item.title,
                        Modifier.width(posterWidth).height(posterWidth * 1.5f))
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(detail.item.title, style = MaterialTheme.typography.headlineMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        Text(listOf(if (detail.item.kind == "tv") "剧集" else "电影", detail.year, detail.source.name)
                            .filter(String::isNotBlank).joinToString(" · "), color = Muted, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        LazyRow(horizontalArrangement = Arrangement.spacedBy(10.dp), contentPadding = PaddingValues(8.dp)) {
                            item {
                                TvAction(primaryLabel, { ui.markUserInteraction(); primary?.let(onPlay) }, Modifier.widthIn(max = 240.dp).detailFocus("detail:play"),
                                    enabled = primary != null && playableSource && !starting, icon = if (starting) "loading" else "play")
                            }
                            item {
                                TvAction(if (item?.favorite == true) "取消收藏" else "收藏", { ui.markUserInteraction(); item?.let(onFavorite) },
                                    Modifier.detailFocus("detail:favorite"), enabled = item != null && !mutationBusy,
                                    icon = if (item?.favorite == true) "heart-filled" else "heart")
                            }
                            if (detail.description.isNotBlank() || detail.attribution.isNotEmpty()) item {
                                TvAction("完整简介", { ui.markUserInteraction(); descriptionDialog = true }, Modifier.detailFocus("detail:description"), icon = "info")
                            }
                            item { TvAction("更多", { ui.markUserInteraction(); moreDialog = true }, Modifier.detailFocus("detail:more"), icon = "more") }
                        }
                        if (detail.description.isNotBlank()) Text(detail.description, color = Muted, maxLines = 3,
                            overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium)
                        if (!playableSource) Text("来源暂不可播放，请返回选择其他来源。", color = Muted)
                        if (starting) Text("正在读取续播位置…", color = Muted)
                        if (notice.isNotBlank()) Text(notice, color = DesignTokens.Error)
                        if (ui.message.isNotBlank()) Text(ui.message, color = DesignTokens.Error)
                    }
                }
            }
            item(key = "detail:lines") {
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("播放线路", style = MaterialTheme.typography.headlineSmall)
                    if (detail.lines.isEmpty()) Text("暂无播放线路", color = Muted)
                    else LazyRow(state = lines, modifier = Modifier.weight(1f), horizontalArrangement = Arrangement.spacedBy(10.dp), contentPadding = PaddingValues(8.dp)) {
                        items(detail.lines, key = { it.id }) { selected ->
                            TvAction(selected.label, {
                                ui.markUserInteraction()
                                if (selected.id != lineId) {
                                    ui.bind(detail.item.id, selected.id)
                                    onSelectLine(selected.id)
                                }
                            }, Modifier.widthIn(max = 200.dp).detailFocus("detail:line:${selected.id}"), selected = selected.id == lineId)
                        }
                    }
                }
            }
            stickyHeader(key = "detail:episode-heading") {
                Column(Modifier.fillMaxWidth().onSizeChanged { headerHeightPx = it.height }
                    .background(MaterialTheme.colorScheme.background),
                    verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        Text(if (detail.item.kind == "tv") "选集" else "播放项", style = MaterialTheme.typography.headlineSmall)
                        if (groups.size > 1) LazyRow(state = groupStrip, modifier = Modifier.weight(1f),
                            horizontalArrangement = Arrangement.spacedBy(10.dp), contentPadding = PaddingValues(8.dp)) {
                            items(groups, key = { it.index }) { group ->
                                TvAction(group.label, { ui.markUserInteraction(); ui.selectGroup(group.index) }, Modifier.detailFocus("detail:group:${group.index}"), selected = group.index == selectedGroup?.index)
                            }
                        } else Text("共 ${entries.size} 项", color = Muted, modifier = Modifier.weight(1f))
                        if (entries.size > 1) TvAction(if (ui.reversed) "正序" else "倒序", {
                            ui.markUserInteraction(); ui.updateReversed(!ui.reversed); ui.focus("detail:reverse", focusMemory)
                        }, Modifier.detailFocus("detail:reverse"))
                        if (progressLine != null && progressIndex >= 0) {
                            TvAction("定位上次观看", {
                                ui.markUserInteraction()
                                if (progressLine.id != lineId) {
                                    ui.bind(detail.item.id, progressLine.id)
                                    onSelectLine(progressLine.id)
                                }
                                ui.selectGroup(progressIndex / ONLINE_EPISODE_GROUP_SIZE)
                                ui.focus(onlineEpisodeKey(progressLine.id, progressLine.entries[progressIndex], progressIndex), focusMemory)
                            }, Modifier.detailFocus("detail:locate"))
                        }
                    }
                    if (ui.focusedLabel.isNotBlank()) Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        Text(ui.focusedLabel, modifier = Modifier.weight(1f), color = Muted, maxLines = 2,
                            overflow = TextOverflow.Ellipsis, onTextLayout = { focusedOverflow = it.hasVisualOverflow })
                        if (focusedOverflow) TvAction("完整名称", { ui.markUserInteraction(); entryInfoDialog = true }, Modifier.detailFocus("detail:full-name"), icon = "info")
                    }
                }
            }
            if (positions.isEmpty()) item { Status("当前线路暂无播放项，请选择其他线路。") }
            else items(rows, key = { "detail:row:${lineId}:${it.first()}" }) { row ->
                Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 4.dp),
                    horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.Top) {
                    row.forEach { index ->
                        val entry = entries[index]
                        val key = onlineEpisodeKey(lineId, entry, index)
                        val available = entry.supported && entry.identity != null && playableSource && !starting
                        val unavailable = when {
                            !entry.supported || entry.identity == null -> onlineEntryUnavailableReason(entry)
                            !playableSource -> "来源暂不可播放"
                            starting -> "正在准备播放，请稍候"
                            else -> ""
                        }
                        val continuing = entry.identity != null && progress?.identity == entry.identity
                        Button(onClick = {
                            ui.markUserInteraction()
                            if (available) onPlay(entry) else ui.message = unavailable
                        }, modifier = Modifier.detailFocus(key).width(buttonWidth).heightIn(min = 56.dp)
                            .onFocusChanged { if (it.isFocused) ui.focusedLabel = entry.label +
                                (if (unavailable.isNotBlank()) " · $unavailable" else if (continuing) " · 上次观看" else "") }
                            .semantics { contentDescription = entry.label + if (unavailable.isNotBlank()) "，$unavailable" else if (continuing) "，上次观看" else "" }
                            .tvSelected(continuing && available),
                            contentPadding = PaddingValues(horizontal = 6.dp, vertical = 5.dp),
                            colors = tvButtonColors(selected = continuing && available, surface = if (available) Panel else Panel.copy(alpha = .55f)),
                            shape = tvButtonShape(), scale = tvButtonScale()) {
                            Column(Modifier.fillMaxWidth().heightIn(min = 46.dp), verticalArrangement = Arrangement.Center,
                                horizontalAlignment = Alignment.CenterHorizontally) {
                                Text(entry.label, style = MaterialTheme.typography.titleSmall, maxLines = 2,
                                    overflow = TextOverflow.Ellipsis, textAlign = TextAlign.Center,
                                    color = if (available) LocalContentColor.current else LocalContentColor.current.copy(alpha = .65f))
                                if (continuing) Text("上次", style = MaterialTheme.typography.labelSmall,
                                    color = LocalContentColor.current.copy(alpha = .8f))
                            }
                        }
                    }
                }
            }
        }
        }
    }
    if (descriptionDialog) TextDialog(detail.item.title,
        listOf(detail.description).filter(String::isNotBlank) + detail.attribution.map { "${it.label}\n${it.url}" },
        onClose = { descriptionDialog = false; ui.focus("detail:description", focusMemory) })
    if (entryInfoDialog) TextDialog("分集信息", listOf(ui.focusedLabel),
        onClose = { entryInfoDialog = false; ui.focus("detail:full-name", focusMemory) })
    if (moreDialog) {
    val dialogFocus = remember { FocusRequester() }
    LaunchedEffect(Unit) { withFrameNanos { }; dialogFocus.requestFocus() }
    Dialog(onDismissRequest = { moreDialog = false; ui.focus("detail:more", focusMemory) },
        properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Column(Modifier.fillMaxWidth(.65f).heightIn(max = tvDialogMaxHeight(.86f))
            .background(Panel, RoundedCornerShape(DesignTokens.DialogRadius)).padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("更多操作", style = MaterialTheme.typography.headlineSmall)
            if (detail.item.kind == "tv") TvAction("自动播放下一集：${if (autoplay) "开启" else "关闭"}",
                { ui.markUserInteraction(); onAutoplayChange(!autoplay) }, Modifier.focusRequester(dialogFocus), selected = autoplay)
            Text("只影响本电视当前观看模式；连播按同源同线原顺序，不跳过不支持的分集。", color = Muted)
            TvAction("关闭", { moreDialog = false; ui.focus("detail:more", focusMemory) },
                if (detail.item.kind == "tv") Modifier else Modifier.focusRequester(dialogFocus))
        }
    }
    }
}
