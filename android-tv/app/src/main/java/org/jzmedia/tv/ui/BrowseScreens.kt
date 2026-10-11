package org.jzmedia.tv.ui

import org.jzmedia.tv.ui.generated.DesignTokens
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.State
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.snapshots.SnapshotStateMap
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.ensureActive
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.mediaTitle
import org.jzmedia.tv.data.queryPath
import org.jzmedia.tv.data.rows
import org.jzmedia.tv.data.text
import org.jzmedia.tv.playback.PlaybackRequest
import org.jzmedia.tv.playback.playbackTime
import org.json.JSONObject
import kotlin.math.roundToInt

private data class HomePart(val rows: List<JSONObject> = emptyList(), val loading: Boolean = true, val error: String = "")
private fun continuingKey(row: JSONObject): String =
    "continue:${row.text("kind", "movie")}:${row.optJSONObject("progress")?.optLong("version_id", row.optLong("id")) ?: row.optLong("id")}"

@Composable
fun HomeScreen(api: JzApi, library: Long, refresh: Int, memory: FocusMemory, navigate: (TvRoute) -> Unit, play: (PlaybackRequest) -> Unit) {
    val paths = listOf("/api/movies/recent-played", "/api/tv/recent-played", "/api/movies", "/api/tv/shows")
    val labels = listOf("电影续播", "剧集续播", "最近加入的电影", "最近加入的电视剧")
    var parts by remember(api, library) { mutableStateOf(List(4) { HomePart() }) }
    var attempts by remember { mutableStateOf(List(4) { 0 }) }
    var retrying by remember { mutableStateOf<Int?>(null) }
    val retryFocus = remember { List(4) { FocusRequester() } }
    val firstFocus = remember { List(3) { FocusRequester() } }
    val refreshFocus = remember { FocusRequester() }
    val listState = rememberLazyListState()
    val continueState = rememberLazyListState()
    val movieState = rememberLazyListState()
    val showState = rememberLazyListState()
    val railStates = listOf(continueState, movieState, showState)
    for (index in paths.indices) {
        LaunchedEffect(api, library, refresh, attempts[index]) {
            parts = parts.toMutableList().also { it[index] = it[index].copy(loading = true, error = "") }
            try {
                val response = api.get(queryPath(paths[index], mapOf("media_library" to library.takeIf { it > 0 }, "limit" to 18, "sort" to "added", "order" to "desc")))
                coroutineContext.ensureActive()
                parts = parts.toMutableList().also { it[index] = HomePart(response.rows(), loading = false) }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) {
                coroutineContext.ensureActive()
                parts = parts.toMutableList().also { it[index] = it[index].copy(loading = false, error = e.message ?: "读取失败") }
            }
        }
    }
    val continuing = (parts[0].rows + parts[1].rows).sortedByDescending { it.optJSONObject("progress")?.optLong("last_played_at") ?: 0 }
    val groups = listOf(continuing, parts[2].rows, parts[3].rows)
    val groupKeys = listOf(continuing.map(::continuingKey), parts[2].rows.map { "newmovie:${it.optLong("id")}" }, parts[3].rows.map { "newshow:${it.optLong("id")}" })
    LaunchedEffect(parts, retrying) {
        if (memory.leaving) return@LaunchedEffect
        val retry = retrying
        if (retry != null) {
            if (parts[retry].loading) return@LaunchedEffect
            val group = if (retry < 2) 0 else retry - 1
            when {
                parts[retry].error.isNotBlank() -> { listState.scrollToItem(group + 1); withFrameNanos { }; retryFocus[retry].requestFocus() }
                groups[group].isNotEmpty() -> { listState.scrollToItem(group + 1); railStates[group].scrollToItem(0); withFrameNanos { }; firstFocus[group].requestFocus() }
                else -> { listState.scrollToItem(0); withFrameNanos { }; refreshFocus.requestFocus() }
            }
            retrying = null
            return@LaunchedEffect
        }
        if (memory.restored || memory.leaving) return@LaunchedEffect
        val restoredRetry = memory.restoreKey.removePrefix("home-retry:").toIntOrNull()?.takeIf { memory.restoreKey.startsWith("home-retry:") && it in 0..3 }
        if (restoredRetry != null) {
            if (parts[restoredRetry].loading) return@LaunchedEffect
            val group = if (restoredRetry < 2) 0 else restoredRetry - 1
            if (parts[restoredRetry].error.isNotBlank()) {
                listState.scrollToItem(group + 1)
                withFrameNanos { }
                if (retryFocus[restoredRetry].requestFocus(FocusDirection.Enter)) memory.restored = true
            } else if (groups[group].isNotEmpty()) {
                listState.scrollToItem(group + 1); railStates[group].scrollToItem(0); withFrameNanos { }
                if (firstFocus[group].requestFocus(FocusDirection.Enter)) memory.restored = true
            } else { listState.scrollToItem(0); withFrameNanos { }; if (refreshFocus.requestFocus(FocusDirection.Enter)) memory.restored = true }
            return@LaunchedEffect
        }
        val group = when {
            memory.restoreKey.startsWith("continue:") -> 0
            memory.restoreKey.startsWith("newmovie:") -> 1
            memory.restoreKey.startsWith("newshow:") -> 2
            else -> return@LaunchedEffect
        }
        val sources = if (group == 0) listOf(0, 1) else listOf(group + 1)
        // Do not decide that a saved card disappeared before its section finishes.
        if (sources.any { parts[it].loading }) return@LaunchedEffect
        val savedIndex = groupKeys[group].indexOf(memory.restoreKey)
        if (savedIndex >= 0) {
            listState.scrollToItem(group + 1)
            railStates[group].scrollToItem(savedIndex)
            return@LaunchedEffect
        }
        val failed = sources.firstOrNull { parts[it].error.isNotBlank() }
        val fallback = if (groups[group].isNotEmpty()) group else groups.indexOfFirst { it.isNotEmpty() }
        when {
            failed != null -> { listState.scrollToItem(group + 1); withFrameNanos { }; if (retryFocus[failed].requestFocus(FocusDirection.Enter)) memory.restored = true }
            fallback >= 0 -> { listState.scrollToItem(fallback + 1); railStates[fallback].scrollToItem(0); withFrameNanos { }; if (firstFocus[fallback].requestFocus(FocusDirection.Enter)) memory.restored = true }
            else -> { listState.scrollToItem(0); withFrameNanos { }; if (refreshFocus.requestFocus(FocusDirection.Enter)) memory.restored = true }
        }
    }
    fun retry(index: Int) {
        parts = parts.toMutableList().also { it[index] = it[index].copy(loading = true, error = "") }
        retrying = index
        attempts = attempts.toMutableList().also { it[index]++ }
    }
    @Composable fun Feedback(index: Int) {
        val part = parts[index]
        if (part.loading) Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
            TvIcon("loading")
            Text(if (part.rows.isEmpty()) "正在读取${labels[index]}…" else "正在更新${labels[index]}…", color = Muted)
        }
        if (part.error.isNotBlank()) {
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                TvIcon("error")
                Text("${labels[index]}：${part.error}", color = Muted)
            }
            TvAction("重试${labels[index]}", { retry(index) }, Modifier.focusMemory(memory, "home-retry:$index", retryFocus[index]), icon = "refresh")
        }
    }
    LazyColumn(state = listState, verticalArrangement = Arrangement.spacedBy(20.dp), contentPadding = PaddingValues(bottom = 24.dp)) {
        item(key = "heading") {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                Text("今晚看什么", style = MaterialTheme.typography.headlineMedium)
                TvAction("刷新", { attempts = attempts.map { it + 1 } }, Modifier.focusMemory(memory, "home-refresh", refreshFocus), icon = "refresh")
            }
        }
        item(key = "continue") {
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                if (continuing.isNotEmpty()) {
                    Text("继续观看", style = MaterialTheme.typography.headlineSmall)
                    LazyRow(state = continueState, horizontalArrangement = Arrangement.spacedBy(16.dp), contentPadding = PaddingValues(8.dp)) {
                        itemsIndexed(continuing, key = { _, row -> continuingKey(row) }) { index, row ->
                            val kind = row.text("kind", "movie")
                            val progress = row.optJSONObject("progress") ?: JSONObject()
                            val id = progress.optLong("version_id", row.optLong("id"))
                            val position = progress.optDouble("position", 0.0).takeIf { it.isFinite() && it >= 0 } ?: 0.0
                            val duration = progress.optDouble("duration", 0.0).takeIf { it.isFinite() && it > 0 }
                            val remaining = if (duration != null) "剩余 ${playbackTime((duration - position).coerceAtLeast(0.0))}" else "已看 ${playbackTime(position)}"
                            val caption = listOf(if (kind == "episode") row.text("subtitle").substringBefore(" · ") else "", remaining).filter { it.isNotBlank() }.joinToString(" · ")
                            MediaCard(api, row, { play(PlaybackRequest(kind, id, mediaTitle(row), true)) },
                                Modifier.focusMemory(memory, continuingKey(row), if (index == 0) firstFocus[0] else null), caption,
                                progressFraction = duration?.let { (position / it).coerceIn(0.0, 1.0).toFloat() })
                        }
                    }
                }
                Feedback(0); Feedback(1)
            }
        }
        for (index in 2..3) item(key = if (index == 2) "newmovies" else "newshows") {
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                HomeRail(labels[index], parts[index].rows, api, memory, if (index == 2) "newmovie" else "newshow", railStates[index - 1], firstFocus[index - 1]) {
                    navigate(TvRoute(if (index == 2) "movie" else "show", it.optLong("id")))
                }
                Feedback(index)
            }
        }
        if (parts.all { !it.loading && it.error.isBlank() && it.rows.isEmpty() }) item(key = "empty") {
            Status("这个媒体库还没有影片。请先在网页端连接媒体库并扫描入库。")
        }
    }
}

