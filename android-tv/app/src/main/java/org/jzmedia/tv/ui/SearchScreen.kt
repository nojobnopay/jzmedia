package org.jzmedia.tv.ui

import org.jzmedia.tv.ui.generated.DesignTokens
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.BringIntoViewSpec
import androidx.compose.foundation.gestures.LocalBringIntoViewSpec
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusProperties
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.tv.material3.Button
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import org.jzmedia.tv.data.ApiException
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.SEARCH_QUERY_LIMIT
import org.jzmedia.tv.data.SearchHistoryStore
import org.jzmedia.tv.data.TV_SEARCH_KEYS
import org.jzmedia.tv.data.appendSearchInput
import org.jzmedia.tv.data.actorSearchPath
import org.jzmedia.tv.data.deleteSearchInput
import org.jzmedia.tv.data.rows
import org.jzmedia.tv.data.searchKind
import org.jzmedia.tv.data.text
import org.jzmedia.tv.data.tvSearchPath
import org.json.JSONObject

/** The keyboard is part of the app, so ordinary TV search never requires a system IME. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun SearchScreen(api: JzApi, library: Long, initialKind: String, memory: FocusMemory, navigate: (TvRoute) -> Unit, back: () -> Unit) {
    var mode by rememberSaveable { mutableStateOf("title") }
    var titleQuery by rememberSaveable { mutableStateOf("") }
    var actorQuery by rememberSaveable { mutableStateOf("") }
    val actorsMode = mode == "actor"
    val query = if (actorsMode) actorQuery else titleQuery
    var kind by rememberSaveable { mutableStateOf(searchKind(initialKind)) }
    var page by rememberSaveable { mutableStateOf(0) }
    var lastKey by rememberSaveable { mutableStateOf("A") }
    var editing by rememberSaveable { mutableStateOf(false) }
    var restoreInput by remember { mutableStateOf(false) }
    var data by remember { mutableStateOf<JSONObject?>(null) }
    var error by remember { mutableStateOf("") }
    var loading by remember { mutableStateOf(false) }
    var attempt by remember { mutableStateOf(0) }
    var focusResults by remember { mutableStateOf(false) }
    var resetResults by remember { mutableStateOf(false) }
    val keys = remember { TV_SEARCH_KEYS.associateWith { FocusRequester() } }
    val firstResult = remember { FocusRequester() }
    val inputButton = remember { FocusRequester() }
    val retryButton = remember { FocusRequester() }
    val previousButton = remember { FocusRequester() }
    val historyEntry = remember { FocusRequester() }
    val grid = rememberLazyGridState()
    val resultEntryIndex by remember { derivedStateOf { grid.firstVisibleItemIndex } }
    val softwareKeyboard = LocalSoftwareKeyboardController.current
    val context = LocalContext.current
    val history = remember(context) { SearchHistoryStore(context.applicationContext) }
    var historyVersion by remember { mutableStateOf(0) }
    val pastQueries = remember(api.baseUrl, library, mode, historyVersion) { history.queries(api.baseUrl, library, mode) }
    val recentActors = remember(api.baseUrl, library, historyVersion) { history.actors(api.baseUrl, library) }
    val showingHistory = query.isBlank() && (pastQueries.isNotEmpty() || recentActors.isNotEmpty())
    val rows = data?.rows().orEmpty()
    fun resultKey(row: JSONObject) = if (actorsMode) "result:actor:${row.text("key")}" else "result:${row.text("kind")}:${row.optLong("id")}"
    fun rememberQuery(value: String = query) {
        history.recordQuery(api.baseUrl, library, mode, value)
        historyVersion++
    }
    fun leaveSearch() { rememberQuery(); back() }
    BackHandler(enabled = !editing) { leaveSearch() }

    fun changeQuery(value: String) {
        val normalized = appendSearchInput("", value)
        if (normalized == query && page == 0) return
        if (memory.key.startsWith("result:") || memory.key.startsWith("history:")) keys.getValue(lastKey).requestFocus()
        if (actorsMode) actorQuery = normalized else titleQuery = normalized
        page = 0
        data = null
        error = ""
        focusResults = false
        resetResults = true
    }
    LaunchedEffect(editing, restoreInput) {
        if (!editing && restoreInput) {
            withFrameNanos { }
            inputButton.requestFocus()
            restoreInput = false
        }
    }

    LaunchedEffect(api, library, query, mode, kind, page, attempt) {
        data = null; error = ""; loading = actorsMode || query.isNotBlank()
        if (!actorsMode && query.isBlank()) return@LaunchedEffect
        try {
            delay(250)
            val response = api.get(if (actorsMode) actorSearchPath(query, library, page) else tvSearchPath(query, kind, library, page))
            coroutineContext.ensureActive()
            data = response
        } catch (e: CancellationException) { throw e }
        catch (e: Exception) {
            coroutineContext.ensureActive()
            error = if (e is ApiException && e.status == 404)
                if (actorsMode) "服务器尚不支持演员搜索，请更新服务器后重试。" else "服务器尚不支持首字母搜索，请更新服务器后重试。"
            else e.message ?: "搜索失败，请重试"
        } finally { loading = false }
    }
    LaunchedEffect(data, error) {
        if (memory.leaving) return@LaunchedEffect
        val restoringResult = !memory.restored && memory.restoreKey.startsWith("result:")
        if (error.isNotBlank()) {
            if (focusResults || restoringResult) {
                withFrameNanos { }
                if (retryButton.requestFocus(FocusDirection.Enter)) memory.restored = true
            }
            focusResults = false
            return@LaunchedEffect
        }
        if (data == null) return@LaunchedEffect
        val savedIndex = rows.indexOfFirst { resultKey(it) == memory.restoreKey }
        if (restoringResult && savedIndex >= 0 && !focusResults && !resetResults) {
            withFrameNanos { }
            if (!memory.restored && grid.layoutInfo.visibleItemsInfo.none { it.index == savedIndex }) grid.scrollToItem(savedIndex)
        } else if (focusResults || resetResults || restoringResult) {
            if (rows.isNotEmpty()) grid.scrollToItem(0)
            withFrameNanos { }
            if (focusResults || restoringResult) {
                val target = if (rows.isNotEmpty()) firstResult else if (page > 0) previousButton else keys.getValue(lastKey)
                if (target.requestFocus(FocusDirection.Enter)) memory.restored = true
            }
        }
        focusResults = false
        resetResults = false
    }
    fun turnPage(next: Int) {
        if (next != page) { rememberQuery(); data = null; error = ""; page = next; focusResults = true }
    }

    Column(Modifier.fillMaxSize().onPreviewKeyEvent { event ->
        if (editing || event.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
        val native = event.nativeKeyEvent
        if (native.keyCode == android.view.KeyEvent.KEYCODE_DEL) {
            changeQuery(deleteSearchInput(query)); true
        } else if (!native.isCtrlPressed && !native.isAltPressed && native.unicodeChar >= 32 && !Character.isISOControl(native.unicodeChar)) {
            changeQuery(appendSearchInput(query, String(Character.toChars(native.unicodeChar)))); true
        } else false
    }, verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(14.dp), verticalAlignment = Alignment.CenterVertically) {
            TvAction("返回", ::leaveSearch, Modifier.focusMemory(memory, "back"), icon = "back")
            Text("搜索", style = MaterialTheme.typography.headlineMedium, modifier = Modifier.padding(end = 12.dp))
            listOf("title" to "片名", "actor" to "演员").forEach { (value, label) ->
                TvAction(label, {
                    if (mode != value) {
                        rememberQuery()
                        mode = value; page = 0; data = null; error = ""; focusResults = false; resetResults = true
                    }
                }, Modifier.focusMemory(memory, "mode:$value"), selected = mode == value)
            }
        }
        BoxWithConstraints(Modifier.weight(1f).fillMaxWidth()) {
            val keyboardWidth = (maxWidth * .3f).coerceIn(264.dp, 328.dp)
            val keyboardStyle = MaterialTheme.typography.titleMedium
            val minimumKeyHeight = maxOf(28.dp, with(LocalDensity.current) { keyboardStyle.lineHeight.toDp() } + 8.dp)
            val keyHeight = ((maxHeight - 174.dp) / 6f - 5.dp)
                .coerceIn(minimumKeyHeight, maxOf(44.dp, minimumKeyHeight))
            Row(Modifier.fillMaxSize(), horizontalArrangement = Arrangement.spacedBy(28.dp)) {
                Column(Modifier.width(keyboardWidth).fillMaxHeight(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Box(Modifier.fillMaxWidth().height(48.dp).background(Panel, RoundedCornerShape(DesignTokens.CornerRadius)).padding(horizontal = 14.dp), contentAlignment = Alignment.CenterStart) {
                        Text(query.ifEmpty { if (actorsMode) "输入姓名首字母" else "输入片名首字母" }, color = if (query.isEmpty()) Muted else DesignTokens.TextStrong,
                            style = MaterialTheme.typography.titleLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                    Text(if (actorsMode) "姓名首字母 / 全拼 / 英文" else "片名首字母 / 全拼 / 英文",
                        style = MaterialTheme.typography.labelMedium, color = Muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Column(Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState()),
                        verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Column(verticalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.padding(4.dp)) {
                            TV_SEARCH_KEYS.chunked(6).forEachIndexed { rowIndex, group ->
                                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                                    group.forEachIndexed { column, letter ->
                                        val index = rowIndex * 6 + column
                                        Button(onClick = { changeQuery(appendSearchInput(query, letter)) },
                                            modifier = Modifier.weight(1f).height(keyHeight)
                                                .focusProperties {
                                                    if (column > 0) left = keys.getValue(TV_SEARCH_KEYS[index - 1]) else left = FocusRequester.Cancel
                                                    if (column < 5) right = keys.getValue(TV_SEARCH_KEYS[index + 1])
                                                    else if (showingHistory) right = historyEntry
                                                    else if (rows.isNotEmpty() && !loading) right = firstResult
                                                    if (rowIndex > 0) up = keys.getValue(TV_SEARCH_KEYS[index - 6])
                                                    if (rowIndex < 5) down = keys.getValue(TV_SEARCH_KEYS[index + 6])
                                                }
                                                .focusMemory(memory, "key:$letter", keys.getValue(letter))
                                                .onFocusChanged { if (it.isFocused) lastKey = letter },
                                            contentPadding = PaddingValues(0.dp),
                                            shape = tvButtonShape(), scale = tvButtonScale(),
                                            colors = tvButtonColors(),
                                        ) { Text(letter, style = keyboardStyle) }
                                    }
                                }
                            }
                        }
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            TvAction("删除", { changeQuery(deleteSearchInput(query)) }, Modifier.weight(1f).focusMemory(memory, "delete"), centerLabel = true)
                            TvAction("清空", { rememberQuery(); changeQuery("") }, Modifier.weight(1f).focusMemory(memory, "clear"), centerLabel = true)
                            TvAction("中文输入", { editing = true }, Modifier.weight(1.5f).focusMemory(memory, "input", inputButton), centerLabel = true)
                        }
                        Text(if (query.codePointCount(0, query.length) >= SEARCH_QUERY_LIMIT) "最多输入 $SEARCH_QUERY_LIMIT 个字符" else "方向键选字母 · 右移看候选",
                            style = MaterialTheme.typography.labelMedium, color = Muted)
                    }
                }
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(if (data == null) { if (actorsMode) "本库演员" else "片名联想" }
                            else "${data!!.optInt("total")} ${if (actorsMode) "位演员" else "项"} · 第 ${page + 1} 页",
                            style = MaterialTheme.typography.labelLarge, modifier = Modifier.weight(1f), maxLines = 2, overflow = TextOverflow.Ellipsis)
                        if (!actorsMode) listOf("all" to "全部", "movie" to "电影", "show" to "电视剧", "collection" to "合集").forEach { (value, label) ->
                            TvAction(label, {
                                if (kind != value || page != 0) { kind = value; page = 0; data = null; error = ""; focusResults = false; resetResults = true }
                            }, Modifier.focusMemory(memory, "scope:$value"), selected = kind == value)
                        }
                    }
                    if (query.isBlank()) {
                        if (pastQueries.isNotEmpty()) SearchHistoryRail(
                            "${if (actorsMode) "演员" else "片名"}搜索记录", pastQueries.map { it to it }, "query:$mode", memory,
                            historyEntry, keys.getValue(lastKey), clear = {
                                keys.getValue(lastKey).requestFocus()
                                history.clearQueries(api.baseUrl, library, mode); historyVersion++
                            }, select = { value ->
                                keys.getValue(lastKey).requestFocus(); rememberQuery(value); changeQuery(value); focusResults = true
                            })
                        if (recentActors.isNotEmpty()) SearchHistoryRail(
                            "最近查看的演员", recentActors.map { it.key to it.name }, "actor", memory,
                            if (pastQueries.isEmpty()) historyEntry else null, keys.getValue(lastKey), clear = {
                                keys.getValue(lastKey).requestFocus()
                                history.clearActors(api.baseUrl, library); historyVersion++
                            }, select = { value ->
                                val actor = recentActors.first { it.key == value }
                                navigate(TvRoute("actor-works", title = actor.name, actorKey = actor.key))
                            })
                    }
                    when {
                        error.isNotBlank() -> Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                            Status(error, icon = "error")
                            TvAction("重试", { focusResults = true; attempt++ }, Modifier.focusMemory(memory, "retry-search", retryButton), icon = "refresh")
                        }
                        !actorsMode && query.isBlank() -> Status("输入几个首字母，就能查看当前媒体库中的候选片名。")
                        loading || data == null -> Status("正在搜索…", icon = "loading")
                        rows.isEmpty() -> Status(if (page > 0) "这一页已没有内容，请返回上一页。" else if (actorsMode) {
                            if (query.isBlank()) "当前媒体库中还没有可浏览的演员。" else "没有找到相关演员，试试更短的首字母或其他姓名。"
                        } else "没有找到相关内容，试试更短的首字母或其他片名。")
                        else -> {
                            BoxWithConstraints(Modifier.weight(1f)) {
                                val columns = ((maxWidth + 2.dp) / (if (actorsMode) 244.dp else 168.dp)).toInt().coerceAtLeast(1)
                                // Reserve two title lines, one caption, card padding and
                                // the grid's focus margins at the actual system font scale.
                                val captionHeight = with(LocalDensity.current) {
                                    MaterialTheme.typography.titleSmall.lineHeight.toDp() * 2f +
                                        MaterialTheme.typography.labelSmall.lineHeight.toDp()
                                } + MediaCardTextGap
                                val focusPadding = 16.dp
                                val posterHeight = (maxHeight - captionHeight - MediaCardPadding * 2f -
                                    MediaCardArtworkGap - focusPadding * 2f).coerceIn(100.dp, 180.dp)
                                val parentScrollSpec = LocalBringIntoViewSpec.current
                                val margin = with(LocalDensity.current) { focusPadding.toPx() }
                                val scrollSpec = remember(parentScrollSpec, margin) {
                                    object : BringIntoViewSpec {
                                        override fun calculateScrollDistance(offset: Float, size: Float, containerSize: Float): Float {
                                            val inset = minOf(margin, ((containerSize - size) / 2f).coerceAtLeast(0f))
                                            return parentScrollSpec.calculateScrollDistance(offset - inset, size + inset * 2f, containerSize)
                                        }
                                    }
                                }
                                CompositionLocalProvider(LocalBringIntoViewSpec provides scrollSpec) {
                                    LazyVerticalGrid(modifier = Modifier.fillMaxSize(), columns = GridCells.Fixed(columns), state = grid,
                                        contentPadding = PaddingValues(focusPadding), horizontalArrangement = Arrangement.spacedBy(18.dp),
                                        verticalArrangement = Arrangement.spacedBy(18.dp)) {
                                        itemsIndexed(rows, key = { _, row -> if (actorsMode) "actor:${row.text("key")}" else "${row.text("kind")}:${row.optLong("id")}" }) { index, row ->
                                            val modifier = Modifier.focusProperties { if (index % columns == 0) left = keys.getValue(lastKey) }
                                                .focusMemory(memory, resultKey(row), if (index == resultEntryIndex) firstResult else null)
                                            if (actorsMode) ActorCard(api, row, {
                                                rememberQuery()
                                                navigate(TvRoute("actor-works", title = row.text("name"), actorKey = row.text("key")))
                                            }, modifier)
                                            else {
                                                val resultKind = row.text("kind")
                                                MediaCard(api, row, { rememberQuery(); navigate(TvRoute(resultKind, row.optLong("id"))) }, modifier,
                                                    listOf(when (resultKind) { "movie" -> "电影"; "show" -> "电视剧"; else -> "合集" }, row.text("year")).filter { it.isNotBlank() }.joinToString(" · "),
                                                    posterHeight = posterHeight)
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    if (page > 0 || data?.optBoolean("has_more") == true) Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                        if (page > 0) TvAction("上一页", { turnPage(page - 1) }, Modifier.focusMemory(memory, "previous-search", previousButton), icon = "back")
                        if (data?.optBoolean("has_more") == true) TvAction("下一页", { turnPage(page + 1) }, Modifier.focusMemory(memory, "next-search"), icon = "forward")
                    }
                }
            }
        }
    }
    if (editing) {
        var draft by remember { mutableStateOf(query) }
        val dialogInput = remember { FocusRequester() }
        fun close() { editing = false; softwareKeyboard?.hide(); restoreInput = true }
        Dialog(onDismissRequest = { close() }, properties = DialogProperties(usePlatformDefaultWidth = false)) {
            LaunchedEffect(Unit) { withFrameNanos { }; dialogInput.requestFocus() }
            Column(Modifier.width(640.dp).background(Panel, RoundedCornerShape(DesignTokens.DialogRadius)).padding(28.dp), verticalArrangement = Arrangement.spacedBy(20.dp)) {
                Text(if (actorsMode) "输入演员姓名" else "输入片名", style = MaterialTheme.typography.headlineSmall)
                TvInput("支持中文、英文或拼音", draft, { draft = appendSearchInput("", it) }, Modifier.focusRequester(dialogInput))
                Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                    TvAction("搜索", { rememberQuery(draft); changeQuery(draft); close() }, icon = "search")
                    TvAction("取消", { close() })
                }
            }
        }
    }
}

@Composable
private fun SearchHistoryRail(title: String, entries: List<Pair<String, String>>, prefix: String, memory: FocusMemory,
                              entryRequester: FocusRequester?, keyboard: FocusRequester, clear: () -> Unit, select: (String) -> Unit) {
    val state = rememberLazyListState()
    val entryIndex by remember { derivedStateOf { state.firstVisibleItemIndex } }
    LaunchedEffect(entries) {
        if (!memory.restored && memory.restoreKey.startsWith("history:$prefix:")) {
            val savedIndex = entries.indexOfFirst { "history:$prefix:${it.first}" == memory.restoreKey }
            if (savedIndex >= 0) state.scrollToItem(savedIndex)
        }
    }
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(title, style = MaterialTheme.typography.labelLarge, color = Muted, modifier = Modifier.weight(1f))
            SearchHistoryAction("清除", clear, Modifier.widthIn(min = 60.dp).heightIn(min = 32.dp).focusMemory(memory, "clear-history:$prefix"))
        }
        LazyRow(state = state, contentPadding = PaddingValues(horizontal = 8.dp, vertical = 4.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            itemsIndexed(entries, key = { _, entry -> entry.first }) { index, entry ->
                SearchHistoryAction(entry.second, { select(entry.first) }, Modifier.width(136.dp).heightIn(min = 36.dp)
                    .focusProperties { if (index == 0) left = keyboard }
                    .focusMemory(memory, "history:$prefix:${entry.first}", if (index == entryIndex) entryRequester else null))
            }
        }
    }
}

@Composable
private fun SearchHistoryAction(text: String, click: () -> Unit, modifier: Modifier) {
    Button(click, modifier, contentPadding = PaddingValues(horizontal = 12.dp, vertical = 4.dp),
        shape = tvButtonShape(), scale = tvButtonScale(), colors = tvButtonColors()) {
        Text(text, style = MaterialTheme.typography.labelLarge,
            textAlign = TextAlign.Center, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}
