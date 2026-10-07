package org.jzmedia.tv.ui

import org.jzmedia.tv.ui.generated.DesignTokens
import android.os.Build
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.isImeVisible
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.jzmedia.tv.BuildConfig
import org.jzmedia.tv.data.ConnectionStore
import org.jzmedia.tv.data.DiscoveredServer
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.SavedConnection
import org.jzmedia.tv.data.ServerDiscovery
import org.jzmedia.tv.data.rows
import org.jzmedia.tv.data.text
import org.jzmedia.tv.playback.PlaybackRequest
import org.jzmedia.tv.playback.TvPlayer
import org.json.JSONObject
import java.util.UUID

data class TvRoute(val kind: String, val id: Long = 0, val season: Int = 0, val title: String = "", val key: String = UUID.randomUUID().toString(), val actorKey: String = "") {
    fun encode(): String = JSONObject().put("kind", kind).put("id", id).put("season", season).put("title", title).put("key", key).put("actor_key", actorKey).toString()
    companion object {
        fun decode(value: String): TvRoute = JSONObject(value).let { TvRoute(it.getString("kind"), it.optLong("id"), it.optInt("season"), it.text("title"), it.getString("key"), it.text("actor_key")) }
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
    var savedPageKeys by rememberSaveable { mutableStateOf(emptyList<String>()) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(Unit) {
        try { saved = withContext(Dispatchers.IO) { store.load() } }
        catch (_: Exception) { restoreError = "保存的连接无法读取，请重新输入服务器地址和令牌" }
        loaded = true
    }

    if (!loaded) { Status("正在读取连接…", icon = "loading"); return }
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
    val pagePrefix = "${server.baseUrl}:$library:"
    val pageKey = pagePrefix + routes.last().key
    LaunchedEffect(pagePrefix, routes) {
        val active = routes.map { pagePrefix + it.key }.toSet()
        val obsolete = savedPageKeys.filter { it !in active && !it.startsWith(pagePrefix + "root:") }
        obsolete.forEach(holder::removeState)
        savedPageKeys = ((savedPageKeys - obsolete.toSet()) + pageKey).distinct()
    }
    if (playback != null) {
        TvPlayer(server, playback!!, onClose = { playback = null; refresh++ }, onPlayNext = { playback = it })
        return
    }
    val route = routes.last()
    fun back() { if (routes.size > 1) routes = routes.dropLast(1) else onExit() }
    BackHandler { back() }
    fun navigate(next: TvRoute) { routes = routes + next }
    holder.SaveableStateProvider(pageKey) {
        val memory = rememberFocusMemory(when (route.kind) {
            "search" -> "key:A"
            "home", "movies", "shows", "collections" -> "nav:${route.kind}"
            "movie", "show", "season", "episode", "collection" -> "detail:primary"
            "libraries" -> "library:0"
            "settings" -> "settings:connection"
            else -> "back"
        })
        fun open(next: TvRoute) { memory.leaving = true; navigate(next) }
        fun play(request: PlaybackRequest) { memory.leaving = true; playback = request }
        Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background).padding(horizontal = 40.dp, vertical = 20.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)) {
            TvNavigation(navigationSection(routes), libraryName, memory) { kind ->
                when (kind) {
                    "search", "libraries", "settings" -> if (route.kind != kind) open(TvRoute(kind))
                    else -> {
                        if (route.key != "root:$kind") memory.leaving = true
                        routes = listOf(TvRoute(kind, key = "root:$kind"))
                    }
                }
            }
            // 屏幕返回键已取消：遥控器返回由上面的全局 BackHandler 兜底。
            when (route.kind) {
                "home" -> HomeScreen(server, library, refresh, memory, ::open, ::play)
                "movies", "shows", "collections" -> BrowseScreen(server, route.kind, library, refresh, memory, ::open)
                "search" -> SearchScreen(server, library, route.title, memory, ::open, ::back)
                "actor-works" -> ActorWorksScreen(server, library, route, memory, ::open, ::back)
                "movie", "show", "season", "episode", "collection" -> DetailScreen(server, route, refresh, memory, ::open, ::play, ::back)
                "libraries" -> LibrariesScreen(server, library, memory) { id, name ->
                    library = id; libraryName = name; routes = listOf(TvRoute("home", key = "root:home"))
                }
                "settings" -> LazyColumn(verticalArrangement = Arrangement.spacedBy(20.dp)) {
                    item { Text("电视设置", style = MaterialTheme.typography.headlineMedium) }
                    item {
                        Column(Modifier.widthIn(max = 760.dp).fillMaxWidth()
                            .background(DesignTokens.Surface, RoundedCornerShape(DesignTokens.CardRadius)).padding(20.dp),
                            verticalArrangement = Arrangement.spacedBy(12.dp)) {
                            SectionHeading("服务器连接")
                            Text(server.baseUrl, color = Muted)
                            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                                TvAction("修改连接", { editConnection = true }, Modifier.focusMemory(memory, "settings:connection"), icon = "edit")
                                TvAction("忘记连接", {
                                    scope.launch { withContext(Dispatchers.IO) { store.clear() }; saved = null; api = null }
                                }, Modifier.focusMemory(memory, "settings:forget"), icon = "trash")
                            }
                        }
                    }
                    item {
                        Column(Modifier.widthIn(max = 760.dp).fillMaxWidth()
                            .background(DesignTokens.Surface, RoundedCornerShape(DesignTokens.CardRadius)).padding(20.dp),
                            verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            SectionHeading("设备与版本")
                            Text("${Build.MANUFACTURER} ${Build.MODEL}")
                            Text("Android ${Build.VERSION.RELEASE} · API ${Build.VERSION.SDK_INT}", color = Muted)
                            Text("电视应用 ${BuildConfig.VERSION_NAME} · 播放协议 1", color = Muted)
                        }
                    }
                    item {
                        Column(Modifier.widthIn(max = 760.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                            Text("观看进度全家共享。媒体库扫描、整理和匹配请在网页端操作。", color = Muted)
                            TvAction("退出应用", onExit, Modifier.focusMemory(memory, "settings:exit"), icon = "logout")
                        }
                    }
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
    var scanning by remember { mutableStateOf(false) }
    var scanProgress by remember { mutableStateOf("") }
    var scanResults by remember { mutableStateOf<List<DiscoveredServer>?>(null) }
    var scanJob by remember { mutableStateOf<Job?>(null) }
    // 扫描结果弹窗：选服务器与确认连接一步完成；null=关闭。
    var serverPicker by remember { mutableStateOf(false) }
    var confirmTarget by remember { mutableStateOf<DiscoveredServer?>(null) }
    fun closePicker() { serverPicker = false; confirmTarget = null }
    val scope = rememberCoroutineScope()
    val memory = rememberFocusMemory("connect")
    val keyboard = LocalSoftwareKeyboardController.current
    val imeVisible = WindowInsets.isImeVisible
    // 已保存服务器换了 IP 时认亲提示。
    val moved = initial?.serverId?.takeIf { it.isNotBlank() }?.let { id ->
        scanResults?.let { ServerDiscovery.matchById(it, id)?.takeIf { it.address != initial.address } }
    }
    fun connect() {
        if (busy) return
        busy = true; error = ""
        val chosenAddress = address
        val chosenToken = token
        job = scope.launch {
            try {
                val candidate = JzApi(chosenAddress, chosenToken)
                val info = candidate.checkConnection()
                val connection = SavedConnection(candidate.baseUrl, chosenToken, info.optString("server_id"))
                save(connection)
                onConnected(candidate, connection)
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { error = e.message ?: "连接失败，请重试" }
            finally { busy = false }
        }
    }
    LaunchedEffect(Unit) { if (autoConnect) connect() }
    fun scan() {
        if (scanning) { scanJob?.cancel(); scanning = false; return }
        scanning = true; scanProgress = ""; scanResults = null; closePicker()
        scanJob = scope.launch {
            try {
                val found = ServerDiscovery.scan(onProgress = { done, total -> scanProgress = "$done/$total" })
                scanResults = found
                if (found.isNotEmpty()) serverPicker = true
            } catch (e: CancellationException) { throw e }
            catch (_: Exception) { scanResults = emptyList() }
            finally { scanning = false }
        }
    }
    BackHandler {
        if (imeVisible) keyboard?.hide()
        else if (busy) { job?.cancel(); busy = false }
        else if (scanning) { scanJob?.cancel(); scanning = false }
        else onBack()
    }
    LazyColumn(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background).padding(horizontal = 64.dp, vertical = 36.dp),
        verticalArrangement = Arrangement.spacedBy(20.dp)) {
        item { Text("连接 jzmedia", style = MaterialTheme.typography.displaySmall) }
        item { Text("让电视和服务器连接同一个家庭网络，然后填写网页端使用的服务器地址。", color = Muted) }
        item { TvInput("服务器地址，例如 http://192.168.1.10:8080", address, { address = it }, Modifier.widthIn(max = 740.dp)) }
        item { TvInput("访问令牌（服务器未设置时留空）", token, { token = it }, Modifier.widthIn(max = 740.dp), password = true) }
        if (error.isNotBlank()) item { Text(error, color = DesignTokens.Error) }
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(20.dp)) {
                TvAction(if (busy) "正在连接…" else "连接并记住", ::connect, Modifier.focusMemory(memory, "connect"), enabled = !busy, icon = if (busy) "loading" else "link")
                // 闲时返回由遥控器（BackHandler）负责；连接中才需要屏幕上的取消键
                if (busy) TvAction("取消连接", { job?.cancel(); busy = false }, icon = "close")
            }
        }
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(20.dp)) {
                TvAction(
                    if (scanning) "停止扫描${if (scanProgress.isNotBlank()) "（$scanProgress）" else ""}" else "扫描局域网",
                    ::scan, Modifier.focusMemory(memory, "scan"), enabled = !busy,
                    icon = if (scanning) "loading" else "search",
                )
            }
        }
        if (moved != null) item {
            Text("已保存的 ${moved.name} 现位于 ${moved.address}，扫描后可直接选择更新", color = Muted)
        }
        val results = scanResults
        if (results != null && !scanning && results.isEmpty()) {
            item { Status("同一网络下未发现服务器，可手动填写地址", ::scan, icon = "info") }
        }
        item { Text("方向键移动 · 确定键编辑或选择 · 返回键关闭键盘或返回", color = Muted) }
    }
    if (serverPicker) {
        ServerPickerDialog(
            results = scanResults.orEmpty(),
            movedAddress = moved?.address,
            tokenReady = token.isNotBlank(),
            onPick = { hit -> address = hit.address; confirmTarget = hit },
            confirm = confirmTarget,
            onConfirm = { hit -> address = hit.address; closePicker(); connect() },
            onBackToList = { confirmTarget = null },
            onClose = ::closePicker,
        )
    }
}

