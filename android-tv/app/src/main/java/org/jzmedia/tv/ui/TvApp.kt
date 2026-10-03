package org.jzmedia.tv.ui

import android.os.Build
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.isImeVisible
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.listSaver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.unit.dp
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.jzmedia.tv.BuildConfig
import org.jzmedia.tv.data.ConnectionStore
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.SavedConnection
import org.jzmedia.tv.data.rows
import org.jzmedia.tv.data.text
import org.jzmedia.tv.playback.PlaybackRequest
import org.jzmedia.tv.playback.TvPlayer
import org.json.JSONObject
import java.util.UUID

data class TvRoute(val kind: String, val id: Long = 0, val season: Int = 0, val title: String = "", val key: String = UUID.randomUUID().toString()) {
    fun encode(): String = JSONObject().put("kind", kind).put("id", id).put("season", season).put("title", title).put("key", key).toString()
    companion object {
        fun decode(value: String): TvRoute = JSONObject(value).let { TvRoute(it.getString("kind"), it.optLong("id"), it.optInt("season"), it.text("title"), it.getString("key")) }
    }
}

@Composable
fun TvApp(onExit: () -> Unit) {
    val context = LocalContext.current
    val store = remember { ConnectionStore(context) }
    var loaded by remember { mutableStateOf(false) }
    var saved by remember { mutableStateOf<SavedConnection?>(null) }
    var restoreError by remember { mutableStateOf("") }
    var api by remember { mutableStateOf<JzApi?>(null) }
    var editConnection by remember { mutableStateOf(false) }
    var library by rememberSaveable { mutableStateOf(0L) }
    var libraryName by rememberSaveable { mutableStateOf("全部媒体库") }
    var refresh by remember { mutableStateOf(0) }
    var playback by remember { mutableStateOf<PlaybackRequest?>(null) }
    var routes by rememberSaveable(stateSaver = listSaver<List<TvRoute>, String>(
        save = { rows -> rows.map { it.encode() } }, restore = { rows -> rows.map(TvRoute::decode) },
    )) { mutableStateOf(listOf(TvRoute("home", key = "root:home"))) }
    val holder = rememberSaveableStateHolder()
    val scope = rememberCoroutineScope()

    LaunchedEffect(Unit) {
        try { saved = withContext(Dispatchers.IO) { store.load() } }
        catch (_: Exception) { restoreError = "保存的连接无法读取，请重新输入服务器地址和令牌" }
        loaded = true
    }

    if (!loaded) { Status("正在读取连接…"); return }
    if (api == null || editConnection) {
        ConnectionScreen(saved, restoreError, autoConnect = api == null && saved != null,
            onConnected = { connected, connection ->
                val changed = saved?.address != connection.address || saved?.token != connection.token
                api = connected; saved = connection; editConnection = false; restoreError = ""
                if (changed) {
                    routes = listOf(TvRoute("home", key = "root:home")); library = 0; libraryName = "全部媒体库"
                }
                refresh++
            }, save = { withContext(Dispatchers.IO) { store.save(it) } },
            onBack = if (api != null) ({ editConnection = false }) else onExit)
        return
    }
    val server = api!!
    if (playback != null) {
        TvPlayer(server, playback!!, onClose = { playback = null; refresh++ }, onPlayNext = { playback = it })
        return
    }
    val route = routes.last()
    fun back() { if (routes.size > 1) routes = routes.dropLast(1) else onExit() }
    BackHandler { back() }
    fun navigate(next: TvRoute) { routes = routes + next }
    holder.SaveableStateProvider("${server.baseUrl}:$library:${route.key}") {
        val memory = rememberFocusMemory(if (route.kind in listOf("home", "movies", "shows", "collections")) "nav:${route.kind}" else "back")
        fun open(next: TvRoute) { memory.leaving = true; navigate(next) }
        fun play(request: PlaybackRequest) { memory.leaving = true; playback = request }
        Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background).padding(horizontal = 40.dp, vertical = 24.dp),
            verticalArrangement = Arrangement.spacedBy(20.dp)) {
            LazyRow(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                item { Text("jzmedia", style = MaterialTheme.typography.headlineSmall, modifier = Modifier.padding(end = 16.dp, top = 5.dp)) }
                items(listOf("home" to "首页", "movies" to "电影", "shows" to "电视剧", "collections" to "合集")) { (kind, label) ->
                    TvAction(label, { if (route.key != "root:$kind") memory.leaving = true; routes = listOf(TvRoute(kind, key = "root:$kind")) },
                        Modifier.focusMemory(memory, "nav:$kind"), selected = route.kind == kind)
                }
                item { TvAction(libraryName, { open(TvRoute("libraries")) }, Modifier.focusMemory(memory, "nav:libraries")) }
                item { TvAction("设置", { open(TvRoute("settings")) }, Modifier.focusMemory(memory, "nav:settings")) }
            }
            if (routes.size > 1) TvAction("‹ 返回", { back() }, Modifier.focusMemory(memory, "back"))
            when (route.kind) {
                "home" -> HomeScreen(server, library, refresh, memory, ::open, ::play)
                "movies", "shows", "collections" -> BrowseScreen(server, route.kind, library, refresh, memory, ::open)
                "movie", "show", "season", "episode", "collection" -> DetailScreen(server, route, refresh, memory, ::open, ::play)
                "libraries" -> LibrariesScreen(server, library, memory) { id, name ->
                    library = id; libraryName = name; routes = listOf(TvRoute("home", key = "root:home"))
                }
                "settings" -> LazyColumn(verticalArrangement = Arrangement.spacedBy(18.dp)) {
                    item { SectionHeading("电视设置") }
                    item { Text("当前服务器：${server.baseUrl}", color = Muted) }
                    item { TvAction("修改连接", { editConnection = true }, Modifier.focusMemory(memory, "settings:connection")) }
                    item { Text("设备：${Build.MANUFACTURER} ${Build.MODEL}") }
                    item { Text("Android ${Build.VERSION.RELEASE} · API ${Build.VERSION.SDK_INT}") }
                    item { Text("电视应用 ${BuildConfig.VERSION_NAME} · 播放协议 1") }
                    item { Text("观看进度全家共享。媒体库扫描、整理和匹配请在网页端操作。", color = Muted) }
                    item { TvAction("忘记连接", {
                        scope.launch { withContext(Dispatchers.IO) { store.clear() }; saved = null; api = null }
                    }, Modifier.focusMemory(memory, "settings:forget")) }
                    item { TvAction("退出应用", onExit, Modifier.focusMemory(memory, "settings:exit")) }
                }
            }
        }
    }
}

