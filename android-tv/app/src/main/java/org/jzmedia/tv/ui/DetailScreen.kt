package org.jzmedia.tv.ui

import org.jzmedia.tv.ui.generated.DesignTokens
import androidx.compose.foundation.background
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.gestures.BringIntoViewSpec
import androidx.compose.foundation.gestures.LocalBringIntoViewSpec
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
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
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalWindowInfo
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.castActorKey
import org.jzmedia.tv.data.detailBackdropPath
import org.jzmedia.tv.data.detailCastList
import org.jzmedia.tv.data.episodeTitle
import org.jzmedia.tv.data.mediaTitle
import org.jzmedia.tv.data.posterPath
import org.jzmedia.tv.data.queryPath
import org.jzmedia.tv.data.rows
import org.jzmedia.tv.data.text
import org.jzmedia.tv.playback.PlaybackRequest
import org.json.JSONArray
import org.json.JSONObject

@OptIn(ExperimentalFoundationApi::class)
@Composable
fun DetailScreen(api: JzApi, route: TvRoute, refresh: Int, memory: FocusMemory, navigate: (TvRoute) -> Unit,
                 play: (PlaybackRequest) -> Unit, onBack: () -> Unit) {
    var data by remember { mutableStateOf<JSONObject?>(null) }
    var similar by remember { mutableStateOf<List<JSONObject>>(emptyList()) }
    var error by remember { mutableStateOf("") }
    var similarError by remember { mutableStateOf("") }
    var similarSettled by remember { mutableStateOf(false) }
    var actionError by remember { mutableStateOf("") }
    var actionBusy by remember { mutableStateOf(false) }
    var attempt by remember { mutableStateOf(0) }
    var page by rememberSaveable { mutableStateOf(0) }
    var selectedVersion by rememberSaveable { mutableStateOf(0L) }
    var seasonVersion by rememberSaveable { mutableStateOf("") }
    var focusedEpisodeId by rememberSaveable { mutableStateOf(0L) }
    var focusAfterLoad by remember { mutableStateOf<String?>(null) }
    var requestedFocus by remember { mutableStateOf<String?>(null) }
    var episodeHeaderHeight by remember { mutableStateOf(0) }
    var choosingVersion by remember { mutableStateOf(false) }
    var moreOpen by remember { mutableStateOf(false) }
    var textDialog by remember { mutableStateOf<Pair<String, List<String>>?>(null) }
    val scope = rememberCoroutineScope()
    // Keep scroll state while data is reloaded and when returning from a child episode.
    val listState = rememberLazyListState()
    val windowInfo = LocalWindowInfo.current
    val contentWidth = with(LocalDensity.current) { windowInfo.containerSize.width.toDp().value.toInt() } - 80
    val collectionColumns = (contentWidth / 168).coerceAtLeast(1)
    val seasonColumns = seasonGridColumns(contentWidth)
    val episodeColumns = episodeGridColumns(contentWidth - 16)
    val requesters = remember { mutableMapOf<String, FocusRequester>() }
    fun targetModifier(key: String): Modifier {
        val requester = requesters.getOrPut(key) { FocusRequester() }
        return Modifier.focusMemory(memory, key, requester).composed {
            LaunchedEffect(requestedFocus) {
                if (requestedFocus == key) {
                    withFrameNanos { }
                    if (requester.requestFocus(FocusDirection.Enter)) {
                        memory.restored = true
                        requestedFocus = null
                    }
                }
            }
            Modifier
        }
    }
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
            if (route.kind == "movie" && data!!.rows("versions").none { it.optLong("id") == selectedVersion }) {
                selectedVersion = data!!.rows("versions").firstOrNull()?.optLong("id") ?: route.id
            }
        } catch (e: CancellationException) { throw e }
        catch (e: Exception) { error = e.message ?: "详情读取失败" }
    }
    LaunchedEffect(api, route, refresh, attempt) {
        similarSettled = false
        similar = emptyList()
        similarError = ""
        if (route.kind != "movie" && route.kind != "show") {
            similarSettled = true
            return@LaunchedEffect
        }
        try {
            similar = api.get(if (route.kind == "movie") "/api/movies/${route.id}/similar?limit=12" else "/api/tv/shows/${route.id}/similar?limit=12").rows()
            similarSettled = true
        }
        catch (e: CancellationException) { throw e }
        catch (_: Exception) { similarError = "相关推荐暂时不可用"; similarSettled = true }
    }
    if (error.isNotBlank()) {
        LaunchedEffect(error) { requestedFocus = "retry" }
        Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Text("影片详情", style = MaterialTheme.typography.headlineMedium)
            Status(error, icon = "error")
            TvAction("重试", { focusAfterLoad = "detail:primary"; attempt++ }, targetModifier("retry"), icon = "refresh")
        }
        return
    }
    val row = data ?: run {
        Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Text("影片详情", style = MaterialTheme.typography.headlineMedium)
            Status("正在读取详情…", icon = "loading")
        }
        return
    }
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
    val next = row.optJSONObject("next_episode")
    val offline = knownOffline(row) || (route.kind == "movie" && knownOffline(movieVersion))
    val nextOffline = knownOffline(next)
    val seasons = row.rows("seasons")
    val episodeRows = row.rows("episodes")
    val episodes = episodeRows.map { episodeButtonModel(it, versions.size > 1 && seasonVersion.isBlank()) }
    val focusedEpisode = episodes.firstOrNull { it.id == focusedEpisodeId } ?: episodes.firstOrNull()
    val watched = when (route.kind) {
        "show", "season" -> row.optInt("episode_count") > 0 && row.optInt("watched_count") >= row.optInt("episode_count")
        else -> row.optInt("watched") == 1
    }
    val primaryKey = detailPrimaryFocusKey(route.kind, row, movieVersion)
    LaunchedEffect(row, similarSettled, similar) {
        // The loading/error header and loaded hero contain different back nodes.
        // Carry the current back focus across that replacement as well as a route restore.
        // 屏幕返回键已取消（遥控器返回由全局 BackHandler 兜底）；残留的 "back"
        // 记忆键（老版本存的）一律落到 primary，不再是有效目标。
        val saved = focusAfterLoad ?: memory.restoreKey.takeUnless { memory.restored } ?: return@LaunchedEffect
        if (!detailFocusReady(saved, similarSettled)) return@LaunchedEffect
        val available = detailFocusKeys(route.kind, row, page, movieVersion) + similar.map { "similar:${it.optLong("id")}" }
        val target = resolveDetailFocusKey(saved, primaryKey, available, episodes.firstOrNull()?.let { "episode:${it.id}" })
        if (target.startsWith("nav:")) {
            focusAfterLoad = null
            return@LaunchedEffect
        }
        val gridStart = 2 + if (actionError.isBlank()) 0 else 1
        val index = when {
            target.startsWith("season:") -> gridStart + seasons.indexOfFirst { "season:${it.optInt("season")}" == target }.coerceAtLeast(0) / seasonColumns
            target.startsWith("episode:") -> gridStart + episodes.indexOfFirst { "episode:${it.id}" == target }.coerceAtLeast(0) / episodeColumns
            target.startsWith("member:") -> gridStart + row.rows("members").indexOfFirst { "member:${it.optLong("id")}" == target }.coerceAtLeast(0) / collectionColumns
            target.startsWith("cast:") -> {
                // 演职员独占一个 item，位置跟随上方各区（季/集行、花絮）。
                var castIndex = gridStart
                if (route.kind == "show") castIndex += 1 + ((seasons.size + seasonColumns - 1) / seasonColumns) +
                    if (seasons.isEmpty()) 1 else 0
                if (route.kind == "season") castIndex += 1 + ((episodes.size + episodeColumns - 1) / episodeColumns) +
                    if (page > 0 || row.optBoolean("has_more")) 1 else 0 + 1
                if (route.kind == "episode") castIndex += 1
                if (route.kind == "collection") {
                    val members = row.rows("members")
                    castIndex += 1 + ((members.size + collectionColumns - 1) / collectionColumns) +
                        if (members.isEmpty()) 1 else 0
                }
                val extras = row.rows("extras")
                if (extras.isNotEmpty()) castIndex += 1 + extras.size
                castIndex
            }
            target in setOf("play", "restart", "version", "more") -> 0
            else -> null
        }
        withFrameNanos { }
        // The late recommendation row can appear after the saved scroll position
        // was clamped to the shorter detail-only list. Make its focus target real.
        val restoredIndex = if (target.startsWith("similar:")) (listState.layoutInfo.totalItemsCount - 1).coerceAtLeast(0) else index
        if (restoredIndex != null && listState.layoutInfo.visibleItemsInfo.none { it.index == restoredIndex }) listState.scrollToItem(restoredIndex)
        requestedFocus = target
        focusAfterLoad = null
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
                focusAfterLoad = "more"; attempt++
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { actionError = e.message ?: "观看状态保存失败" }
            finally { actionBusy = false }
        }
    }
    val parentScrollSpec = LocalBringIntoViewSpec.current
    val focusMargin = with(LocalDensity.current) { 16.dp.toPx() }
    val scrollSpec = remember(parentScrollSpec, focusMargin, route.kind, episodeHeaderHeight) {
        if (route.kind !in listOf("show", "season")) parentScrollSpec else object : BringIntoViewSpec {
            override fun calculateScrollDistance(offset: Float, size: Float, containerSize: Float): Float {
                if (route.kind == "season") {
                    // Keep the compact hero in view until scrolling is necessary.
                    // Only episode buttons must stay below the pinned context.
                    return seasonFocusScrollDistance(offset, size, containerSize, episodeHeaderHeight.toFloat(),
                        focusMargin, memory.key.startsWith("episode:"))
                }
                // TV's default pivot can place a tall card flush with the viewport
                // bottom. Include its focused scale when choosing the scroll target.
                val margin = minOf(focusMargin, ((containerSize - size) / 2f).coerceAtLeast(0f))
                return parentScrollSpec.calculateScrollDistance(offset - margin, size + margin * 2, containerSize)
            }
        }
    }
    CompositionLocalProvider(LocalBringIntoViewSpec provides scrollSpec) {
        LazyColumn(state = listState, verticalArrangement = Arrangement.spacedBy(12.dp), contentPadding = PaddingValues(top = 8.dp, bottom = 32.dp)) {
            item(key = "hero") {
                // 网页详情大海报观感：固定 2:3 展示尺寸（清晰度由来图决定，布局尺寸不变）。
                // 背景盖顶部介绍区（海报+标题+操作+简介，网页同口径），合集无背景。
                // 回到顶部介绍区时吸顶到首项，避免海报半截悬空。
                val heroPosterWidth = if (contentWidth < 700) 120.dp else 168.dp
                Box(Modifier.fillMaxWidth().onFocusChanged { if (it.hasFocus) scope.launch { listState.scrollToItem(0) } }) {
                    DetailBackdrop(api, detailBackdropPath(route.kind, row, route.id), Modifier.matchParentSize())
                    // 背景出血：上下左右各留边，背景高于海报、低于简介按钮，不与海报齐平。
                    Row(Modifier.padding(start = 20.dp, end = 20.dp, top = 28.dp, bottom = 24.dp),
                        horizontalArrangement = Arrangement.spacedBy(18.dp)) {
                    Box {
                        Poster(api, posterPath(row), title,
                            Modifier.width(heroPosterWidth).height(heroPosterWidth * 1.5f),
                            fallbackPosterPath = detailFallbackPoster(row))
                        if (watched) WatchedBadge(Modifier.align(Alignment.TopStart).padding(5.dp))
                    }
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        Text(title, style = MaterialTheme.typography.headlineMedium, maxLines = 2,
                            overflow = TextOverflow.Ellipsis)
                        Text(listOf(row.text("year").ifBlank { row.text("show_year") }, row.text("origin_country_name"), row.text("library_name"),
                            org.jzmedia.tv.data.mediaRatingLabel(row))
                            .filter { it.isNotBlank() }.joinToString(" · "), color = Muted, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        if (watched) WatchedBadge()
                        LazyRow(horizontalArrangement = Arrangement.spacedBy(12.dp), contentPadding = PaddingValues(8.dp)) {
                            if (canPlay) {
                                item { TvAction(if (offline) "文件离线" else "播放 / 继续观看", {
                                    if (!offline) play(PlaybackRequest(if (route.kind == "movie") "movie" else "episode", playableId, title, true))
                                }, targetModifier("play"), enabled = !offline, icon = if (offline) "error" else "play") }
                                item { TvAction("从头开始", {
                                    if (!offline) play(PlaybackRequest(if (route.kind == "movie") "movie" else "episode", playableId, title, false))
                                }, targetModifier("restart"), enabled = !offline, icon = "replay") }
                            }
                            if (next != null && route.kind in listOf("show", "season")) item {
                                val label = episodeTitle(next).substringBefore("  ")
                                val hasProgress = (next.optJSONObject("progress")?.optDouble("position") ?: next.optDouble("_pos")) > 0
                                TvAction(if (nextOffline) "$label · 离线" else "${if (hasProgress) "继续观看" else "播放"} · $label", {
                                    if (!nextOffline) play(PlaybackRequest("episode", next.optLong("id"), "${row.text("show_title").ifBlank { title }} · ${episodeTitle(next)}", true))
                                }, targetModifier("play"), enabled = !nextOffline, icon = if (nextOffline) "error" else "play")
                            }
                            if (route.kind == "movie" && versions.size > 1) item {
                                TvAction("版本：${versionLabel(movieVersion ?: JSONObject().put("id", selectedVersion))}", { choosingVersion = true },
                                    targetModifier("version").widthIn(max = 260.dp), icon = "collections")
                            }
                            if (route.kind == "season" && versions.size > 1) item {
                                TvAction(if (seasonVersion.isBlank()) "全部版本" else "版本 $seasonVersion", { choosingVersion = true }, targetModifier("version"), icon = "collections")
                            }
                            item {
                                TvAction(if (actionBusy) "保存中…" else "更多操作", { moreOpen = true }, targetModifier("more"), enabled = !actionBusy, icon = if (actionBusy) "loading" else "more")
                            }
                        }
                        if (offline || (route.kind in listOf("show", "season") && next != null && nextOffline))
                            Text("此文件目前离线，请选择其它分集或版本", color = DesignTokens.Error)
                        if (overview.isNotBlank()) {
                            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                Text(overview, color = Muted, maxLines = 3, overflow = TextOverflow.Ellipsis)
                                // 简介整段一块进弹窗，不按字数切片（切片会在句中切成两截）。
                                TvAction("查看完整简介", { textDialog = "剧情简介" to listOf(overview) }, targetModifier("overview"), icon = "info")
                            }
                        }
                    }
                    }
                }
            }
            if (actionError.isNotBlank()) item(key = "action-error") { Status(actionError, ::markWatched) }
            if (route.kind == "show") {
                item(key = "season-heading") { SectionHeading("选季", "${row.optInt("episode_count")} 集 · 已看 ${row.optInt("watched_count")} 集") }
                seasonGrid(api, seasons, posterPath(row), seasonColumns, ::targetModifier) { number -> navigate(TvRoute("season", route.id, number, title)) }
                if (seasons.isEmpty()) item { Status("暂时没有已入库的分集") }
            }
            if (route.kind == "season") {
                stickyHeader(key = "episode-heading") {
                    Column(Modifier.fillMaxWidth().onSizeChanged { episodeHeaderHeight = it.height }
                        .background(MaterialTheme.colorScheme.background).padding(vertical = 10.dp),
                        verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        SectionHeading("选集", "共 ${row.optInt("total")} 项 · 第 ${page + 1} 页")
                        Text(focusedEpisode?.description ?: "暂时没有已入库的分集", color = Muted, fontSize = 16.sp, lineHeight = 22.sp,
                            maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.fillMaxWidth().heightIn(min = 44.dp).padding(horizontal = 8.dp))
                    }
                }
                episodeGrid(episodes, episodeColumns, ::targetModifier, { focusedEpisodeId = it }, { navigate(TvRoute("episode", it)) })
                if (page > 0 || row.optBoolean("has_more")) item(key = "episode-pages") {
                    Row(horizontalArrangement = Arrangement.spacedBy(18.dp), modifier = Modifier.padding(8.dp)) {
                        if (page > 0) TvAction("上一页", { focusAfterLoad = "episode:first"; page-- }, targetModifier("previous-page"), icon = "back")
                        if (row.optBoolean("has_more")) TvAction("下一页", { focusAfterLoad = "episode:first"; page++ }, targetModifier("next-page"), icon = "forward")
                    }
                }
                // End the pinned selection context before the synopsis and cast.
                stickyHeader(key = "episode-context-end") { Row(Modifier.height(0.dp)) {} }
            }
            if (route.kind == "episode") item {
                LazyRow(horizontalArrangement = Arrangement.spacedBy(16.dp), contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp)) {
                    row.optJSONObject("previous_episode")?.let { previous -> item { TvAction("上一集", { navigate(TvRoute("episode", previous.optLong("id"))) }, targetModifier("previous-episode"), icon = "previous") } }
                    next?.let { following -> item { TvAction(if (nextOffline) "下一集 · 离线" else "下一集", {
                        if (!nextOffline) play(PlaybackRequest("episode", following.optLong("id"), episodeTitle(following)))
                    }, targetModifier("next-episode"), enabled = !nextOffline, icon = "next") } }
                    item { TvAction("返回剧集", { navigate(TvRoute("show", row.optLong("show_id"))) }, targetModifier("show"), icon = "tv") }
                }
            }
            if (route.kind == "collection") {
                item { Text("${row.optInt("member_count")} 部影片", color = Muted) }
                val members = row.rows("members")
                items(members.chunked(collectionColumns), key = { "members:${it.first().optLong("id")}" }) { group ->
                    Row(horizontalArrangement = Arrangement.spacedBy(18.dp), modifier = Modifier.padding(8.dp)) {
                        group.forEach { member ->
                            MediaCard(api, member, { navigate(TvRoute("movie", member.optLong("id"))) }, targetModifier("member:${member.optLong("id")}"))
                        }
                    }
                }
                if (members.isEmpty()) item { Status("合集中还没有影片") }
            }
            if (row.rows("extras").isNotEmpty()) {
                item { SectionHeading("花絮与特别内容") }
                items(row.rows("extras"), key = { "extra:${it.optLong("id")}" }) { extra ->
                    TvAction("${extra.text("label").ifBlank { "花絮" }} · ${mediaTitle(extra)}", {
                        if (!knownOffline(extra)) play(PlaybackRequest("extra", extra.optLong("id"), mediaTitle(extra), true))
                    }, targetModifier("extra:${extra.optLong("id")}").fillMaxWidth(), enabled = !knownOffline(extra), icon = "play", fillContent = true)
                }
            }
            val cast = detailCastList(route.kind, row)
            if (cast.isNotEmpty()) item {
                // 卡片墙与“库中相关推荐”同列宽：占满整列横滚，不套 720dp 文字限宽。
                CastWall(api, cast, row.text("original_language"), memory, { person ->
                    navigate(TvRoute("actor-works",
                        title = person.text("name").ifBlank { "演员作品" },
                        actorKey = castActorKey(person)))
                }, Modifier.padding(top = 8.dp))
            }
            if (row.rows("collections").isNotEmpty()) item {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    SectionHeading("所属合集")
                    LazyRow(horizontalArrangement = Arrangement.spacedBy(16.dp), contentPadding = PaddingValues(8.dp)) {
                        items(row.rows("collections"), key = { it.optLong("id") }) { collection ->
                            TvAction(collection.text("name"), { navigate(TvRoute("collection", collection.optLong("id"))) }, targetModifier("collection:${collection.optLong("id")}"))
                        }
                    }
                }
            }
            if (similar.isNotEmpty()) item { MediaRail("库中相关推荐", similar, api, memory, "similar") { navigate(TvRoute(route.kind, it.optLong("id"))) } }
            if (similarError.isNotBlank()) item { Status(similarError, { attempt++ }) }
        }
    }
    if (choosingVersion) {
        val choices = if (route.kind == "movie") versions.map { it.optLong("id").toString() to versionLabel(it) }
            else listOf("" to "全部版本") + versions.map { it.optInt("version").toString() to "版本 ${it.optInt("version")} · ${it.optInt("count")} 集" }
        ChoiceDialog("选择版本", choices, if (route.kind == "movie") selectedVersion.toString() else seasonVersion, { choosingVersion = false }) { chosen ->
            if (route.kind == "movie") selectedVersion = chosen.toLong() else if (seasonVersion != chosen) {
                focusAfterLoad = "episode:first"; seasonVersion = chosen; page = 0
            }
            choosingVersion = false
        }
    }
    if (moreOpen) {
        val choices = (if (route.kind != "collection") listOf("watched" to if (watched) "标记未看" else "标记已看") else emptyList()) +
            listOf("refresh" to "刷新资料")
        ChoiceDialog("更多操作", choices, choices.first().first, { moreOpen = false }) { chosen ->
            moreOpen = false
            when (chosen) {
                "watched" -> markWatched()
                "refresh" -> { focusAfterLoad = "more"; attempt++ }
            }
        }
    }
    textDialog?.let { (heading, lines) -> TextDialog(heading, lines) { textDialog = null } }
}

