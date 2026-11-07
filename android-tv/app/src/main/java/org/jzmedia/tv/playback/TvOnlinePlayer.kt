package org.jzmedia.tv.playback

import android.content.Context
import android.content.ContextWrapper
import android.view.KeyEvent
import android.view.ViewGroup
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusProperties
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.media3.common.C
import androidx.media3.common.util.UnstableApi
import androidx.media3.ui.PlayerView
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import kotlinx.coroutines.delay
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.ui.Accent
import org.jzmedia.tv.ui.Muted
import org.jzmedia.tv.ui.TvAction
import org.jzmedia.tv.ui.generated.DesignTokens

/** Native TV controls; online playback never mounts the NAS preview/quality workflow. */
@androidx.annotation.OptIn(UnstableApi::class)
@Composable
fun TvOnlinePlayer(api: JzApi, request: OnlinePlaybackRequest, onClose: () -> Unit,
                   onPlayNext: (OnlinePlaybackRequest) -> Unit = {},
                   onPlayingChanged: (Boolean) -> Unit = {},
                   onController: (OnlinePlaybackController, Boolean) -> Unit = { _, _ -> }) {
    val context = LocalContext.current
    val controller = remember(api, request) { OnlinePlaybackController(context, api, request) }
    val state = controller.state
    val close by rememberUpdatedState(onClose)
    val next by rememberUpdatedState(onPlayNext)
    val playingChanged by rememberUpdatedState(onPlayingChanged)
    val controllerChanged by rememberUpdatedState(onController)
    fun leave() { controller.close(); close() }
    DisposableEffect(controller) {
        controllerChanged(controller, true)
        val lifecycle = context.onlineActivity()?.lifecycle
        val observer = LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_STOP) leave() }
        lifecycle?.addObserver(observer)
        onDispose {
            lifecycle?.removeObserver(observer)
            controllerChanged(controller, false); playingChanged(false); controller.close()
        }
    }
    LaunchedEffect(state.playing) { playingChanged(state.playing) }
    LaunchedEffect(controller, state.next) {
        state.next?.let(controller::consumeNext)?.let { value -> controller.close(); next(value) }
    }
    val actions = remember(controller) {
        OnlinePlayerActions(controller::toggle, controller::setPlaying, controller::skip, controller::seek,
            controller::retry, controller::cancelNext,
            { controller.consumeNetworkNext()?.let { value -> controller.close(); next(value) } },
            controller::setRate, controller::setTrack, ::leave)
    }
    key(controller) {
        OnlinePlayerContent(request, state, actions) {
            AndroidView(factory = { PlayerView(it).apply {
                player = controller.player; useController = false; isFocusable = false
                descendantFocusability = ViewGroup.FOCUS_BLOCK_DESCENDANTS
            } }, modifier = Modifier.fillMaxSize(), update = {
                it.player = controller.player; it.keepScreenOn = state.playing
            }, onRelease = { it.keepScreenOn = false; it.player = null })
        }
    }
}

/** Keeps remote interaction testable without creating a decoder or a network session. */
internal data class OnlinePlayerActions(
    val toggle: () -> Unit,
    val setPlaying: (Boolean) -> Unit,
    val skip: (Double) -> Unit,
    val seek: (Double) -> Unit,
    val retry: () -> Unit,
    val cancelNext: () -> Unit,
    val playNetworkNext: () -> Unit,
    val setRate: (Float) -> Unit,
    val setTrack: (Int, OnlineTrackOption?) -> Unit,
    val close: () -> Unit,
)

