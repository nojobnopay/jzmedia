package org.jzmedia.tv.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.tv.material3.Button
import androidx.tv.material3.ButtonDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.ensureActive
import org.jzmedia.tv.data.ApiException
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.RecentActor
import org.jzmedia.tv.data.SearchHistoryStore
import org.jzmedia.tv.data.actorAvatarPath
import org.jzmedia.tv.data.actorWorkSummary
import org.jzmedia.tv.data.actorWorksPath
import org.jzmedia.tv.data.rows
import org.jzmedia.tv.data.text
import org.json.JSONObject

@Composable
internal fun ActorCard(api: JzApi, actor: JSONObject, onClick: () -> Unit, modifier: Modifier = Modifier) {
    val name = actor.text("name").ifBlank { "未知演员" }
    val summary = actorWorkSummary(actor)
    Button(onClick, modifier.fillMaxWidth().height(76.dp).semantics { contentDescription = "$name · $summary" },
        contentPadding = PaddingValues(8.dp), shape = ButtonDefaults.shape(shape = RoundedCornerShape(8.dp)),
        colors = ButtonDefaults.colors(containerColor = Panel, contentColor = Color.White,
            focusedContainerColor = Color.White, focusedContentColor = Color.Black)) {
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
            Poster(api, actorAvatarPath(actor), name.take(1), Modifier.width(44.dp).height(56.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(name, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(summary, style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}

@Composable
fun ActorWorksScreen(api: JzApi, library: Long, route: TvRoute, memory: FocusMemory,
                     navigate: (TvRoute) -> Unit, back: () -> Unit) {
    var kind by rememberSaveable { mutableStateOf("all") }
    var page by rememberSaveable { mutableStateOf(0) }
    var data by remember { mutableStateOf<JSONObject?>(null) }
    var error by remember { mutableStateOf("") }
    var attempt by remember { mutableStateOf(0) }
    var resetResults by remember { mutableStateOf(false) }
    var focusResults by remember { mutableStateOf(false) }
    val grid = rememberLazyGridState()
    val firstResult = remember { FocusRequester() }
    val returnButton = remember { FocusRequester() }
    val previousButton = remember { FocusRequester() }
    val retryButton = remember { FocusRequester() }
    val context = LocalContext.current
    val history = remember(context) { SearchHistoryStore(context.applicationContext) }
    val rows = data?.rows().orEmpty()
    LaunchedEffect(api, library, route.actorKey, kind, page, attempt) {
        data = null; error = ""
        try {
            val response = api.get(actorWorksPath(route.actorKey, kind, library, page))
            coroutineContext.ensureActive()
            data = response
            val actorName = response.optJSONObject("actor")?.text("name").orEmpty().ifBlank { route.title }
            history.recordActor(api.baseUrl, library, RecentActor(route.actorKey, actorName))
        } catch (e: CancellationException) { throw e }
        catch (e: Exception) {
            coroutineContext.ensureActive()
            error = if (e is ApiException && e.status == 404)
                "演员资料已失效，或服务器尚不支持演员作品。请返回重新选择；旧版服务器请先更新。"
            else e.message ?: "演员作品读取失败，请重试"
        }
    }
    LaunchedEffect(data, error) {
        if (memory.leaving) return@LaunchedEffect
        val restoringResult = !memory.restored && memory.restoreKey.startsWith("work:")
        if (error.isNotBlank()) {
            if (focusResults || restoringResult) {
                withFrameNanos { }
                if (retryButton.requestFocus(FocusDirection.Enter)) memory.restored = true
            }
            focusResults = false
            return@LaunchedEffect
        }
        if (data == null) return@LaunchedEffect
        val savedIndex = rows.indexOfFirst { "work:${it.text("kind")}:${it.optLong("id")}" == memory.restoreKey }
        if (restoringResult && savedIndex >= 0 && !focusResults && !resetResults) {
            withFrameNanos { }
            if (!memory.restored && grid.layoutInfo.visibleItemsInfo.none { it.index == savedIndex }) grid.scrollToItem(savedIndex)
        } else if (focusResults || resetResults || restoringResult) {
            if (rows.isNotEmpty()) grid.scrollToItem(0)
            withFrameNanos { }
            if (focusResults || restoringResult) {
                val target = if (rows.isNotEmpty()) firstResult else if (page > 0) previousButton else returnButton
                if (target.requestFocus(FocusDirection.Enter)) memory.restored = true
            }
        }
        resetResults = false; focusResults = false
    }
    fun turnPage(next: Int) { if (next != page) { data = null; error = ""; page = next; focusResults = true } }
    Column(Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(14.dp), verticalAlignment = Alignment.CenterVertically) {
            TvAction("‹ 返回", back, Modifier.focusMemory(memory, "back", returnButton))
            Text(data?.optJSONObject("actor")?.text("name").orEmpty().ifBlank { route.title.ifBlank { "演员作品" } },
                style = MaterialTheme.typography.headlineMedium, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f))
            listOf("all" to "全部", "movie" to "电影", "show" to "电视剧").forEach { (value, label) ->
                TvAction(label, {
                    if (kind != value || page != 0) {
                        kind = value; page = 0; data = null; error = ""; resetResults = true; focusResults = false
                    }
                }, Modifier.focusMemory(memory, "works-scope:$value"), selected = kind == value)
            }
        }
        Text(data?.let { "本库 ${it.optInt("total")} 部作品 · 第 ${page + 1} 页" } ?: "本库作品", color = Muted)
        when {
            error.isNotBlank() -> Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Status(error)
                TvAction("重试", { focusResults = true; attempt++ }, Modifier.focusMemory(memory, "retry-works", retryButton))
            }
            data == null -> Status("正在读取演员作品…")
            rows.isEmpty() -> Status(if (page > 0) "这一页已没有作品，请返回上一页。" else "当前媒体库中没有这一类型的作品。")
            else -> {
                BoxWithConstraints(Modifier.weight(1f).fillMaxWidth()) {
                    val columns = ((maxWidth + 2.dp) / 168.dp).toInt().coerceAtLeast(1)
                    LazyVerticalGrid(columns = GridCells.Fixed(columns), state = grid,
                        contentPadding = PaddingValues(8.dp), horizontalArrangement = Arrangement.spacedBy(18.dp),
                        verticalArrangement = Arrangement.spacedBy(18.dp)) {
                        itemsIndexed(rows, key = { _, row -> "${row.text("kind")}:${row.optLong("id")}" }) { index, row ->
                            val resultKind = row.text("kind")
                            MediaCard(api, row, { navigate(TvRoute(resultKind, row.optLong("id"))) },
                                Modifier.focusMemory(memory, "work:$resultKind:${row.optLong("id")}", if (index == 0) firstResult else null),
                                listOf(if (resultKind == "show") "电视剧" else "电影", row.text("year")).filter { it.isNotBlank() }.joinToString(" · "))
                        }
                    }
                }
            }
        }
        if (page > 0 || data?.optBoolean("has_more") == true) Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            if (page > 0) TvAction("上一页", { turnPage(page - 1) }, Modifier.focusMemory(memory, "previous-works", previousButton))
            if (data?.optBoolean("has_more") == true) TvAction("下一页", { turnPage(page + 1) }, Modifier.focusMemory(memory, "next-works"))
        }
    }
}