private fun serverChoiceLabel(hit: DiscoveredServer): String = buildString {
    append(hit.name)
    append(" · ")
    append(hit.version)
    append(" · ")
    append(hit.address.removePrefix("http://").removePrefix("https://").trimEnd('/'))
    if (hit.authRequired) append(" · 需令牌")
    if (!hit.protocolOk) append(" · 需升级服务端")
}

@Composable
private fun ServerPickerDialog(
    results: List<DiscoveredServer>,
    movedAddress: String?,
    tokenReady: Boolean,
    onPick: (DiscoveredServer) -> Unit,
    confirm: DiscoveredServer?,
    onConfirm: (DiscoveredServer) -> Unit,
    onBackToList: () -> Unit,
    onClose: () -> Unit,
) {
    val memory = rememberFocusMemory("server-pick")
    val dialogHeight = tvDialogMaxHeight(.86f)
    BackHandler { if (confirm != null) onBackToList() else onClose() }
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Column(Modifier.fillMaxWidth(.7f).heightIn(max = dialogHeight).background(Panel, RoundedCornerShape(DesignTokens.DialogRadius)).padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            if (confirm == null) {
                Text("选择服务器", style = MaterialTheme.typography.headlineSmall)
                if (movedAddress != null) Text("已保存的服务器现位于 $movedAddress", color = Muted)
                val initialIndex = (results.indexOfFirst { it.address == movedAddress }.coerceAtLeast(0))
                val listState = rememberLazyListState(initialIndex)
                LazyColumn(state = listState, modifier = Modifier.weight(1f, fill = false),
                    contentPadding = PaddingValues(8.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    items(results, key = { it.address }) { hit ->
                        TvAction(serverChoiceLabel(hit), { onPick(hit) },
                            Modifier.fillMaxWidth().focusMemory(memory, "pick:${hit.address}"),
                            selected = hit.address == movedAddress, enabled = hit.protocolOk, icon = "server")
                    }
                }
            } else {
                Text("连接到 ${confirm.name}？", style = MaterialTheme.typography.headlineSmall)
                Text("${confirm.address} · ${confirm.version}", color = Muted)
                Row(horizontalArrangement = Arrangement.spacedBy(18.dp)) {
                    if (confirm.authRequired && !tokenReady) {
                        TvAction("去填写令牌", onClose, Modifier.focusMemory(memory, "confirm:token"), icon = "link")
                    } else {
                        TvAction("连接", { onConfirm(confirm) }, Modifier.focusMemory(memory, "confirm:ok"), icon = "link")
                    }
                }
            }
        }
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
        item { TvAction("全部媒体库", { onSelect(0, "全部媒体库") }, Modifier.focusMemory(memory, "library:0"), selected == 0L, icon = "library") }
        if (error.isNotBlank()) item { Status(error, { attempt++ }) }
        else if (rows == null) item { Status("正在读取媒体库…", icon = "loading") }
        items(rows.orEmpty(), key = { it.optLong("id") }) { row ->
            TvAction(row.text("name"), { onSelect(row.optLong("id"), row.text("name")) },
                Modifier.focusMemory(memory, "library:${row.optLong("id")}"), selected == row.optLong("id"), icon = "library")
        }
    }
}