@Composable
internal fun OnlinePlayerContent(request: OnlinePlaybackRequest, state: OnlinePlaybackState,
                                 actions: OnlinePlayerActions, video: @Composable () -> Unit = {}) {
    var controls by remember { mutableStateOf(true) }
    var menu by remember { mutableStateOf<String?>(null) }
    var interaction by remember { mutableIntStateOf(0) }
    var preview by remember { mutableStateOf<Double?>(null) }
    var seekFocused by remember { mutableStateOf(false) }
    var seekUnit by remember { mutableIntStateOf(10) }
    var seekHold by remember { mutableStateOf<Job?>(null) }
    var lastControl by remember { mutableStateOf("") }
    val uiScope = rememberCoroutineScope()
    val rootFocus = remember { FocusRequester() }
    val actionFocus = remember { FocusRequester() }
    val menuFocus = remember { FocusRequester() }
    val errorFocus = remember { FocusRequester() }
    val actionList = rememberLazyListState()
    val confirmPress = remember { PlaybackConfirmPress() }
    val canSeek = state.seekable && state.duration > 0 && !state.ended

    fun controlFocus(name: String): Modifier = Modifier.onFocusChanged { if (it.isFocused) lastControl = name }
    fun togglePlayback() { if (state.ended) actions.retry() else actions.toggle() }
    fun focusedControlRemoved(): Boolean = when (lastControl) {
        "seek", "rewind", "forward" -> !canSeek
        "cancel-next" -> !state.nextLoading
        "network-next" -> state.networkNext == null
        else -> false
    }

    fun stopSeekHold() { seekHold?.cancel(); seekHold = null }
    fun moveSeek(direction: Int, repeats: Int) {
        if (state.seekable && state.duration > 0 && !state.ended)
            preview = seekPreviewPosition(preview ?: state.position, state.duration, direction, repeats, seekUnit)
    }
    fun startSeekHold(direction: Int) {
        if (!canSeek || seekHold != null) return
        moveSeek(direction, 0)
        seekHold = uiScope.launch {
            delay(450)
            var repeats = 0
            while (true) { moveSeek(direction, ++repeats); delay(100) }
        }
    }
    fun commitSeek() { stopSeekHold(); preview?.let(actions.seek); preview = null }
    suspend fun focusPrimaryAction(onlyIfRemoved: Boolean = false) {
        actionList.scrollToItem(0)
        withFrameNanos { }
        if (!onlyIfRemoved || focusedControlRemoved()) actionFocus.requestFocus()
    }
    DisposableEffect(Unit) { onDispose { stopSeekHold() } }
    LaunchedEffect(state.error, controls, menu) {
        withFrameNanos { }
        when {
            state.error != null -> errorFocus.requestFocus()
            menu != null -> menuFocus.requestFocus()
            controls -> focusPrimaryAction()
            else -> rootFocus.requestFocus()
        }
    }
    LaunchedEffect(canSeek, state.nextLoading, state.networkNext != null, controls, menu, state.error) {
        // Only recover a control that disappeared; background next-episode work must not steal focus.
        if (controls && menu == null && state.error == null && focusedControlRemoved()) {
            withFrameNanos { }
            if (focusedControlRemoved()) focusPrimaryAction(onlyIfRemoved = true)
        }
    }
    LaunchedEffect(state.ended, state.error) {
        if (state.ended || state.error != null) { stopSeekHold(); preview = null; controls = true }
        if (state.error != null) menu = null
    }
    LaunchedEffect(controls, interaction, menu, state.playing, state.ended, state.error, preview, seekFocused) {
        if (controls && state.playing && menu == null && !state.ended && state.error == null && preview == null && !seekFocused) {
            delay(6000); controls = false
        }
    }
    BackHandler {
        interaction++
        stopSeekHold()
        when {
            menu != null -> menu = null
            preview != null -> preview = null
            state.nextLoading -> actions.cancelNext()
            controls && state.error == null -> controls = false
            else -> actions.close()
        }
    }
    Box(Modifier.fillMaxSize().background(Color.Black).onPreviewKeyEvent { event ->
        if (confirmPress.consume(event.nativeKeyEvent.keyCode, event.nativeKeyEvent.action == KeyEvent.ACTION_UP))
            return@onPreviewKeyEvent true
        if (event.nativeKeyEvent.action == KeyEvent.ACTION_UP && seekHold != null
            && event.nativeKeyEvent.keyCode in listOf(KeyEvent.KEYCODE_DPAD_LEFT, KeyEvent.KEYCODE_DPAD_RIGHT)) {
            stopSeekHold(); return@onPreviewKeyEvent true
        }
        if (event.nativeKeyEvent.action != KeyEvent.ACTION_DOWN) return@onPreviewKeyEvent false
        interaction++
        when (event.nativeKeyEvent.keyCode) {
            KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE -> { togglePlayback(); controls = true; true }
            KeyEvent.KEYCODE_MEDIA_PLAY -> { actions.setPlaying(true); true }
            KeyEvent.KEYCODE_MEDIA_PAUSE -> { actions.setPlaying(false); controls = true; true }
            KeyEvent.KEYCODE_MEDIA_FAST_FORWARD -> { actions.skip(30.0); true }
            KeyEvent.KEYCODE_MEDIA_REWIND -> { actions.skip(-30.0); true }
            else -> if (!controls && menu == null && state.error == null) when (event.nativeKeyEvent.keyCode) {
                KeyEvent.KEYCODE_DPAD_LEFT -> { startSeekHold(-1); true }
                KeyEvent.KEYCODE_DPAD_RIGHT -> { startSeekHold(1); true }
                KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER -> {
                    confirmPress.handle(event.nativeKeyEvent.keyCode)
                    stopSeekHold()
                    if (preview != null) commitSeek() else { togglePlayback(); controls = true }
                    true
                }
                KeyEvent.KEYCODE_DPAD_UP, KeyEvent.KEYCODE_DPAD_DOWN -> { stopSeekHold(); preview = null; controls = true; true }
                else -> false
            } else false
        }
    }.focusRequester(rootFocus).focusable()) {
        video()
        if (state.loading && state.error == null) Box(Modifier.align(Alignment.Center)
            .background(DesignTokens.SurfaceRaised).padding(24.dp)) { Text(if (request.localCache) "正在准备本地缓存…" else "正在准备在线原片…") }
        if (preview != null && !controls) Column(Modifier.align(Alignment.BottomCenter).fillMaxWidth()
            .background(Color.Black.copy(alpha = .88f)).padding(28.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("跳转至 ${playbackTime(preview!!)} / ${playbackTime(state.duration)}", style = MaterialTheme.typography.headlineSmall)
            Text("短按 ${playbackTime(seekUnit.toDouble())} · 按住加速 · 确定跳转 · 返回取消", color = Muted)
        }
        if (controls && menu == null && state.error == null) Column(Modifier.align(Alignment.BottomCenter)
            .fillMaxWidth().background(Color.Black.copy(alpha = .88f)).padding(28.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(request.title, style = MaterialTheme.typography.titleLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text("${if (request.localCache) "本地缓存" else "在线原片"} · ${state.transport.uppercase()} · 由本电视解码", color = Muted)
            Box(Modifier.fillMaxWidth().height(48.dp).onFocusChanged {
                seekFocused = it.isFocused
                if (it.isFocused) lastControl = "seek"
                else { stopSeekHold(); preview = null }
            }
                .onPreviewKeyEvent { event ->
                    if (event.nativeKeyEvent.action != KeyEvent.ACTION_DOWN) return@onPreviewKeyEvent false
                    when (event.nativeKeyEvent.keyCode) {
                        KeyEvent.KEYCODE_DPAD_LEFT -> { startSeekHold(-1); true }
                        KeyEvent.KEYCODE_DPAD_RIGHT -> { startSeekHold(1); true }
                        KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER -> {
                            stopSeekHold()
                            if (preview == null) preview = state.position else commitSeek()
                            true
                        }
                        else -> false
                    }
                }.semantics {
                    contentDescription = "在线播放进度"
                    stateDescription = "${playbackTime(preview ?: state.position)} / ${playbackTime(state.duration)}"
                }.border(2.dp, if (seekFocused) Color.White else Color.Transparent, RoundedCornerShape(8.dp))
                .padding(horizontal = 10.dp).focusable(enabled = canSeek),
                contentAlignment = Alignment.Center) {
                val buffered = if (state.duration > 0) (state.buffered / state.duration).toFloat().coerceIn(0f, 1f) else 0f
                val played = if (state.duration > 0) ((preview ?: state.position) / state.duration).toFloat().coerceIn(0f, 1f) else 0f
                Box(Modifier.fillMaxWidth().height(5.dp).background(DesignTokens.Border)) {
                    Box(Modifier.fillMaxWidth(buffered).height(5.dp).background(DesignTokens.TextDim))
                    Box(Modifier.fillMaxWidth(played).height(5.dp).background(Accent))
                }
            }
            Text("${if (preview != null) "跳转至 " else ""}${playbackTime(preview ?: state.position)} / ${playbackTime(state.duration)} · ${state.rate}×")
            if (seekFocused) Text("左右预选 · 确定跳转 · 返回取消 · 步长${playbackTime(seekUnit.toDouble())}", color = Muted)
            state.notice?.let { Text(it, color = Muted, style = MaterialTheme.typography.bodySmall) }
            if (state.ended) Text(if (state.nextLoading) "播放结束，正在读取下一集…" else "播放结束")
            LazyRow(state = actionList, contentPadding = PaddingValues(6.dp), horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                item("play") {
                    // One call site preserves the focused node across pause, resume and replay.
                    TvAction(if (state.ended) "重新播放" else if (state.playWhenReady) "暂停" else "播放",
                        { togglePlayback(); interaction++ },
                        Modifier.focusRequester(actionFocus).then(controlFocus("play")),
                        icon = if (state.ended) "replay" else if (state.playWhenReady) "pause" else "play")
                }
                item("rewind") { TvAction("后退10秒", { actions.skip(-10.0); interaction++ }, controlFocus("rewind").focusProperties { canFocus = canSeek }, enabled = canSeek, icon = "rewind") }
                item("forward") { TvAction("前进10秒", { actions.skip(10.0); interaction++ }, controlFocus("forward").focusProperties { canFocus = canSeek }, enabled = canSeek, icon = "forward10") }
                item("settings") { TvAction("播放设置", { menu = "settings" }, controlFocus("settings"), icon = "settings") }
                if (state.nextLoading) item("cancel-next") { TvAction("取消连续播放", actions.cancelNext, controlFocus("cancel-next")) }
                if (state.networkNext != null) item("network-next") { TvAction("联网播放下一集", actions.playNetworkNext, controlFocus("network-next"), icon = "forward") }
                item("exit") { TvAction("退出播放", actions.close, controlFocus("exit"), icon = "close") }
            }
        }
        state.error?.let { error -> Column(Modifier.align(Alignment.Center).widthIn(max = 720.dp)
            .background(DesignTokens.SurfaceRaised).padding(32.dp), verticalArrangement = Arrangement.spacedBy(20.dp)) {
            Text(error, style = MaterialTheme.typography.titleLarge)
            Text("在线不提供服务器转码；可返回选择其他线路。", color = Muted)
            Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                TvAction("重试播放", actions.retry, Modifier.focusRequester(errorFocus), icon = "refresh")
                TvAction("返回选集", actions.close, icon = "arrow-left")
            }
        } }
        if (menu != null && state.error == null) key(menu) { Column(Modifier.align(Alignment.CenterEnd).fillMaxWidth(.48f)
            .background(DesignTokens.SurfaceRaised).padding(28.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Text(when (menu) { "rate" -> "播放速度"; "audio" -> "音轨"; "subtitle" -> "字幕"; else -> "播放设置" }, style = MaterialTheme.typography.headlineSmall)
            LazyColumn(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                item { TvAction(if (menu == "settings") "返回播放" else "返回播放设置",
                    { menu = if (menu == "settings") null else "settings" },
                    Modifier.fillMaxWidth().focusRequester(menuFocus), icon = "arrow-left", fillContent = true) }
                when (menu) {
                    "settings" -> {
                        item { TvAction("播放速度 · ${state.rate}×", { menu = "rate" }, Modifier.fillMaxWidth(), fillContent = true) }
                        item { TvAction("音轨", { menu = "audio" }, Modifier.fillMaxWidth(), enabled = state.audios.isNotEmpty(), fillContent = true) }
                        item { TvAction("字幕", { menu = "subtitle" }, Modifier.fillMaxWidth(), enabled = state.subtitles.isNotEmpty(), fillContent = true) }
                        item { Text("跳转步长", color = Muted) }
                        items(listOf(10, 60, 300)) { seconds -> TvAction(playbackTime(seconds.toDouble()),
                            { seekUnit = seconds }, Modifier.fillMaxWidth(), selected = seekUnit == seconds, fillContent = true) }
                        item { Text("音量使用遥控器音量键；音轨、字幕能力由本电视播放器决定。", color = Muted) }
                    }
                    "rate" -> items(PLAYBACK_RATES) { rate -> TvAction("${rate}×", { actions.setRate(rate); menu = null },
                        Modifier.fillMaxWidth(), selected = state.rate == rate, fillContent = true) }
                    "audio" -> items(state.audios) { option -> TvAction(option.label, { actions.setTrack(C.TRACK_TYPE_AUDIO, option); menu = null },
                        Modifier.fillMaxWidth(), selected = option.selected, fillContent = true) }
                    "subtitle" -> {
                        item { TvAction("关闭字幕", { actions.setTrack(C.TRACK_TYPE_TEXT, null); menu = null }, Modifier.fillMaxWidth(),
                            selected = state.subtitles.none { it.selected }, icon = "subtitles", fillContent = true) }
                        items(state.subtitles) { option -> TvAction(option.label, { actions.setTrack(C.TRACK_TYPE_TEXT, option); menu = null },
                            Modifier.fillMaxWidth(), selected = option.selected, fillContent = true) }
                    }
                }
            }
        } }
    }
}

private tailrec fun Context.onlineActivity(): ComponentActivity? = when (this) {
    is ComponentActivity -> this
    is ContextWrapper -> baseContext.onlineActivity()
    else -> null
}