/** Only explicit server knowledge blocks playback; omitted legacy fields remain compatible. */
internal fun knownOffline(row: JSONObject?): Boolean = row != null &&
    (!row.optBoolean("exists", true) || row.optInt("missing") == 1)

internal fun detailFallbackPoster(row: JSONObject): String =
    posterPath(JSONObject().put("poster_path", row.text("show_poster")))

internal fun seasonFocusScrollDistance(offset: Float, size: Float, viewport: Float, headerHeight: Float,
                                       focusMargin: Float, episodeFocused: Boolean): Float {
    val reserved = if (episodeFocused) headerHeight.coerceIn(0f, (viewport - size).coerceAtLeast(0f)) else 0f
    val margin = focusMargin.coerceIn(0f, ((viewport - reserved - size) / 2f).coerceAtLeast(0f))
    val top = reserved + margin
    val bottom = viewport - margin
    val end = offset + size
    return when {
        offset >= top && end <= bottom -> 0f
        offset < top && end > bottom -> 0f // An oversized target already spans the available viewport.
        offset < top -> offset - top
        else -> end - bottom
    }
}

internal fun detailFocusReady(saved: String, recommendationsSettled: Boolean): Boolean =
    !saved.startsWith("similar:") || recommendationsSettled

internal fun resolveDetailFocusKey(saved: String, primary: String, available: Set<String>, firstEpisode: String?): String = when {
    saved == "episode:first" -> firstEpisode ?: "more"
    saved == "detail:primary" || (saved !in available && !saved.startsWith("nav:")) -> primary
    else -> saved
}