@Composable
@OptIn(ExperimentalLayoutApi::class)
private fun ConnectionScreen(initial: SavedConnection?, initialError: String, autoConnect: Boolean,
    onConnected: (JzApi, SavedConnection) -> Unit, save: suspend (SavedConnection) -> Unit, onBack: () -> Unit) {
    var address by rememberSaveable { mutableStateOf(initial?.address.orEmpty()) }
    // The token is never written to saved-instance state or screenshots of plain text fields.
    var token by remember { mutableStateOf(initial?.token.orEmpty()) }
    var error by remember { mutableStateOf(initialError) }
    var busy by remember { mutableStateOf(false) }
    var job by remember { mutableStateOf<Job?>(null) }
    val scope = rememberCoroutineScope()
    val memory = rememberFocusMemory("connect")
    val keyboard = LocalSoftwareKeyboardController.current
    val imeVisible = WindowInsets.isImeVisible
    fun connect() {
        if (busy) return
        busy = true; error = ""
        val chosenAddress = address
        val chosenToken = token
        job = scope.launch {
            try {
                val candidate = JzApi(chosenAddress, chosenToken)
                candidate.checkConnection()
                val connection = SavedConnection(candidate.baseUrl, chosenToken)
                save(connection)
                onConnected(candidate, connection)
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { error = e.message ?: "连接失败，请重试" }
            finally { busy = false }
        }
    }
    LaunchedEffect(Unit) { if (autoConnect) connect() }
    BackHandler {
        if (imeVisible) keyboard?.hide()
        else if (busy) { job?.cancel(); busy = false }
        else onBack()
    }
    LazyColumn(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background).padding(horizontal = 64.dp, vertical = 36.dp),
        verticalArrangement = Arrangement.spacedBy(20.dp)) {
        item { Text("连接 jzmedia", style = MaterialTheme.typography.displaySmall) }
        item { Text("让电视和服务器连接同一个家庭网络，然后填写网页端使用的服务器地址。", color = Muted) }
        item { TvInput("服务器地址，例如 http://192.168.1.10:8080", address, { address = it }, Modifier.widthIn(max = 740.dp)) }
        item { TvInput("访问令牌（服务器未设置时留空）", token, { token = it }, Modifier.widthIn(max = 740.dp), password = true) }
        if (error.isNotBlank()) item { Text(error, color = androidx.compose.ui.graphics.Color(0xFFFFB4AB)) }
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(20.dp)) {
                TvAction(if (busy) "正在连接…" else "连接并记住", ::connect, Modifier.focusMemory(memory, "connect"), enabled = !busy)
                TvAction(if (busy) "取消连接" else "返回", { if (busy) { job?.cancel(); busy = false } else onBack() })
            }
        }
        item { Text("方向键移动 · 确定键编辑或选择 · 返回键关闭键盘或返回", color = Muted) }
    }
}

@Composable
private fun LibrariesScreen(api: JzApi, selected: Long, memory: FocusMemory, onSelect: (Long, String) -> Unit) {
    var rows by remember { mutableStateOf<List<JSONObject>?>(null) }
    var error by remember { mutableStateOf("") }
    var attempt by remember { mutableStateOf(0) }
    LaunchedEffect(attempt) {
        error = ""
        try { rows = api.get("/api/media-libraries").rows().filter { it.optBoolean("enabled", true) } }
        catch (e: CancellationException) { throw e }
        catch (e: Exception) { error = e.message ?: "媒体库读取失败" }
    }
    LazyColumn(verticalArrangement = Arrangement.spacedBy(16.dp)) {
        item { SectionHeading("选择媒体库") }
        item { TvAction("全部媒体库", { onSelect(0, "全部媒体库") }, Modifier.focusMemory(memory, "library:0"), selected == 0L) }
        if (error.isNotBlank()) item { Status(error, { attempt++ }) }
        else if (rows == null) item { Status("正在读取媒体库…") }
        items(rows.orEmpty(), key = { it.optLong("id") }) { row ->
            TvAction(row.text("name"), { onSelect(row.optLong("id"), row.text("name")) },
                Modifier.focusMemory(memory, "library:${row.optLong("id")}"), selected == row.optLong("id"))
        }
    }
}
