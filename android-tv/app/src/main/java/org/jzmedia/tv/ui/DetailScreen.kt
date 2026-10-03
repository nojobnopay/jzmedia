package org.jzmedia.tv.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.episodeTitle
import org.jzmedia.tv.data.mediaTitle
import org.jzmedia.tv.data.posterPath
import org.jzmedia.tv.data.queryPath
import org.jzmedia.tv.data.rows
import org.jzmedia.tv.data.text
import org.jzmedia.tv.playback.PlaybackRequest
import org.json.JSONArray
import org.json.JSONObject

@Composable
fun DetailScreen(api: JzApi, route: TvRoute, refresh: Int, memory: FocusMemory, navigate: (TvRoute) -> Unit, play: (PlaybackRequest) -> Unit) {
    var data by remember { mutableStateOf<JSONObject?>(null) }
    var similar by remember { mutableStateOf<List<JSONObject>>(emptyList()) }
    var error by remember { mutableStateOf("") }
    var similarError by remember { mutableStateOf("") }
    var actionError by remember { mutableStateOf("") }
    var actionBusy by remember { mutableStateOf(false) }
    var attempt by remember { mutableStateOf(0) }
    var page by rememberSaveable { mutableStateOf(0) }
    var selectedVersion by rememberSaveable { mutableStateOf(0L) }
    var seasonVersion by rememberSaveable { mutableStateOf("") }
    var focusedEpisodeId by rememberSaveable { mutableStateOf(0L) }
    var focusEpisodesAfterLoad by remember { mutableStateOf(false) }
    var requestEpisodeFocus by remember { mutableStateOf(false) }
    var choosingVersion by remember { mutableStateOf(false) }
    var textDialog by remember { mutableStateOf<Pair<String, List<String>>?>(null) }
    val scope = rememberCoroutineScope()
    // Keep scroll state while data is reloaded and when returning from a child episode.
    val listState = rememberLazyListState()
    val collectionColumns = ((LocalConfiguration.current.screenWidthDp - 80) / 168).coerceAtLeast(1)
    LaunchedEffect(api, route, refresh, attempt, page, seasonVersion) {
        error = ""; data = null
        try {
            data = api.get(when (route.kind) {
                "movie" -> "/api/movies/${route.id}"
                "show" -> "/api/tv/shows/${route.id}"
                "season" -> queryPath("/api/tv/shows/${route.id}/seasons/${route.season}", mapOf("offset" to page * 50, "limit" to 50, "version" to seasonVersion))
                "episode" -> "/api/tv/episodes/${route.id}"
                else -> "/api/collections/${route.id}"
            })
            if (route.kind == "season" && focusEpisodesAfterLoad) {
                requestEpisodeFocus = true
                focusEpisodesAfterLoad = false
            }
            if (route.kind == "movie" && data!!.rows("versions").none { it.optLong("id") == selectedVersion }) {
                selectedVersion = data!!.rows("versions").firstOrNull()?.optLong("id") ?: route.id
            }
        } catch (e: CancellationException) { throw e }
        catch (e: Exception) { error = e.message ?: "详情读取失败" }
    }
    LaunchedEffect(api, route, refresh, attempt) {
        if (route.kind != "movie" && route.kind != "show") return@LaunchedEffect
        similarError = ""
        try { similar = api.get(if (route.kind == "movie") "/api/movies/${route.id}/similar?limit=12" else "/api/tv/shows/${route.id}/similar?limit=12").rows() }
        catch (e: CancellationException) { throw e }
        catch (_: Exception) { similarError = "相关推荐暂时不可用" }
    }
    if (error.isNotBlank()) { Status(error, { attempt++ }); return }
    val row = data ?: run { Status("正在读取详情…"); return }
    val title = when (route.kind) {
        "season" -> "${row.text("show_title")} · ${row.text("name").ifBlank { "第 ${route.season} 季" }}"
        "episode" -> "${row.text("show_title")} · ${episodeTitle(row)}"
        else -> mediaTitle(row)
    }
    val overview = row.text("overview_display").ifBlank { row.text("overview") }
    val versions = row.rows("versions")
    val movieVersion = versions.firstOrNull { it.optLong("id") == selectedVersion }
    val playableId = if (route.kind == "movie") selectedVersion else route.id
    val canPlay = route.kind in listOf("movie", "episode")
    val watched = when (route.kind) {
        "show", "season" -> row.optInt("episode_count") > 0 && row.optInt("watched_count") >= row.optInt("episode_count")
        else -> row.optInt("watched") == 1
    }
    LaunchedEffect(row, requestEpisodeFocus) {
        if (route.kind == "season" && requestEpisodeFocus) {
            // Hero, actions, optional error, then the heading and compact grid.
            listState.scrollToItem(if (actionError.isBlank()) 2 else 3)
        }
    }
    fun markWatched() {
        if (actionBusy) return
        actionBusy = true; actionError = ""
        scope.launch {
            try {
                if (route.kind == "movie") api.post("/api/movies/batch", JSONObject().put("ids", JSONArray().put(route.id)).put("ops", JSONObject().put("watched", !watched)))
                else api.post(when (route.kind) {
                    "show" -> "/api/tv/shows/${route.id}/watched"
                    "season" -> "/api/tv/shows/${route.id}/seasons/${route.season}/watched"
                    else -> "/api/tv/episodes/${route.id}/watched"
                }, JSONObject().put("watched", !watched))
                attempt++
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { actionError = e.message ?: "观看状态保存失败" }
            finally { actionBusy = false }
        }
    }
    LazyColumn(state = listState, verticalArrangement = Arrangement.spacedBy(22.dp), contentPadding = PaddingValues(bottom = 32.dp)) {
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(24.dp)) {
                Poster(api, posterPath(row), title, Modifier.width(144.dp).height(202.dp))
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                    Text(title, style = MaterialTheme.typography.headlineMedium, maxLines = 3, overflow = TextOverflow.Ellipsis)
                    Text(listOf(row.text("year").ifBlank { row.text("show_year") }, row.text("origin_country_name"), row.text("library_name"),
                        if (watched) "已看" else "", row.text("vote_average").takeIf { it.isNotBlank() && it != "0.0" }?.let { "评分 $it" }.orEmpty())
                        .filter { it.isNotBlank() }.joinToString(" · "), color = Muted)
                    if (canPlay) {
                        LazyRow(horizontalArrangement = Arrangement.spacedBy(16.dp), contentPadding = PaddingValues(6.dp)) {
                            item { TvAction("▶ 播放 / 继续观看", { play(PlaybackRequest(if (route.kind == "movie") "movie" else "episode", playableId, title, true)) },
                                Modifier.focusMemory(memory, "play"), enabled = row.optBoolean("exists", true) && movieVersion?.optInt("missing", 0) != 1) }
                            item { TvAction("从头开始", { play(PlaybackRequest(if (route.kind == "movie") "movie" else "episode", playableId, title, false)) },
                                Modifier.focusMemory(memory, "restart"), enabled = row.optBoolean("exists", true) && movieVersion?.optInt("missing", 0) != 1) }
                        }
                    }
                    val next = row.optJSONObject("next_episode")
                    if (next != null && route.kind in listOf("show", "season")) TvAction("▶ ${episodeTitle(next)}", {
                        play(PlaybackRequest("episode", next.optLong("id"), "${row.text("show_title").ifBlank { title }} · ${episodeTitle(next)}", true))
                    }, Modifier.focusMemory(memory, "play"))
                    if (row.optInt("missing") == 1 || !row.optBoolean("exists", true)) Text("此文件目前离线或不存在", color = Color(0xFFFFB4AB))
                }
            }
        }
        if (route.kind != "collection") item {
            LazyRow(horizontalArrangement = Arrangement.spacedBy(16.dp), contentPadding = PaddingValues(6.dp)) {
                if (route.kind == "movie" && versions.size > 1) item {
                    TvAction("版本：${versionLabel(movieVersion ?: JSONObject().put("id", selectedVersion))}", { choosingVersion = true }, Modifier.focusMemory(memory, "version"))
                }
                if (route.kind == "season" && versions.size > 1) item {
                    TvAction(if (seasonVersion.isBlank()) "全部版本" else "版本 $seasonVersion", { choosingVersion = true }, Modifier.focusMemory(memory, "version"))
                }
                item { TvAction(if (watched) "标记未看" else "标记已看", ::markWatched, Modifier.focusMemory(memory, "watched"), enabled = !actionBusy) }
                item { TvAction("刷新", { attempt++ }, Modifier.focusMemory(memory, "refresh")) }
                if (route.kind == "episode") item { TvAction("返回剧集", { navigate(TvRoute("show", row.optLong("show_id"))) }, Modifier.focusMemory(memory, "show")) }
            }
        }
        if (actionError.isNotBlank()) item { Status(actionError, ::markWatched) }
        if (route.kind == "show") {
            item { SectionHeading("选季", "${row.optInt("episode_count")} 集 · 已看 ${row.optInt("watched_count")} 集") }
            items(row.rows("seasons"), key = { "season:${it.optInt("season")}" }) { season ->
                val number = season.optInt("season")
                TvAction("${season.text("name").ifBlank { if (number == 0) "特别篇" else "第 $number 季" }}  ·  ${season.optInt("total")} 集  ·  已看 ${season.optInt("watched_count")} 集", {
                    navigate(TvRoute("season", route.id, number, title))
                }, Modifier.fillMaxWidth().focusMemory(memory, "season:$number"))
            }
            if (row.rows("seasons").isEmpty()) item { Status("暂时没有已入库的分集") }
        }
        if (route.kind == "season") {
            item { SectionHeading("选集", "共 ${row.optInt("total")} 项 · 第 ${page + 1} 页") }
            item(key = "episodes:$page:$seasonVersion") {
                val episodes = row.rows("episodes").map { episodeButtonModel(it, versions.size > 1 && seasonVersion.isBlank()) }
                EpisodeGrid(episodes, memory, focusedEpisodeId, { focusedEpisodeId = it },
                    { navigate(TvRoute("episode", it)) }, requestEpisodeFocus, { requestEpisodeFocus = false })
            }
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(18.dp)) {
                    if (page > 0) TvAction("上一页", { focusEpisodesAfterLoad = true; page-- }, Modifier.focusMemory(memory, "previous-page"))
                    if (row.optBoolean("has_more")) TvAction("下一页", { focusEpisodesAfterLoad = true; page++ }, Modifier.focusMemory(memory, "next-page"))
                }
            }
        }
        if (route.kind == "episode") item {
            LazyRow(horizontalArrangement = Arrangement.spacedBy(16.dp), contentPadding = PaddingValues(6.dp)) {
                row.optJSONObject("previous_episode")?.let { previous -> item { TvAction("上一集", { navigate(TvRoute("episode", previous.optLong("id"))) }, Modifier.focusMemory(memory, "previous-episode")) } }
                row.optJSONObject("next_episode")?.let { next -> item { TvAction("下一集", { play(PlaybackRequest("episode", next.optLong("id"), episodeTitle(next))) }, Modifier.focusMemory(memory, "next-episode")) } }
            }
        }
        if (route.kind == "collection") {
            item { Text("${row.optInt("member_count")} 部影片", color = Muted) }
            val members = row.rows("members")
            items(members.chunked(collectionColumns), key = { "members:${it.first().optLong("id")}" }) { group ->
                Row(horizontalArrangement = Arrangement.spacedBy(18.dp), modifier = Modifier.padding(8.dp)) {
                    group.forEach { member ->
                        MediaCard(api, member, { navigate(TvRoute("movie", member.optLong("id"))) }, Modifier.focusMemory(memory, "member:${member.optLong("id")}"))
                    }
                }
            }
            if (members.isEmpty()) item { Status("合集中还没有影片") }
        }
        if (overview.isNotBlank()) item {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("剧情简介", style = MaterialTheme.typography.headlineSmall)
                Text(overview, color = Muted, maxLines = 4, overflow = TextOverflow.Ellipsis)
                TvAction("查看完整简介", { textDialog = "剧情简介" to overview.chunked(240) }, Modifier.focusMemory(memory, "overview"))
            }
        }
        if (row.rows("extras").isNotEmpty()) {
            item { SectionHeading("花絮与特别内容") }
            items(row.rows("extras"), key = { "extra:${it.optLong("id")}" }) { extra ->
                TvAction("▶ ${extra.text("label").ifBlank { "花絮" }} · ${mediaTitle(extra)}", {
                    play(PlaybackRequest("extra", extra.optLong("id"), mediaTitle(extra), true))
                }, Modifier.fillMaxWidth().focusMemory(memory, "extra:${extra.optLong("id")}"), enabled = extra.optBoolean("exists", true))
            }
        }
        val cast = row.rows(if (route.kind == "movie") "persons" else "cast")
        if (cast.isNotEmpty()) item {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("演职员", style = MaterialTheme.typography.headlineSmall)
                Text(cast.take(8).joinToString(" · ") { it.text("name") }, color = Muted)
                TvAction("查看演职员（${cast.size}）", {
                    textDialog = "演职员" to cast.map { person -> person.text("name") + person.text("character").ifBlank { person.text("character_name").ifBlank { person.text("role") } }.let { if (it.isBlank()) "" else " · $it" } }
                }, Modifier.focusMemory(memory, "cast"))
            }
        }
        if (row.rows("collections").isNotEmpty()) item {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                SectionHeading("所属合集")
                LazyRow(horizontalArrangement = Arrangement.spacedBy(16.dp), contentPadding = PaddingValues(8.dp)) {
                    items(row.rows("collections"), key = { it.optLong("id") }) { collection ->
                        TvAction(collection.text("name"), { navigate(TvRoute("collection", collection.optLong("id"))) }, Modifier.focusMemory(memory, "collection:${collection.optLong("id")}"))
                    }
                }
            }
        }
        if (similar.isNotEmpty()) item { MediaRail("库中相关推荐", similar, api, memory, "similar") { navigate(TvRoute(route.kind, it.optLong("id"))) } }
        if (similarError.isNotBlank()) item { Status(similarError, { attempt++ }) }
    }
    if (choosingVersion) {
        val choices = if (route.kind == "movie") versions.map { it.optLong("id").toString() to versionLabel(it) }
            else listOf("" to "全部版本") + versions.map { it.optInt("version").toString() to "版本 ${it.optInt("version")} · ${it.optInt("count")} 集" }
        ChoiceDialog("选择版本", choices, if (route.kind == "movie") selectedVersion.toString() else seasonVersion, { choosingVersion = false }) { chosen ->
            if (route.kind == "movie") selectedVersion = chosen.toLong() else if (seasonVersion != chosen) {
                focusEpisodesAfterLoad = true; seasonVersion = chosen; page = 0
            }
            choosingVersion = false
        }
    }
    textDialog?.let { (heading, lines) -> TextDialog(heading, lines) { textDialog = null } }
}

private fun versionLabel(row: JSONObject): String = listOf(row.text("edition"), row.text("spec"), row.text("file_path").substringAfterLast('/'))
    .filter { it.isNotBlank() }.joinToString(" · ").ifBlank { "版本 ${row.optLong("id")}" }

@Composable
private fun TextDialog(title: String, lines: List<String>, onClose: () -> Unit) {
    val memory = rememberFocusMemory("text:0")
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Column(Modifier.fillMaxWidth(.86f).heightIn(max = 520.dp).background(Panel, RoundedCornerShape(16.dp)).padding(28.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Text(title, style = MaterialTheme.typography.headlineSmall)
            LazyColumn(Modifier.weight(1f, fill = false), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                items(lines.size) { index ->
                    var focused by remember { mutableStateOf(false) }
                    Text(lines[index], modifier = Modifier.fillMaxWidth().focusMemory(memory, "text:$index")
                        .onFocusChanged { focused = it.isFocused }.border(2.dp, if (focused) Color.White else Color.Transparent, RoundedCornerShape(6.dp))
                        .focusable().padding(12.dp))
                }
            }
            TvAction("关闭", onClose)
        }
    }
}