internal fun detailPrimaryFocusKey(kind: String, row: JSONObject, movieVersion: JSONObject? = null): String {
    if (kind in listOf("movie", "episode")) {
        if (!knownOffline(row) && !knownOffline(movieVersion)) return "play"
        return if (kind == "movie" && row.rows("versions").size > 1) "version" else "more"
    }
    val next = row.optJSONObject("next_episode")
    if (kind in listOf("show", "season") && next != null && !knownOffline(next)) return "play"
    if (kind == "show") {
        val seasons = row.rows("seasons")
        val current = seasons.firstOrNull { next != null && it.optInt("season") == next.optInt("season") }
            ?: seasons.firstOrNull { it.optBoolean("has_partial") }
            ?: seasons.firstOrNull { !it.optBoolean("done") } ?: seasons.firstOrNull()
        return current?.let { "season:${it.optInt("season")}" } ?: "more"
    }
    if (kind == "season") {
        val episodes = row.rows("episodes")
        val current = episodes.firstOrNull { !knownOffline(it) && it.optInt("watched") != 1 }
            ?: episodes.firstOrNull { !knownOffline(it) } ?: episodes.firstOrNull()
        return current?.let { "episode:${it.optLong("id")}" } ?: "more"
    }
    return row.rows("members").firstOrNull()?.let { "member:${it.optLong("id")}" } ?: "more"
}

