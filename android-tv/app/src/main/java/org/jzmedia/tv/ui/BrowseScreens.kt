package org.jzmedia.tv.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
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
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.focus.FocusRequester
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
        if (part.loading) Text(if (part.rows.isEmpty()) "正在读取${labels[index]}…" else "正在更新${labels[index]}…", color = Muted)
        if (part.error.isNotBlank()) {
            Text("${labels[index]}：${part.error}", color = Muted)
            TvAction("重试${labels[index]}", { retry(index) }, Modifier.focusMemory(memory, "home-retry:$index", retryFocus[index]))
        }
    }
    LazyColumn(state = listState, verticalArrangement = Arrangement.spacedBy(28.dp), contentPadding = PaddingValues(bottom = 30.dp)) {
        item(key = "heading") {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text("今晚看什么", style = MaterialTheme.typography.headlineSmall)
                TvAction("刷新", { attempts = attempts.map { it + 1 } }, Modifier.focusMemory(memory, "home-refresh", refreshFocus))
            }
        }
        item(key = "continue") {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                if (continuing.isNotEmpty()) {
                    Text("继续观看", style = MaterialTheme.typography.headlineSmall)
                    LazyRow(state = continueState, horizontalArrangement = Arrangement.spacedBy(18.dp), contentPadding = PaddingValues(16.dp)) {
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
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
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
    LazyRow(state = state, horizontalArrangement = Arrangement.spacedBy(18.dp), contentPadding = PaddingValues(16.dp)) {
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
    var watched by rememberSaveable { mutableStateOf("") }
    var rating by rememberSaveable { mutableStateOf("") }
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
    val filters = listOf(query, genre, region, year, watched, rating, sort, order)
    val gridState = rememberLazyGridState()
    LaunchedEffect(api, library, kind, page, filters, refresh, attempt) {
        data = null; error = ""
        try {
            val path = when (kind) { "movies" -> if (query.isBlank()) "/api/movies" else "/api/search"; "shows" -> "/api/tv/shows"; else -> "/api/collections" }
            val response = api.get(queryPath(path, mapOf("media_library" to library.takeIf { it > 0 }, "limit" to 36, "offset" to page * 36,
                "q" to query, "genre" to genre, "region" to region, "year" to year, "watched" to watched, "min_rating" to rating, "sort" to sort, "order" to order)))
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
    LaunchedEffect(data, error) {
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
    Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(18.dp), modifier = Modifier.fillMaxWidth()) {
            Text(title, style = MaterialTheme.typography.headlineMedium, modifier = Modifier.weight(1f))
            TvAction("搜索", { navigate(TvRoute("search", title = kind)) }, Modifier.focusMemory(memory, "search"))
            TvAction("筛选 / 排序", { filtering = true }, Modifier.focusMemory(memory, "filter", filterFocus))
            TvAction("刷新", { attempt++ }, Modifier.focusMemory(memory, "refresh"))
        }
        val summary = listOf(query.takeIf { it.isNotBlank() }?.let { "搜索：$it" }, genre, region, year,
            when (watched) { "1" -> "已看"; "0" -> "未看"; else -> "" }, rating.takeIf { it.isNotBlank() }?.let { "评分 ≥ $it" }).filterNotNull().filter { it.isNotBlank() }
        Text((if (summary.isEmpty()) "全部$title" else summary.joinToString(" · ")) + " · 第 ${page + 1} 页", color = Muted)
        when {
            error.isNotBlank() -> Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Status(error)
                TvAction("重试", { focusResults = true; attempt++ }, Modifier.focusMemory(memory, "retry-browse", retryFocus))
                if (page > 0) TvAction("返回上一页", { turnPage(page - 1) }, Modifier.focusMemory(memory, "previous", previousFocus))
            }
            data == null -> Status("正在读取$title…")
            rows.isEmpty() -> {
                Status(if (page > 0) "这一页已没有内容，请返回上一页。" else if (filters.take(6).any { it.isNotBlank() }) "没有符合条件的$title，请调整搜索或筛选。" else "暂无$title，请先在网页端导入。")
                if (page > 0) TvAction("返回上一页", { turnPage(page - 1) }, Modifier.focusMemory(memory, "previous", previousFocus))
            }
            else -> LazyVerticalGrid(state = gridState, columns = GridCells.Adaptive(150.dp), contentPadding = PaddingValues(10.dp),
                horizontalArrangement = Arrangement.spacedBy(18.dp), verticalArrangement = Arrangement.spacedBy(24.dp)) {
                itemsIndexed(rows, key = { _, row -> row.optLong("id") }) { index, row ->
                    MediaCard(api, row, { navigate(TvRoute(when (kind) { "movies" -> "movie"; "shows" -> "show"; else -> "collection" }, row.optLong("id"))) },
                        Modifier.focusMemory(memory, "card:${row.optLong("id")}", if (index == 0) firstResult else null),
                        if (kind == "collections") "${row.optInt("member_count")} 部影片" else "")
                }
                item(span = { GridItemSpan(maxLineSpan) }) {
                    Row(horizontalArrangement = Arrangement.spacedBy(18.dp), modifier = Modifier.padding(vertical = 10.dp)) {
                        if (page > 0) TvAction("上一页", { turnPage(page - 1) }, Modifier.focusMemory(memory, "previous", previousFocus))
                        if (hasMore) TvAction("下一页", { turnPage(page + 1) }, Modifier.focusMemory(memory, "next"))
                        Text("本页 ${rows.size} 项" + if (data?.has("total") == true) " / 共 ${data?.optInt("total")} 项" else "", color = Muted, modifier = Modifier.padding(12.dp))
                    }
                }
            }
        }
    }
    if (filtering) FilterDialog(api, kind, library, filters, onClose = { filtering = false }) { chosen ->
        if (chosen != filters || page != 0) { data = null; focusResults = true }
        query = chosen[0]; genre = chosen[1]; region = chosen[2]; year = chosen[3]; watched = chosen[4]; rating = chosen[5]; sort = chosen[6]; order = chosen[7]
        page = 0; filtering = false
    }
}

@Composable
private fun FilterDialog(api: JzApi, kind: String, library: Long, current: List<String>, onClose: () -> Unit, apply: (List<String>) -> Unit) {
    var query by remember { mutableStateOf(current[0]) }
    var values by remember { mutableStateOf(current.drop(1)) }
    var facets by remember { mutableStateOf(JSONObject()) }
    var error by remember { mutableStateOf("") }
    var attempt by remember { mutableStateOf(0) }
    var picker by remember { mutableStateOf<Int?>(null) }
    val memory = rememberFocusMemory("apply")
    LaunchedEffect(attempt) {
        if (kind == "collections") return@LaunchedEffect
        error = ""
        try { facets = api.get(queryPath(if (kind == "shows") "/api/tv/facets" else "/api/facets", mapOf("media_library" to library.takeIf { it > 0 }))) }
        catch (e: CancellationException) { throw e }
        catch (e: Exception) { error = e.message ?: "筛选项读取失败" }
    }
    val options = listOf(
        listOf("" to "全部类型") + facets.rows("genres").map { it.text("value") to "${it.text("value")} (${it.optInt("count")})" },
        listOf("" to "全部地区") + facets.rows("regions").map { it.text("value") to it.text("label", it.text("value")) },
        listOf("" to "全部年份") + facets.rows("years").map { it.text("value") to it.text("value") },
        listOf("" to "全部观看状态", "0" to "未看", "1" to "已看"),
        listOf("" to "不限评分", "6" to "6 分以上", "7" to "7 分以上", "8" to "8 分以上", "9" to "9 分以上"),
        if (kind == "collections") listOf("updated" to "最近更新", "title" to "标题") else listOf("added" to "加入时间", "updated" to "更新时间", "year" to "年份", "title" to "标题", "rating" to "评分"),
        listOf("desc" to "降序", "asc" to "升序"),
    )
    val labels = listOf("类型", "地区", "年份", "观看", "评分", "排序", "顺序")
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Column(Modifier.fillMaxWidth(.88f).heightIn(max = 540.dp).background(Panel, RoundedCornerShape(16.dp)).padding(28.dp),
            verticalArrangement = Arrangement.spacedBy(18.dp)) {
            Text("筛选与排序", style = MaterialTheme.typography.headlineSmall)
            LazyColumn(Modifier.weight(1f, fill = false), verticalArrangement = Arrangement.spacedBy(18.dp)) {
                item { TvInput(if (kind == "collections") "合集名称（原文，可选）" else "片名或演员（原文，可选）", query, { query = it }) }
                if (error.isNotBlank()) item { Status(error, { attempt++ }) }
                items((if (kind == "collections") listOf(5, 6) else (0..6).toList())) { index ->
                    val label = options[index].firstOrNull { it.first == values[index] }?.second ?: values[index]
                    TvAction("${labels[index]}：$label", { picker = index }, Modifier.fillMaxWidth())
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(18.dp)) {
                TvAction("应用", { apply(listOf(query.trim()) + values) }, Modifier.focusMemory(memory, "apply"))
                TvAction("清空", { query = ""; values = listOf("", "", "", "", "", if (kind == "collections") "updated" else "added", "desc") })
                TvAction("取消", onClose)
            }
        }
    }
    picker?.let { index ->
        ChoiceDialog(labels[index], options[index], values[index], { picker = null }) { value ->
            values = values.toMutableList().also { it[index] = value }; picker = null
        }
    }
}

@Composable
fun ChoiceDialog(title: String, choices: List<Pair<String, String>>, selected: String, onClose: () -> Unit, onSelect: (String) -> Unit) {
    val initialIndex = choices.indexOfFirst { it.first == selected }.coerceAtLeast(0)
    val listState = rememberLazyListState(initialIndex)
    val memory = rememberFocusMemory("choice:$selected")
    BackHandler { onClose() }
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Column(Modifier.fillMaxWidth(.7f).heightIn(max = 480.dp).background(Panel, RoundedCornerShape(16.dp)).padding(28.dp),
            verticalArrangement = Arrangement.spacedBy(18.dp)) {
            Text(title, style = MaterialTheme.typography.headlineSmall)
            LazyColumn(state = listState, modifier = Modifier.weight(1f, fill = false), contentPadding = PaddingValues(8.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                items(choices, key = { it.first }) { (value, label) ->
                    TvAction(label, { onSelect(value) }, Modifier.fillMaxWidth().focusMemory(memory, "choice:$value"), selected == value)
                }
            }
            TvAction("返回", onClose)
        }
    }
}