@Composable
private fun HomeRail(title: String, rows: List<JSONObject>, api: JzApi, memory: FocusMemory, prefix: String,
                     state: LazyListState, first: FocusRequester, open: (JSONObject) -> Unit) {
    if (rows.isEmpty()) return
    Text(title, style = MaterialTheme.typography.headlineSmall)
    LazyRow(state = state, horizontalArrangement = Arrangement.spacedBy(16.dp), contentPadding = PaddingValues(8.dp)) {
        itemsIndexed(rows, key = { _, row -> row.optLong("id") }) { index, row ->
            MediaCard(api, row, { open(row) }, Modifier.focusMemory(memory, "$prefix:${row.optLong("id")}", if (index == 0) first else null))
        }
    }
}

@Composable
fun BrowseScreen(api: JzApi, kind: String, library: Long, refresh: Int, memory: FocusMemory, navigate: (TvRoute) -> Unit) {
    var page by rememberSaveable { mutableStateOf(0) }
    var query by rememberSaveable { mutableStateOf("") }
    var genre by rememberSaveable { mutableStateOf("") }
    var region by rememberSaveable { mutableStateOf("") }
    var year by rememberSaveable { mutableStateOf("") }
    var decade by rememberSaveable { mutableStateOf("") }
    var sort by rememberSaveable { mutableStateOf(if (kind == "collections") "updated" else "added") }
    var order by rememberSaveable { mutableStateOf("desc") }
    var filtering by rememberSaveable { mutableStateOf(false) }
    var data by remember { mutableStateOf<JSONObject?>(null) }
    var error by remember { mutableStateOf("") }
    var attempt by remember { mutableStateOf(0) }
    var focusResults by remember { mutableStateOf(false) }
    val firstResult = remember { FocusRequester() }
    val filterFocus = remember { FocusRequester() }
    val retryFocus = remember { FocusRequester() }
    val previousFocus = remember { FocusRequester() }
    val title = when (kind) { "movies" -> "电影"; "shows" -> "电视剧"; else -> "合集" }
    val filters = listOf(query, genre, region, year, decade, sort, order)
    val gridState = rememberLazyGridState()
    LaunchedEffect(api, library, kind, page, filters, refresh, attempt) {
        data = null; error = ""
        try {
            val path = when (kind) { "movies" -> if (query.isBlank()) "/api/movies" else "/api/search"; "shows" -> "/api/tv/shows"; else -> "/api/collections" }
            val response = api.get(queryPath(path, mapOf("media_library" to library.takeIf { it > 0 }, "limit" to 36, "offset" to page * 36,
                "q" to query, "genre" to genre, "region" to region, "year" to year, "decade" to decade, "sort" to sort, "order" to order)))
            coroutineContext.ensureActive()
            data = response
        } catch (e: CancellationException) { throw e }
        catch (e: Exception) { coroutineContext.ensureActive(); error = e.message ?: "读取失败" }
    }
    val source = data?.rows().orEmpty()
    val rows = if (kind == "collections") {
        val sorted = if (sort == "title") source.sortedBy { it.text("name") } else source.sortedBy { it.optLong("updated_at") }
        (if (order == "desc") sorted.reversed() else sorted).drop(page * 36).take(36)
    } else source
    val hasMore = if (kind == "collections") source.size > (page + 1) * 36 else data?.optBoolean("has_more") == true
    LaunchedEffect(data, error, focusResults) {
        if (memory.leaving) return@LaunchedEffect
        val restoringCard = !memory.restored && memory.restoreKey.startsWith("card:")
        if (error.isNotBlank()) {
            if (focusResults || restoringCard) {
                withFrameNanos { }
                if (retryFocus.requestFocus(FocusDirection.Enter)) memory.restored = true
            }
            focusResults = false
            return@LaunchedEffect
        }
        if (data == null) return@LaunchedEffect
        val savedIndex = rows.indexOfFirst { "card:${it.optLong("id")}" == memory.restoreKey }
        if (restoringCard && savedIndex >= 0 && !focusResults) {
            withFrameNanos { }
            if (!memory.restored && gridState.layoutInfo.visibleItemsInfo.none { it.index == savedIndex }) gridState.scrollToItem(savedIndex)
        } else if (focusResults || restoringCard) {
            if (rows.isNotEmpty()) gridState.scrollToItem(0)
            withFrameNanos { }
            val target = if (rows.isNotEmpty()) firstResult else if (page > 0) previousFocus else filterFocus
            if (target.requestFocus(FocusDirection.Enter)) memory.restored = true
            focusResults = false
        }
    }
    fun turnPage(next: Int) { if (next != page) { data = null; error = ""; page = next; focusResults = true } }
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(16.dp), modifier = Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(title, style = MaterialTheme.typography.headlineMedium, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
            TvAction("搜索", { navigate(TvRoute("search", title = kind)) }, Modifier.focusMemory(memory, "search"), icon = "search")
            TvAction("筛选", { filtering = true }, Modifier.focusMemory(memory, "filter", filterFocus), icon = "filter")
            TvAction("刷新", { attempt++ }, Modifier.focusMemory(memory, "refresh"), icon = "refresh")
        }
        val periodLabel = when {
            year.isNotBlank() -> year
            decade.isNotBlank() -> "${decade}年代"
            else -> ""
        }
        val activeFilters = listOfNotNull(
            query.takeIf { it.isNotBlank() }?.let { "搜索：$it" },
            genre.ifBlank { null }, region.ifBlank { null }, periodLabel.ifBlank { null },
        )
        val base = if (activeFilters.isEmpty()) "全部$title" else activeFilters.joinToString(" · ")
        Text("$base · ${sortLabelFor(kind, sort)} · 第 ${page + 1} 页", color = Muted,
            style = MaterialTheme.typography.bodyMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
        when {
            error.isNotBlank() -> Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Status(error, icon = "error")
                TvAction("重试", { focusResults = true; attempt++ }, Modifier.focusMemory(memory, "retry-browse", retryFocus), icon = "refresh")
                if (page > 0) TvAction("返回上一页", { turnPage(page - 1) }, Modifier.focusMemory(memory, "previous", previousFocus), icon = "back")
            }
            data == null -> Status("正在读取$title…", icon = "loading")
            rows.isEmpty() -> {
                Status(if (page > 0) "这一页已没有内容，请返回上一页。" else if (listOf(query, genre, region, year, decade).any { it.isNotBlank() }) "没有符合条件的$title，请调整搜索或筛选。" else "暂无$title，请先在网页端导入。")
                if (page > 0) TvAction("返回上一页", { turnPage(page - 1) }, Modifier.focusMemory(memory, "previous", previousFocus), icon = "back")
            }
            else -> LazyVerticalGrid(state = gridState, columns = GridCells.Adaptive(150.dp), contentPadding = PaddingValues(8.dp),
                horizontalArrangement = Arrangement.spacedBy(16.dp), verticalArrangement = Arrangement.spacedBy(20.dp)) {
                itemsIndexed(rows, key = { _, row -> row.optLong("id") }) { index, row ->
                    MediaCard(api, row, { navigate(TvRoute(when (kind) { "movies" -> "movie"; "shows" -> "show"; else -> "collection" }, row.optLong("id"))) },
                        Modifier.focusMemory(memory, "card:${row.optLong("id")}", if (index == 0) firstResult else null),
                        if (kind == "collections") "${row.optInt("member_count")} 部影片" else "")
                }
                item(span = { GridItemSpan(maxLineSpan) }) {
                    Row(horizontalArrangement = Arrangement.spacedBy(16.dp), modifier = Modifier.padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                        if (page > 0) TvAction("上一页", { turnPage(page - 1) }, Modifier.focusMemory(memory, "previous", previousFocus), icon = "back")
                        if (hasMore) TvAction("下一页", { turnPage(page + 1) }, Modifier.focusMemory(memory, "next"), icon = "forward")
                        Text("本页 ${rows.size} 项" + if (data?.has("total") == true) " / 共 ${data?.optInt("total")} 项" else "", color = Muted, modifier = Modifier.padding(12.dp))
                    }
                }
            }
        }
    }
    if (filtering) {
        val snapshot = filters
        FilterDrawer(
            api = api, kind = kind, library = library,
            genre = genre, region = region, year = year, decade = decade,
            sort = sort, query = query,
            onGenre = { v -> if (v != genre) { genre = v; page = 0 } },
            onRegion = { v -> if (v != region) { region = v; page = 0 } },
            onPeriod = { y, d -> if (y != year || d != decade) { year = y; decade = d; page = 0 } },
            onSort = { s, o -> if (s != sort || o != order) { sort = s; order = o; page = 0 } },
            onQuery = { v -> if (v != query) { query = v; page = 0 } },
            onClear = {
                query = ""; genre = ""; region = ""; year = ""; decade = ""
                sort = if (kind == "collections") "updated" else "added"; order = "desc"; page = 0
            },
            onClose = {
                filtering = false
                if (filters != snapshot) focusResults = true
            },
        )
    }
}