internal fun detailFocusKeys(kind: String, row: JSONObject, page: Int, movieVersion: JSONObject? = null): Set<String> = buildSet {
    add("more")
    if (kind in listOf("movie", "episode") && !knownOffline(row) && !knownOffline(movieVersion)) addAll(listOf("play", "restart"))
    if (kind in listOf("show", "season") && row.optJSONObject("next_episode")?.let { !knownOffline(it) } == true) add("play")
    if (kind in listOf("movie", "season") && row.rows("versions").size > 1) add("version")
    if (kind == "show") row.rows("seasons").forEach { add("season:${it.optInt("season")}") }
    if (kind == "season") {
        row.rows("episodes").forEach { add("episode:${it.optLong("id")}") }
        if (page > 0) add("previous-page")
        if (row.optBoolean("has_more")) add("next-page")
    }
    if (kind == "episode") {
        add("show")
        if (row.optJSONObject("previous_episode") != null) add("previous-episode")
        if (row.optJSONObject("next_episode")?.let { !knownOffline(it) } == true) add("next-episode")
    }
    if (kind == "collection") row.rows("members").forEach { add("member:${it.optLong("id")}") }
    if (row.text("overview_display").ifBlank { row.text("overview") }.isNotBlank()) add("overview")
    row.rows("extras").filterNot(::knownOffline).forEach { add("extra:${it.optLong("id")}") }
    row.rows("collections").forEach { add("collection:${it.optLong("id")}") }
    detailCastList(kind, row).forEach { add("cast:${castActorKey(it)}") }
}

private fun versionLabel(row: JSONObject): String = listOf(row.text("edition"), row.text("spec"), row.text("file_path").substringAfterLast('/'))
    .filter { it.isNotBlank() }.joinToString(" · ").ifBlank { "版本 ${row.optLong("id")}" }
