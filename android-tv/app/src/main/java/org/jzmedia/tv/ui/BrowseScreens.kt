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
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.mediaTitle
import org.jzmedia.tv.data.queryPath
import org.jzmedia.tv.data.rows
import org.jzmedia.tv.data.text
import org.jzmedia.tv.playback.PlaybackRequest
import org.jzmedia.tv.playback.playbackTime
import org.json.JSONObject

@Composable
fun HomeScreen(api: JzApi, library: Long, refresh: Int, memory: FocusMemory, navigate: (TvRoute) -> Unit, play: (PlaybackRequest) -> Unit) {
    var data by remember { mutableStateOf<List<JSONObject>?>(null) }
    var error by remember { mutableStateOf("") }
    var attempt by remember { mutableStateOf(0) }
    val listState = rememberLazyListState()
    LaunchedEffect(api, library, refresh, attempt) {
        error = ""
        try {
            data = coroutineScope {
                listOf("/api/movies/recent-played", "/api/tv/recent-played", "/api/movies", "/api/tv/shows").map { path ->
                    async { api.get(queryPath(path, mapOf("media_library" to library.takeIf { it > 0 }, "limit" to 18, "sort" to "added", "order" to "desc"))) }
                }.map { it.await() }
            }
        } catch (e: CancellationException) { throw e }
        catch (e: Exception) { error = e.message ?: "首页加载失败" }
    }
    LaunchedEffect(data) {
        val loaded = data ?: return@LaunchedEffect
        if (!memory.restored) {
            val continues = loaded[0].rows().isNotEmpty() || loaded[1].rows().isNotEmpty()
            val target = when {
                memory.restoreKey.startsWith("newmovie:") -> if (continues) 2 else 1
                memory.restoreKey.startsWith("newshow:") -> if (continues) 3 else 2
                memory.restoreKey.startsWith("continue:") -> 1
                else -> null
            }
            if (target != null) listState.scrollToItem(target)
        }
    }
    LazyColumn(state = listState, verticalArrangement = Arrangement.spacedBy(28.dp), contentPadding = PaddingValues(bottom = 30.dp)) {
        item(key = "heading") { SectionHeading("今晚看什么", "遥控器方向键浏览，确定键打开") }
        if (error.isNotBlank()) item(key = "error") { Status(error, { attempt++ }) }
        if (data == null && error.isBlank()) item(key = "loading") { Status("正在读取影片…") }
        data?.let { loaded ->
            val continuing = (loaded[0].rows() + loaded[1].rows()).sortedByDescending { it.optJSONObject("progress")?.optLong("last_played_at") ?: 0 }
            if (continuing.isNotEmpty()) item(key = "continue") {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text("继续观看", style = MaterialTheme.typography.headlineSmall)
                    LazyRow(horizontalArrangement = Arrangement.spacedBy(18.dp), contentPadding = PaddingValues(8.dp)) {
                        items(continuing, key = { "${it.text("kind", "movie")}:${it.optJSONObject("progress")?.optLong("version_id")}" }) { row ->
                            val kind = row.text("kind", "movie")
                            val progress = row.optJSONObject("progress") ?: JSONObject()
                            val id = progress.optLong("version_id", row.optLong("id"))
                            MediaCard(api, row, { play(PlaybackRequest(kind, id, mediaTitle(row), true)) },
                                Modifier.focusMemory(memory, "continue:$kind:$id"),
                                row.text("subtitle").ifBlank { "已看 ${playbackTime(progress.optDouble("position", 0.0))}" })
                        }
                    }
                }
            }
            item(key = "newmovies") { MediaRail("最近加入的电影", loaded[2].rows(), api, memory, "newmovie") { navigate(TvRoute("movie", it.optLong("id"))) } }
            item(key = "newshows") { MediaRail("最近加入的电视剧", loaded[3].rows(), api, memory, "newshow") { navigate(TvRoute("show", it.optLong("id"))) } }
            if (loaded.all { it.rows().isEmpty() }) item(key = "empty") { Status("这个媒体库还没有影片。请先在网页端连接媒体库并扫描入库。") }
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
    val title = when (kind) { "movies" -> "电影"; "shows" -> "电视剧"; else -> "合集" }
    val filters = listOf(query, genre, region, year, watched, rating, sort, order)
    val gridState = rememberLazyGridState()
    LaunchedEffect(api, library, kind, page, filters, refresh, attempt) {
        data = null; error = ""
        try {
            val path = when (kind) { "movies" -> if (query.isBlank()) "/api/movies" else "/api/search"; "shows" -> "/api/tv/shows"; else -> "/api/collections" }
            data = api.get(queryPath(path, mapOf("media_library" to library.takeIf { it > 0 }, "limit" to 36, "offset" to page * 36,
                "q" to query, "genre" to genre, "region" to region, "year" to year, "watched" to watched, "min_rating" to rating, "sort" to sort, "order" to order)))
        } catch (e: CancellationException) { throw e }
        catch (e: Exception) { error = e.message ?: "读取失败" }
    }
    val source = data?.rows().orEmpty()
    val rows = if (kind == "collections") {
        val sorted = if (sort == "title") source.sortedBy { it.text("name") } else source.sortedBy { it.optLong("updated_at") }
        (if (order == "desc") sorted.reversed() else sorted).drop(page * 36).take(36)
    } else source
    val hasMore = if (kind == "collections") source.size > (page + 1) * 36 else data?.optBoolean("has_more") == true
    Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(18.dp), modifier = Modifier.fillMaxWidth()) {
            Text(title, style = MaterialTheme.typography.headlineMedium, modifier = Modifier.weight(1f))
            TvAction("搜索", { navigate(TvRoute("search", title = kind)) }, Modifier.focusMemory(memory, "search"))
            TvAction("筛选 / 排序", { filtering = true }, Modifier.focusMemory(memory, "filter"))
            TvAction("刷新", { attempt++ }, Modifier.focusMemory(memory, "refresh"))
        }
        val summary = listOf(query.takeIf { it.isNotBlank() }?.let { "搜索：$it" }, genre, region, year,
            when (watched) { "1" -> "已看"; "0" -> "未看"; else -> "" }, rating.takeIf { it.isNotBlank() }?.let { "评分 ≥ $it" }).filterNotNull().filter { it.isNotBlank() }
        Text((if (summary.isEmpty()) "全部$title" else summary.joinToString(" · ")) + " · 第 ${page + 1} 页", color = Muted)
        when {
            error.isNotBlank() -> Status(error, { attempt++ })
            data == null -> Status("正在读取$title…")
            rows.isEmpty() -> {
                Status(if (filters.take(6).any { it.isNotBlank() }) "没有符合条件的$title，请调整搜索或筛选。" else "暂无$title，请先在网页端导入。")
                if (page > 0) TvAction("返回上一页", { page-- })
            }
            else -> LazyVerticalGrid(state = gridState, columns = GridCells.Adaptive(150.dp), contentPadding = PaddingValues(10.dp),
                horizontalArrangement = Arrangement.spacedBy(18.dp), verticalArrangement = Arrangement.spacedBy(24.dp)) {
                items(rows, key = { it.optLong("id") }) { row ->
                    MediaCard(api, row, { navigate(TvRoute(when (kind) { "movies" -> "movie"; "shows" -> "show"; else -> "collection" }, row.optLong("id"))) },
                        Modifier.focusMemory(memory, "card:${row.optLong("id")}"),
                        if (kind == "collections") "${row.optInt("member_count")} 部影片" else "")
                }
                item(span = { GridItemSpan(maxLineSpan) }) {
                    Row(horizontalArrangement = Arrangement.spacedBy(18.dp), modifier = Modifier.padding(vertical = 10.dp)) {
                        if (page > 0) TvAction("上一页", { page-- }, Modifier.focusMemory(memory, "previous"))
                        if (hasMore) TvAction("下一页", { page++ }, Modifier.focusMemory(memory, "next"))
                        Text("本页 ${rows.size} 项" + if (data?.has("total") == true) " / 共 ${data?.optInt("total")} 项" else "", color = Muted, modifier = Modifier.padding(12.dp))
                    }
                }
            }
        }
    }
    if (filtering) FilterDialog(api, kind, library, filters, onClose = { filtering = false }) { chosen ->
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