/**
 * 左侧筛选抽屉：分组按钮一屏平铺、无子菜单，选中即时生效。
 * 焦点留在抽屉内；关闭时若有改动才把焦点交给结果区（由 BrowseScreen 的
 * focusResults 接管，避免每次点选都抢焦点）。
 */
@Composable
private fun FilterDrawer(
    api: JzApi, kind: String, library: Long,
    genre: String, region: String, year: String, decade: String, sort: String, query: String,
    onGenre: (String) -> Unit, onRegion: (String) -> Unit, onPeriod: (String, String) -> Unit,
    onSort: (String, String) -> Unit, onQuery: (String) -> Unit,
    onClear: () -> Unit, onClose: () -> Unit,
) {
    var facets by remember { mutableStateOf(JSONObject()) }
    var error by remember { mutableStateOf("") }
    var attempt by remember { mutableStateOf(0) }
    // 抽屉用独立焦点记忆，不污染浏览页的返回恢复。
    val drawerMemory = rememberFocusMemory("g:$genre")
    val clearFocus = remember { FocusRequester() }
    LaunchedEffect(api, library, kind, attempt) {
        if (kind == "collections") return@LaunchedEffect
        error = ""
        try { facets = api.get(queryPath(if (kind == "shows") "/api/tv/facets" else "/api/facets", mapOf("media_library" to library.takeIf { it > 0 }))) }
        catch (e: CancellationException) { throw e }
        catch (e: Exception) { error = e.message ?: "筛选项读取失败" }
    }
    val genreOptions = remember(facets) {
        listOf("" to "全部") + facets.rows("genres").map { it.text("value") to "${it.text("value")} (${it.optInt("count")})" }
    }
    val regionOptions = remember(facets) {
        listOf("" to "全部") + facets.rows("regions").map { it.text("value") to it.text("value") }
    }
    val periodChips = remember(facets) {
        val years = facets.rows("years").mapNotNull { row ->
            val v = row.text("value").toIntOrNull() ?: return@mapNotNull null
            v to row.optInt("count")
        }
        val decades = facets.rows("decades").mapNotNull { row ->
            val v = row.text("value").toIntOrNull() ?: return@mapNotNull null
            v to row.optInt("count")
        }
        buildPeriodChips(years, decades)
    }
    val sortChips = remember(kind) { sortChipsFor(kind) }
    val periodId = periodChipId(year, decade)
    // 多行换行平铺，纵向由外层 LazyColumn 滚动承载，不再横向单行。
    val listState = rememberLazyListState()
    // 滚动条几何：各分节实测高度 + 固定间距，进度=前缀和+首项内偏移。
    val sectionHeights = remember { mutableStateMapOf<String, Int>() }
    var listViewportPx by remember { mutableStateOf(0) }
    val sectionKeys = if (kind == "collections") listOf("sort") else listOf("genre", "region", "period", "sort")
    val density = LocalDensity.current
    val gapPx = remember { with(density) { 12.dp.toPx() } }
    val padPx = remember { with(density) { 4.dp.toPx() } }
    // facets 未就绪且当前选中不在首屏选项里时，焦点落到清空按钮兜底。
    LaunchedEffect(genreOptions) {
        if (drawerMemory.restored) return@LaunchedEffect
        if (genreOptions.any { "g:${it.first}" == drawerMemory.restoreKey }) return@LaunchedEffect
        withFrameNanos { }
        if (clearFocus.requestFocus()) drawerMemory.restored = true
    }
    // 组吸顶对齐：焦点进入某分节时，把该组组名+选项滚到可见区顶部，
    // 保证组名永远在自己的选项上方；从底部一路上移即自然回到真正的列表顶部。
    var focusSection by remember { mutableStateOf(0) }
    LaunchedEffect(Unit) {
        snapshotFlow { focusSection }.collect { section ->
            if (section !in sectionKeys.indices) return@collect
            val heights = sectionKeys.map { sectionHeights[it] }
            if (heights.any { it == null || it <= 0 } || listViewportPx <= 0) return@collect
            val sectionTopPx = heights.take(section).sumOf { it as Int }.toFloat() + gapPx * section
            val firstIdx = listState.firstVisibleItemIndex.coerceAtMost(sectionKeys.size - 1)
            val scrolledPx = heights.take(firstIdx).sumOf { it as Int }.toFloat() + gapPx * firstIdx +
                listState.firstVisibleItemScrollOffset
            if (scrolledPx > sectionTopPx + 1f) listState.animateScrollToItem(section)
        }
    }
    // 摘要只列实际生效的筛选；排序非默认值时才作为一项显示，无内容整行隐藏。
    val defaultSort = if (kind == "collections") "updated" else "added"
    val pickedSummary = listOfNotNull(
        genreOptions.firstOrNull { it.first == genre && genre.isNotBlank() }?.second,
        regionOptions.firstOrNull { it.first == region && region.isNotBlank() }?.second,
        periodChips.firstOrNull { it.id == periodId && it.id != "all" }?.label,
        sortChips.firstOrNull { it.sort == sort && sort != defaultSort }?.label,
    )
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.CenterEnd) {
            // 背景压暗：抽屉优先的层级感（结果仍透出可见，便于即时对比筛选效果）。
            Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background.copy(alpha = .45f)))
            Column(
                Modifier.fillMaxWidth(.46f).fillMaxHeight()
                    .background(Panel, RoundedCornerShape(
                        topStart = DesignTokens.DialogRadius, bottomStart = DesignTokens.DialogRadius))
                    .padding(24.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text("筛选", style = MaterialTheme.typography.headlineSmall, modifier = Modifier.weight(1f),
                        maxLines = 1, overflow = TextOverflow.Ellipsis)
                    TvAction("清空", onClear, Modifier.focusRequester(clearFocus))
                    TvAction("关闭", onClose)
                }
                if (kind == "collections") {
                    TvInput("合集名称（原文，可选）", query, onQuery, Modifier.fillMaxWidth())
                }
                if (error.isNotBlank()) Status(error, { attempt++ })
                Box(Modifier.weight(1f, fill = false)) {
                    // 右侧留出滚动条宽度，chip 不再铺到滚动条下面。
                    LazyColumn(state = listState,
                        modifier = Modifier.fillMaxWidth().onSizeChanged { listViewportPx = it.height },
                        contentPadding = PaddingValues(start = 4.dp, top = 4.dp, end = 18.dp, bottom = 4.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        if (kind != "collections") {
                            item(key = "genre") {
                                SectionRow(sectionHeights, "genre") {
                                    FilterChipRow(0,
                                        "类型",
                                        genreOptions.map { (value, label) -> Triple("g:$value", label, value == genre) },
                                        drawerMemory, onFocusSection = { focusSection = 0 }) { onGenre(it.removePrefix("g:")) }
                                }
                            }
                            item(key = "region") {
                                SectionRow(sectionHeights, "region") {
                                    FilterChipRow(1,
                                        "地区",
                                        regionOptions.map { (value, label) -> Triple("r:$value", label, value == region) },
                                        drawerMemory, onFocusSection = { focusSection = 1 }) { onRegion(it.removePrefix("r:")) }
                                }
                            }
                            item(key = "period") {
                                SectionRow(sectionHeights, "period") {
                                    FilterChipRow(2,
                                        "年份",
                                        periodChips.map { chip -> Triple("p:${chip.id}", chip.label, chip.id == periodId) },
                                        drawerMemory, onFocusSection = { focusSection = 2 }) { id ->
                                        periodChips.firstOrNull { "p:${it.id}" == id }
                                            ?.let { onPeriod(it.year, it.decade) }
                                        }
                                }
                            }
                        }
                        item(key = "sort") {
                            SectionRow(sectionHeights, "sort") {
                                FilterChipRow(sectionKeys.indexOf("sort"),
                                    "排序",
                                    sortChips.map { chip -> Triple("s:${chip.key}", chip.label, chip.sort == sort) },
                                    drawerMemory, onFocusSection = { focusSection = sectionKeys.indexOf("sort") }) { id ->
                                    sortChips.firstOrNull { "s:${it.key}" == id }
                                        ?.let { onSort(it.sort, it.order) }
                                    }
                            }
                        }
                    }
                    // 滚动条画在覆盖层上，几何完全自持：各分节实测高度 + 固定间距，
                    // 进度 = 前缀和 + 首项内偏移，顶底端点必然精确、滚动必然重绘。
                    val thumb = remember(listState, sectionKeys) {
                        derivedStateOf {
                            if (!(listState.canScrollForward || listState.canScrollBackward)) return@derivedStateOf null
                            val heights = sectionKeys.map { k -> sectionHeights[k]?.toFloat() }
                                .takeIf { size -> size.size == sectionKeys.size && size.none { it == null || it <= 0f } }
                                ?.map { it as Float } ?: return@derivedStateOf null
                            val viewportPx = listViewportPx.toFloat()
                            if (viewportPx <= 0f) return@derivedStateOf null
                            // 内容总高 = Σ分节高 + 分节间间距 + 上下内容边距
                            val contentPx = heights.sum() + gapPx * (heights.size - 1) + padPx * 2
                            val maxScrollPx = contentPx - viewportPx
                            if (maxScrollPx <= 2f) return@derivedStateOf null
                            val firstIndex = listState.firstVisibleItemIndex.coerceAtMost(heights.size - 1)
                            val scrolledPx = (heights.take(firstIndex).sum() + gapPx * firstIndex +
                                listState.firstVisibleItemScrollOffset)
                                .coerceIn(0f, maxScrollPx)
                            val frac = (viewportPx / contentPx).coerceIn(.18f, .8f)
                            val progress = scrolledPx / maxScrollPx
                            (progress * (1f - frac)) to frac
                        }
                    }
                    Box(Modifier.matchParentSize().drawerScrollbar(thumb))
                }
                if (pickedSummary.isNotEmpty()) Text("已选：" + pickedSummary.joinToString(" · "),
                    color = Muted, style = MaterialTheme.typography.bodyMedium,
                    maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}

/** 抽屉右缘滚动条：覆盖层绘制，位置跟随内容滚动；只指示位置，不参与焦点。 */
private fun Modifier.drawerScrollbar(thumb: State<Pair<Float, Float>?>): Modifier = drawWithContent {
    drawContent()
    val t = thumb.value ?: return@drawWithContent
    val barW = 4.dp.toPx()
    val insetEnd = 8.dp.toPx()
    drawRoundRect(DesignTokens.TextDim,
        Offset(size.width - insetEnd - barW, size.height * t.first),
        Size(barW, size.height * t.second),
        CornerRadius(barW / 2, barW / 2))
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun FilterChipRow(
    section: Int, label: String, chips: List<Triple<String, String, Boolean>>,
    memory: FocusMemory, onFocusSection: (Int) -> Unit, onPick: (String) -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(label, style = MaterialTheme.typography.titleMedium)
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            chips.forEach { (id, text, selected) ->
                var chipFocused by remember { mutableStateOf(false) }
                TvAction(
                    text, { onPick(id) },
                    Modifier
                        // 常驻细边框（Web chip 式）：未选中 1dp 暗框；选中 2dp 白框（红底上可见），
                        // 聚焦+选中改红框（白底上可见）。先缩 1dp 让描边完整落在自身边界内，
                        // 再禁用聚焦放大——放大是上下边缘被裁切的根源。
                        .onFocusChanged {
                            chipFocused = it.isFocused
                            if (it.isFocused) onFocusSection(section)
                        }
                        .padding(1.dp)
                        .border(
                            width = if (selected) 2.dp else 1.dp,
                            color = when {
                                selected && chipFocused -> Accent
                                selected -> DesignTokens.TextStrong
                                else -> DesignTokens.Border
                            },
                            shape = RoundedCornerShape(DesignTokens.CornerRadius),
                        )
                        .focusMemory(memory, id),
                    selected = selected,
                    focusScale = 1f,
                )
            }
        }
    }
}

/** 分节测高包装：把实际渲染高度登记进快照映射，供滚动条几何用。 */
@Composable
private fun SectionRow(heights: SnapshotStateMap<String, Int>, key: String, content: @Composable () -> Unit) {
    Box(Modifier.onSizeChanged { heights[key] = it.height }) { content() }
}

@Composable
fun ChoiceDialog(title: String, choices: List<Pair<String, String>>, selected: String, onClose: () -> Unit, onSelect: (String) -> Unit) {
    val initialIndex = choices.indexOfFirst { it.first == selected }.coerceAtLeast(0)
    val listState = rememberLazyListState(initialIndex)
    val memory = rememberFocusMemory("choice:$selected")
    val dialogHeight = tvDialogMaxHeight(.86f)
    BackHandler { onClose() }
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Column(Modifier.fillMaxWidth(.7f).heightIn(max = dialogHeight).background(Panel, RoundedCornerShape(DesignTokens.DialogRadius)).padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(title, style = MaterialTheme.typography.headlineSmall)
            LazyColumn(state = listState, modifier = Modifier.weight(1f, fill = false), contentPadding = PaddingValues(8.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                items(choices, key = { it.first }) { (value, label) ->
                    TvAction(label, { onSelect(value) }, Modifier.fillMaxWidth().focusMemory(memory, "choice:$value"), selected == value, fillContent = true)
                }
            }
        }
    }
}
