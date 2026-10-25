package org.jzmedia.tv.ui

import org.jzmedia.tv.ui.generated.DesignTokens
import android.os.Build
import android.os.SystemClock
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
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
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.key
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.LocalSaveableStateRegistry
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
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.jzmedia.tv.BuildConfig
import org.jzmedia.tv.data.ConnectionStore
import org.jzmedia.tv.data.ApiException
import org.jzmedia.tv.data.DiscoveredServer
import org.jzmedia.tv.data.DiscoveryScan
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.SavedConnection
import org.jzmedia.tv.data.ServerDiscovery
import org.jzmedia.tv.data.SearchHistoryStore
import org.jzmedia.tv.data.TvCastCommand
import org.jzmedia.tv.data.TvCastCommandHistory
import org.jzmedia.tv.data.TvCastReceiver
import org.jzmedia.tv.data.TvCastSnapshot
import org.jzmedia.tv.data.supportsViewingModes
import org.jzmedia.tv.data.viewingIdleExpired
import org.jzmedia.tv.data.rows
import org.jzmedia.tv.data.text
import org.jzmedia.tv.playback.PlaybackRequest
import org.jzmedia.tv.playback.PlaybackController
import org.jzmedia.tv.playback.TvPlayer
import org.json.JSONObject
import java.util.UUID

data class TvRoute(val kind: String, val id: Long = 0, val season: Int = 0, val title: String = "", val key: String = UUID.randomUUID().toString(), val actorKey: String = "") {
    fun encode(): String = JSONObject().put("kind", kind).put("id", id).put("season", season).put("title", title).put("key", key).put("actor_key", actorKey).toString()
    companion object {
        fun decode(value: String): TvRoute = JSONObject(value).let { TvRoute(it.getString("kind"), it.optLong("id"), it.optInt("season"), it.text("title"), it.getString("key"), it.text("actor_key")) }
    }
}

internal fun savedViewingRoutes(routes: List<TvRoute>, mode: String?): List<String> =
    if (mode == "private") listOf(TvRoute("home", key = "root:home").encode()) else routes.map(TvRoute::encode)

@androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
@Composable
fun TvApp(onExit: () -> Unit, lastInteraction: Long = 0L) {
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
    var playing by remember { mutableStateOf(false) }
    var viewingDialog by remember { mutableStateOf<ViewingDialogRequest?>(null) }
    var viewingBlocked by remember { mutableStateOf(false) }
    var viewingError by remember { mutableStateOf("") }
    var locking by remember { mutableStateOf(false) }
    var dialogInteraction by remember { mutableLongStateOf(0L) }
    var foreground by remember { mutableStateOf((context as? ComponentActivity)?.lifecycle?.currentState?.isAtLeast(Lifecycle.State.STARTED) == true) }
    var receiverEpoch by remember { mutableLongStateOf(0L) }
    var remoteSessionId by remember { mutableStateOf<String?>(null) }
    var remoteSnapshot by remember { mutableStateOf(TvCastSnapshot()) }
    var playbackController by remember { mutableStateOf<PlaybackController?>(null) }
    val receiverDeviceId = remember(context) {
        val preferences = context.getSharedPreferences("cast_receiver", android.content.Context.MODE_PRIVATE)
        preferences.getString("device_id", null) ?: UUID.randomUUID().toString().also {
            preferences.edit().putString("device_id", it).apply()
        }
    }
    var routes by rememberSaveable(stateSaver = listSaver<List<TvRoute>, String>(
        save = { rows -> savedViewingRoutes(rows, api?.viewing?.mode) }, restore = { rows -> rows.map(TvRoute::decode) },
    )) { mutableStateOf(listOf(TvRoute("home", key = "root:home"))) }
    val holder = rememberSaveableStateHolder()
    var savedPageKeys by rememberSaveable { mutableStateOf(emptyList<String>()) }
    val scope = rememberCoroutineScope()
    val latestInteraction by rememberUpdatedState(maxOf(lastInteraction, dialogInteraction))
    val latestPlaying by rememberUpdatedState(playing)

    fun clearPages() {
        remoteSessionId = null; remoteSnapshot = TvCastSnapshot(); playbackController = null
        playback = null; playing = false; viewingDialog = null
        savedPageKeys.forEach(holder::removeState); savedPageKeys = emptyList()
        routes = listOf(TvRoute("home", key = "root:home"))
        Posters.clear()
        refresh++
    }
    fun applySession(updated: JzApi, reset: Boolean) {
        val previous = api
        if (!reset && previous != null && previous.viewing?.mode == updated.viewing?.mode &&
            previous.viewing?.generation == updated.viewing?.generation && previous.viewingToken == updated.viewingToken) {
            updated.viewing?.let(previous::updateViewingMetadata)
            if (!foreground && updated.viewing?.protected == true) { clearPages(); viewingBlocked = true }
            return
        }
        if (previous?.viewing?.mode != updated.viewing?.mode) { library = 0; libraryName = "全部媒体库" }
        if (previous?.viewing != null && previous.viewing?.mode == updated.viewing?.mode && previous.viewing?.generation != updated.viewing?.generation) {
            SearchHistoryStore.clearAll(context)
            previous.clearPrivateHistory()
        }
        if (reset || previous?.viewing?.mode != updated.viewing?.mode || previous?.viewing?.generation != updated.viewing?.generation) clearPages()
        previous?.cancelRequests()
        api = updated
        if (!foreground && updated.viewing?.protected == true) { clearPages(); viewingBlocked = true }
        saved?.let { connection ->
            if (connection.viewingToken != updated.viewingToken || connection.viewingGeneration != updated.viewing?.generation) {
                saved = connection.copy(viewingToken = updated.viewingToken, viewingGeneration = updated.viewing?.generation ?: 0)
                val value = saved!!
                scope.launch { withContext(Dispatchers.IO) { store.save(value) } }
            }
        }
    }
    fun lockViewing() {
        val current = api ?: return
        if (current.viewing == null || locking) return
        // Keep the old token quarantined until the server confirms it no longer grants private access.
        viewingBlocked = true; viewingError = ""; locking = true
        clearPages(); current.cancelRequests()
        scope.launch {
            try {
                val updated = current.withViewing(current.post("/api/viewing/lock"))
                if (api === current) { applySession(updated, true); viewingBlocked = false }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { viewingError = e.message ?: "无法确认锁定状态，请重试" }
            finally { locking = false }
        }
    }
    val latestLock by rememberUpdatedState(::lockViewing)
    val latestApi by rememberUpdatedState(api)
    val latestBlocked by rememberUpdatedState(viewingBlocked)
    DisposableEffect(context) {
        val lifecycle = (context as? ComponentActivity)?.lifecycle
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_STOP) {
                receiverEpoch++
                foreground = false
                if (latestApi?.viewing?.protected == true) latestLock()
            }
            if (event == Lifecycle.Event.ON_START) {
                receiverEpoch++
                foreground = true
                if (latestBlocked) latestLock()
            }
        }
        lifecycle?.addObserver(observer)
        onDispose { lifecycle?.removeObserver(observer) }
    }

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
                api?.cancelRequests()
                api = connected; saved = connection; editConnection = false; restoreError = ""
                viewingBlocked = false; viewingError = ""
                if (changed || connected.viewing != null) {
                    clearPages(); library = 0; libraryName = "全部媒体库"
                }
                refresh++
            }, save = { withContext(Dispatchers.IO) { store.save(it) } },
            onBack = if (api != null) ({ editConnection = false }) else onExit)
        return
    }
    val server = api!!
    val receiverHistory = remember(server) { TvCastCommandHistory() }
    fun currentReceiverSnapshot(): TvCastSnapshot {
        val session = remoteSessionId ?: return TvCastSnapshot()
        val controller = playbackController
        if (playback?.remoteSessionId != session || controller?.request?.remoteSessionId != session) return remoteSnapshot
        val state = controller.remoteState()
        val status = when {
            state.error != null -> "ERROR"
            state.loading -> "LOADING"
            state.ended -> "STOPPED"
            state.playing -> "PLAYING"
            else -> "PAUSED_PLAYBACK"
        }
        val known = !state.loading && state.error == null && state.duration > 0
        return TvCastSnapshot(session, status, state.position.takeIf { known }, state.duration.takeIf { it > 0 },
            if (state.error != null) "电视播放失败，请使用电视遥控器查看详情" else "").also { remoteSnapshot = it }
    }
    fun closeCurrentPlayback() {
        if (remoteSessionId != null && playback?.remoteSessionId == remoteSessionId) {
            remoteSnapshot = currentReceiverSnapshot().copy(state = "STOPPED")
        }
        playbackController?.close()
        playbackController = null
        playback = null; playing = false; refresh++
    }
    fun acceptReceiverCommand(command: TvCastCommand): Boolean {
        if (!foreground || viewingBlocked || api !== server || !command.isValid()) return false
        if (command.startsMedia) {
            // Replacing a player releases its local output before acknowledging the new command.
            playbackController?.close(); playbackController = null
            remoteSessionId = command.sessionId
            remoteSnapshot = TvCastSnapshot(command.sessionId, "LOADING")
            playback = PlaybackRequest(command.kind!!, command.versionId!!, command.title,
                position = command.position, audio = command.audio, subtitle = command.subtitle,
                remoteSessionId = command.sessionId)
            return true
        }
        if (command.action == "stop" && command.sessionId == remoteSessionId &&
            (playback?.remoteSessionId == command.sessionId || playback == null &&
                remoteSnapshot.sessionId == command.sessionId && remoteSnapshot.state == "STOPPED")) {
            // Stop must also cancel a Play accepted before Compose mounts its controller.
            playbackController?.setPlaying(false)
            closeCurrentPlayback()
            return true
        }
        val controller = playbackController
        if (command.sessionId != remoteSessionId || playback?.remoteSessionId != command.sessionId ||
            controller?.request?.remoteSessionId != command.sessionId) return false
        when (command.action) {
            "play" -> controller.setPlaying(true)
            "pause" -> controller.setPlaying(false)
            "seek" -> controller.seek(command.position!!)
            else -> return false
        }
        return true
    }
    val latestReceiverSnapshot by rememberUpdatedState(::currentReceiverSnapshot)
    val latestReceiverCommand by rememberUpdatedState(::acceptReceiverCommand)
    val latestRejectedRemote by rememberUpdatedState<(String) -> Unit>({ session ->
        if (remoteSessionId == session && playback?.remoteSessionId == session) closeCurrentPlayback()
    })
    val latestDisconnectRemote by rememberUpdatedState<() -> Unit>({
        val session = remoteSessionId
        if (session != null && playback?.remoteSessionId == session) closeCurrentPlayback()
        remoteSessionId = null; remoteSnapshot = TvCastSnapshot()
    })
    LaunchedEffect(server, foreground, viewingBlocked, receiverEpoch) {
        if (!foreground || viewingBlocked) return@LaunchedEffect
        val epoch = receiverEpoch
        fun current() = foreground && !viewingBlocked && api === server && receiverEpoch == epoch
        while (current()) {
            val receiver = TvCastReceiver(server, receiverDeviceId, "${Build.MODEL} · jzmedia-tv")
            var registered = false
            try {
                latestDisconnectRemote()
                receiver.register(); registered = true
                while (current()) {
                    val command = receiver.poll(latestReceiverSnapshot())
                    if (!current()) break
                    if (command != null) {
                        val accepted = if (receiverHistory.claim(command)) {
                            latestReceiverCommand(command).also { receiverHistory.complete(command, it) }
                        } else receiverHistory.accepted(command)
                        try {
                            // Publish the released player's final source position before Stop ACK
                            // makes the phone's session inactive. Unacked commands are redelivered.
                            if (accepted && command.action == "stop") receiver.poll(latestReceiverSnapshot())
                            receiver.acknowledge(command, accepted,
                                if (accepted) "" else "电视播放或前台状态已变化，请重新发起投屏")
                        } catch (error: ApiException) {
                            if (error.status != 409 || !accepted) throw error
                            // A phone cancellation/expiry can race the local Play action and ACK.
                            // Revoke only the exact remote media; never a subsequent manual player.
                            latestRejectedRemote(command.sessionId)
                            receiverHistory.complete(command, false)
                        }
                    }
                    delay(receiver.heartbeatMillis)
                }
            } catch (error: CancellationException) { throw error }
            catch (error: ApiException) {
                if (!registered && error.status == 404) return@LaunchedEffect // Older servers retain normal TV playback.
                if (current()) { latestDisconnectRemote(); delay(3000) }
            } catch (_: Exception) { if (current()) { latestDisconnectRemote(); delay(3000) } }
            finally {
                withContext(NonCancellable) { withTimeoutOrNull(3000) { runCatching { receiver.unregister() } } }
            }
        }
    }
    LaunchedEffect(server, foreground, server.viewing?.protected) {
        if (!foreground && server.viewing?.protected == true) lockViewing()
    }
    DisposableEffect(server.viewing?.mode) {
        val window = (context as? ComponentActivity)?.window
        if (server.viewing?.mode == "private") window?.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        onDispose { if (server.viewing?.mode == "private") window?.clearFlags(WindowManager.LayoutParams.FLAG_SECURE) }
    }
    LaunchedEffect(server) { server.viewingLocked.collect { lockViewing() } }
    LaunchedEffect(server, viewingBlocked) {
        if (server.viewing == null || viewingBlocked) return@LaunchedEffect
        var lastSeenInteraction = latestInteraction
        var activeAt = SystemClock.elapsedRealtime()
        while (true) {
            delay(15_000)
            val userActivity = latestInteraction > lastSeenInteraction
            lastSeenInteraction = latestInteraction
            val now = SystemClock.elapsedRealtime()
            val privatePlayback = latestPlaying && server.viewing?.mode == "private"
            if (userActivity || privatePlayback) activeAt = now
            if (server.viewing?.protected == true && viewingIdleExpired(activeAt, now, privatePlayback)) {
                if (server.viewing?.mode == "private") { lockViewing(); break }
                server.updateViewingMetadata(server.viewing!!.copy(managementUnlocked = false))
                viewingDialog = null
            }
            try {
                val session = server.readViewingSession(server.post("/api/viewing/activity", JSONObject()
                    .put("user_activity", userActivity).put("playing", latestPlaying)))
                if (api === server && session != server.viewing) applySession(JzApi(server.baseUrl, server.token, session), false)
            } catch (e: CancellationException) { throw e }
            catch (_: Exception) { /* Local idle protection still runs while offline. */ }
        }
    }
    if (viewingBlocked) {
        BackHandler { onExit() }
        Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background).padding(40.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Status(if (locking) "正在锁定观看模式…" else "观看模式已在本机锁定", icon = if (locking) "loading" else "info")
            if (viewingError.isNotBlank()) Text(viewingError, color = DesignTokens.Error)
            TvAction("重试", ::lockViewing, enabled = !locking)
            TvAction("检查连接", { editConnection = true }, enabled = !locking)
            TvAction("退出应用", onExit)
        }
        return
    }
    var storageAlert by remember(server) { mutableStateOf(false) }
    LaunchedEffect(server) {
        while (true) {
            storageAlert = try { server.get("/api/data-usage").optBoolean("over_limit") }
            catch (e: CancellationException) { throw e }
            catch (_: Exception) { false } // Older servers do not expose application usage.
            delay(15 * 60 * 1000L)
        }
    }
    val pagePrefix = "${server.baseUrl}:$library:${server.viewing?.mode ?: "normal"}:${server.viewing?.generation ?: 0}:"
    val pageKey = pagePrefix + routes.last().key
    LaunchedEffect(pagePrefix, routes) {
        val active = routes.map { pagePrefix + it.key }.toSet()
        val obsolete = savedPageKeys.filter { it !in active && !it.startsWith(pagePrefix + "root:") }
        obsolete.forEach(holder::removeState)
        if (server.viewing?.mode != "private") savedPageKeys = ((savedPageKeys - obsolete.toSet()) + pageKey).distinct()
    }
    if (playback != null) {
        TvPlayer(server, playback!!, onClose = ::closeCurrentPlayback, onPlayNext = {
            remoteSessionId = null; remoteSnapshot = TvCastSnapshot(); playback = it
        }, onPlayingChanged = { playing = it }, onController = { controller, available ->
            if (available && foreground && !viewingBlocked && playback == controller.request) playbackController = controller
            else if (available) controller.close()
            else if (playbackController === controller) playbackController = null
        })
        return
    }
    val route = routes.last()
    fun back() { if (routes.size > 1) routes = routes.dropLast(1) else onExit() }
    BackHandler { back() }
    fun navigate(next: TvRoute) { routes = routes + next }
    val pageContent: @Composable () -> Unit = {
        val memory = rememberFocusMemory(when (route.kind) {
            "search" -> "key:A"
            "home", "movies", "shows", "collections" -> "nav:${route.kind}"
            "movie", "show", "season", "episode", "collection" -> "detail:primary"
            "libraries" -> "library:0"
            "settings" -> if (server.viewing != null) "settings:viewing" else "settings:connection"
            else -> "nav:home"
        })
        fun open(next: TvRoute) { memory.leaving = true; navigate(next) }
        fun play(request: PlaybackRequest) {
            memory.leaving = true
            remoteSessionId = null; remoteSnapshot = TvCastSnapshot()
            playback = request
        }
        Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background).padding(horizontal = 40.dp, vertical = 20.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)) {
            TvNavigation(navigationSection(routes), libraryName, memory, server.viewing?.label) { kind ->
                when (kind) {
                    "viewing" -> viewingDialog = ViewingDialogRequest()
                    "search", "libraries", "settings" -> if (route.kind != kind) open(TvRoute(kind))
                    else -> {
                        if (route.key != "root:$kind") memory.leaving = true
                        routes = listOf(TvRoute(kind, key = "root:$kind"))
                    }
                }
            }
            if (storageAlert) {
                Text("服务器应用数据已达到 20 GB。请在网页设置的“系统维护”中检查并清理缓存。",
                    modifier = Modifier.fillMaxWidth().background(DesignTokens.SurfaceRaised, RoundedCornerShape(DesignTokens.CardRadius)).padding(16.dp),
                    color = DesignTokens.Error)
            }
            // 屏幕返回键已取消：遥控器返回由上面的全局 BackHandler 兜底。
            when (route.kind) {
                "home" -> HomeScreen(server, library, refresh, memory, ::open, ::play)
                "movies", "shows", "collections" -> BrowseScreen(server, route.kind, library, refresh, memory, ::open)
                "search" -> SearchScreen(server, library, route.title, memory, ::open, ::back)
                "actor-works" -> ActorWorksScreen(server, library, route, memory, ::open, ::back)
                "movie", "show", "season", "episode", "collection" -> DetailScreen(server, route, refresh, memory, ::open, ::play, ::back,
                    onProtection = { kind, id -> viewingDialog = ViewingDialogRequest("policy", kind, id) })
                "libraries" -> LibrariesScreen(server, library, memory) { id, name ->
                    library = id; libraryName = name; routes = listOf(TvRoute("home", key = "root:home"))
                }
                "settings" -> LazyColumn(verticalArrangement = Arrangement.spacedBy(20.dp)) {
                    item { Text("电视设置", style = MaterialTheme.typography.headlineMedium) }
                    if (server.viewing != null) item {
                        Column(Modifier.widthIn(max = 760.dp).fillMaxWidth()
                            .background(DesignTokens.Surface, RoundedCornerShape(DesignTokens.CardRadius)).padding(20.dp),
                            verticalArrangement = Arrangement.spacedBy(12.dp)) {
                            SectionHeading("观看模式")
                            Text("${server.viewing?.label} · 只影响当前电视", color = Muted)
                            TvAction("切换模式", { viewingDialog = ViewingDialogRequest() }, Modifier.focusMemory(memory, "settings:viewing"))
                            if (server.viewing?.mode != "children") {
                                TvAction(if (server.viewing?.enabled == true) "修改保护密码" else "设置保护密码",
                                    { viewingDialog = ViewingDialogRequest("password") })
                                if (server.viewing?.managementUnlocked == true) TvAction("锁定保护设置", ::lockViewing)
                            }
                        }
                    }
                    item {
                        Column(Modifier.widthIn(max = 760.dp).fillMaxWidth()
                            .background(DesignTokens.Surface, RoundedCornerShape(DesignTokens.CardRadius)).padding(20.dp),
                            verticalArrangement = Arrangement.spacedBy(12.dp)) {
                            SectionHeading("服务器连接")
                            Text(server.baseUrl, color = Muted)
                            if (server.viewing?.mode != "children" && server.viewing?.protected != true) Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                                TvAction("修改连接", { editConnection = true }, Modifier.focusMemory(memory, "settings:connection"), icon = "edit")
                                TvAction("忘记连接", {
                                    scope.launch { withContext(Dispatchers.IO) { store.clear() }; saved = null; api = null }
                                }, Modifier.focusMemory(memory, "settings:forget"), icon = "trash")
                            }
                            else Text("切换到普通模式并锁定保护设置后，可以修改服务器连接。", color = Muted)
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
    if (server.viewing?.mode == "private") {
        CompositionLocalProvider(LocalSaveableStateRegistry provides null) { key(pageKey) { pageContent() } }
    } else holder.SaveableStateProvider(pageKey, pageContent)
    viewingDialog?.let { request ->
        ViewingDialog(server, request, onClose = { viewingDialog = null }, onSession = ::applySession,
            onPolicySaved = { SearchHistoryStore.clearAll(context); server.clearPrivateHistory(); clearPages() },
            onUserActivity = { dialogInteraction = SystemClock.elapsedRealtime() })
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
    var resetViewingSession by remember { mutableStateOf(false) }
    var viewingResetRequired by remember { mutableStateOf(false) }
    var job by remember { mutableStateOf<Job?>(null) }
    var scanning by remember { mutableStateOf(false) }
    var scanProgress by remember { mutableStateOf("") }
    var scanResults by remember { mutableStateOf<List<DiscoveredServer>?>(null) }
    // 扫描结果弹窗：选服务器与确认连接一步完成；null=关闭。
    var serverPicker by remember { mutableStateOf(false) }
    var confirmTarget by remember { mutableStateOf<DiscoveredServer?>(null) }
    fun closePicker() { serverPicker = false; confirmTarget = null }
    val scope = rememberCoroutineScope()
    val discovery = remember(scope) { DiscoveryScan(scope) }
    val context = LocalContext.current
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
                val prior = initial?.takeIf { !resetViewingSession && it.address == JzApi.normalizeServerUrl(chosenAddress) && it.token == chosenToken }
                var candidate = JzApi(chosenAddress, chosenToken, initialViewingToken = prior?.viewingToken.orEmpty())
                val info = candidate.checkConnection()
                if (supportsViewingModes(info)) {
                    if (!resetViewingSession && prior == null && initial?.serverId?.isNotBlank() == true && initial.serverId == info.text("server_id") && initial.token == chosenToken) {
                        candidate = JzApi(chosenAddress, chosenToken, initialViewingToken = initial.viewingToken)
                    }
                    candidate = candidate.withViewing(candidate.get("/api/viewing/session"))
                    if (initial?.viewingToken == candidate.viewingToken && initial.viewingGeneration != candidate.viewing?.generation) {
                        SearchHistoryStore.clearAll(context)
                    }
                    // A persisted device token must never restore a previous private or management grant.
                    candidate = candidate.withViewing(candidate.post("/api/viewing/lock"))
                }
                val connection = SavedConnection(candidate.baseUrl, chosenToken, info.optString("server_id"), candidate.viewingToken,
                    candidate.viewing?.generation ?: 0)
                save(connection)
                onConnected(candidate, connection)
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) {
                error = e.message ?: "连接失败，请重试"
                viewingResetRequired = e is ApiException && e.code == "viewing_locked"
            }
            finally { busy = false }
        }
    }
    LaunchedEffect(Unit) { if (autoConnect) connect() }
    fun scan() {
        if (scanning) { discovery.cancel(); scanning = false; return }
        scanning = true; scanProgress = ""; scanResults = null; closePicker()
        discovery.start(
            onProgress = { done, total -> scanProgress = "$done/$total" },
            onResult = { found ->
                scanResults = found
                if (found.isNotEmpty()) serverPicker = true
            },
            onFailure = { scanResults = emptyList() },
            onFinished = { scanning = false },
        )
    }
    BackHandler {
        if (imeVisible) keyboard?.hide()
        else if (busy) { job?.cancel(); busy = false }
        else if (scanning) { discovery.cancel(); scanning = false }
        else onBack()
    }
    LazyColumn(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background).padding(horizontal = 64.dp, vertical = 36.dp),
        verticalArrangement = Arrangement.spacedBy(20.dp)) {
        item { Text("连接 jzmedia", style = MaterialTheme.typography.displaySmall) }
        item { Text("让电视和服务器连接同一个家庭网络，然后填写网页端使用的服务器地址。", color = Muted) }
        item { TvInput("服务器地址，例如 http://192.168.1.10:8080", address, { address = it }, Modifier.widthIn(max = 740.dp)) }
        item { TvInput("访问令牌（服务器未设置时留空）", token, { token = it }, Modifier.widthIn(max = 740.dp), password = true) }
        if (error.isNotBlank()) item { Text(error, color = DesignTokens.Error) }
        if (viewingResetRequired) item {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("旧观看会话已失效。重建会话后本机回到普通模式，服务器内的隐私内容仍保持隐藏。", color = Muted)
                TvAction("重建本机观看会话", { resetViewingSession = true; viewingResetRequired = false; connect() }, enabled = !busy)
            }
        }
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
                            selected = hit.address == movedAddress, enabled = hit.protocolOk, icon = "server", fillContent = true)
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
        item { TvAction("全部媒体库", { onSelect(0, "全部媒体库") }, Modifier.fillMaxWidth().focusMemory(memory, "library:0"), selected == 0L, icon = "library", fillContent = true) }
        if (error.isNotBlank()) item { Status(error, { attempt++ }) }
        else if (rows == null) item { Status("正在读取媒体库…", icon = "loading") }
        items(rows.orEmpty(), key = { it.optLong("id") }) { row ->
            TvAction(row.text("name"), { onSelect(row.optLong("id"), row.text("name")) },
                Modifier.fillMaxWidth().focusMemory(memory, "library:${row.optLong("id")}"), selected == row.optLong("id"), icon = "library", fillContent = true)
        }
    }
}
