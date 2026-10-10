package org.jzmedia.tv.playback

import android.content.Context
import android.content.ContextWrapper
import android.view.KeyEvent
import android.view.ViewGroup
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
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
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shadow
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.media3.common.util.UnstableApi
import androidx.media3.ui.PlayerView
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import kotlinx.coroutines.delay
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.ui.Accent
import org.jzmedia.tv.ui.Muted
import org.jzmedia.tv.ui.TvIcon
import org.jzmedia.tv.ui.generated.DesignTokens
import org.jzmedia.tv.ui.TvAction

@androidx.annotation.OptIn(UnstableApi::class)
@Composable
fun TvPlayer(api: JzApi, request: PlaybackRequest, onClose: () -> Unit, onPlayNext: (PlaybackRequest) -> Unit) {
    val context = LocalContext.current
    val controller = remember(api, request) { PlaybackController(context, api, request) }
    val state = controller.state
    val close by rememberUpdatedState(onClose)
    val playNext by rememberUpdatedState(onPlayNext)
    var controls by remember(controller) { mutableStateOf(true) }
    var menu by remember(controller) { mutableStateOf<String?>(null) }
    var preview by remember(controller) { mutableStateOf<Double?>(null) }
    var interaction by remember(controller) { mutableIntStateOf(0) }
    var countdown by remember(controller) { mutableIntStateOf(10) }
    var cancelThisNext by remember(controller) { mutableStateOf(false) }
    var controlsHeightPx by remember(controller) { mutableIntStateOf(0) }
    val density = LocalDensity.current
    val rootFocus = remember(controller) { FocusRequester() }
    val playFocus = remember(controller) { FocusRequester() }
    val menuFocus = remember(controller) { FocusRequester() }
    val errorFocus = remember(controller) { FocusRequester() }

    fun startNext() {
        val next = controller.nextRequest() ?: return
        controller.close()
        playNext(next)
    }

    DisposableEffect(controller) {
        val lifecycle = context.activity()?.lifecycle
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_STOP) { controller.close(); close() }
        }
        lifecycle?.addObserver(observer)
        onDispose { lifecycle?.removeObserver(observer); controller.close() }
    }
    LaunchedEffect(controller, controls, menu, state.error, cancelThisNext) {
        withFrameNanos { }
        when {
            state.error != null -> errorFocus.requestFocus()
            menu != null -> menuFocus.requestFocus()
            controls -> playFocus.requestFocus()
            else -> rootFocus.requestFocus()
        }
    }
    LaunchedEffect(controller, interaction, controls, menu, state.playing, state.ended, preview) {
        if (controls && menu == null && state.playing && !state.ended && preview == null) {
            delay(6000)
            controls = false
        }
    }
    LaunchedEffect(controller, state.ended) {
        countdown = 10
        cancelThisNext = false
        if (state.ended) controls = true
    }
    LaunchedEffect(controller, state.ended, state.autoNext, state.next, menu, cancelThisNext, state.error) {
        if (shouldCountDownNext(state.ended, state.autoNext, state.next != null, menu != null, cancelThisNext, state.error != null)) {
            while (countdown > 0) { delay(1000); countdown-- }
            startNext()
        }
    }
    BackHandler {
        interaction++
        when {
            menu != null -> menu = null
            preview != null -> preview = null
            state.ended && state.autoNext && state.next != null && !cancelThisNext -> cancelThisNext = true
            controls && state.error == null -> controls = false
            else -> { controller.close(); close() }
        }
    }
    Box(Modifier.fillMaxSize().background(Color.Black).onPreviewKeyEvent { event ->
        val native = event.nativeKeyEvent
        if (native.action != KeyEvent.ACTION_DOWN) return@onPreviewKeyEvent false
        interaction++
        when (native.keyCode) {
            KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE -> { controller.toggle(); controls = true; true }
            KeyEvent.KEYCODE_MEDIA_PLAY -> { controller.setPlaying(true); true }
            KeyEvent.KEYCODE_MEDIA_PAUSE -> { controller.setPlaying(false); controls = true; true }
            KeyEvent.KEYCODE_MEDIA_FAST_FORWARD -> { controller.skip(30.0); true }
            KeyEvent.KEYCODE_MEDIA_REWIND -> { controller.skip(-30.0); true }
            else -> if (!controls && menu == null && state.error == null) {
                when (native.keyCode) {
                    KeyEvent.KEYCODE_DPAD_LEFT, KeyEvent.KEYCODE_DPAD_RIGHT -> {
                        val delta = if (native.keyCode == KeyEvent.KEYCODE_DPAD_RIGHT) 10 else -10
                        preview = ((preview ?: state.position) + delta).coerceIn(0.0, state.duration.coerceAtLeast(0.0)); true
                    }
                    KeyEvent.KEYCODE_DPAD_UP, KeyEvent.KEYCODE_DPAD_DOWN -> { preview = null; controls = true; true }
                    KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER -> {
                        if (preview != null) { controller.seek(preview!!); preview = null }
                        else { controller.toggle(); controls = true }
                        true
                    }
                    else -> false
                }
            } else false
        }
    }.focusRequester(rootFocus).focusable()) {
        AndroidView(factory = { PlayerView(it).apply {
            player = controller.player
            useController = false
            isFocusable = false
            descendantFocusability = ViewGroup.FOCUS_BLOCK_DESCENDANTS
            setKeepContentOnPlayerReset(true)
        } }, modifier = Modifier.fillMaxSize(), update = {
            it.player = controller.player
            it.keepScreenOn = state.playing
        }, onRelease = { it.keepScreenOn = false; it.player = null })

        if (state.subtitleText.isNotBlank()) Text(state.subtitleText,
            modifier = Modifier.align(Alignment.BottomCenter).padding(horizontal = 44.dp)
                .padding(bottom = when {
                    controls && menu == null -> maxOf(180.dp, with(density) { controlsHeightPx.toDp() }) + 16.dp
                    preview != null -> 160.dp
                    else -> 32.dp
                }),
            color = Color.White, textAlign = TextAlign.Center,
            style = TextStyle(fontSize = listOf(20, 26, 32)[state.subtitleSize].sp, shadow = Shadow(Color.Black, blurRadius = 6f)),
        )
        if (state.loading) Row(Modifier.align(Alignment.Center).background(Color.Black.copy(alpha = .8f)).padding(24.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
            TvIcon("loading")
            Text("正在准备播放…")
        }
        if (preview != null) Column(Modifier.align(Alignment.BottomCenter).fillMaxWidth().background(Color.Black.copy(alpha = .85f)).padding(32.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("跳转至 ${playbackTime(preview!!)} / ${playbackTime(state.duration)}", style = MaterialTheme.typography.headlineSmall)
            Progress(preview!!, state.duration, state.buffered)
            Text("左右调整 10 秒 · 确定跳转 · 返回取消", color = Muted)
        }
        if (controls && menu == null && state.error == null) Column(Modifier.align(Alignment.BottomCenter).fillMaxWidth()
            .onSizeChanged { controlsHeightPx = it.height }
            .background(Color.Black.copy(alpha = .88f)).padding(horizontal = 32.dp, vertical = 22.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(request.title, style = MaterialTheme.typography.titleLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (state.notice != null) Text(state.notice, color = Muted, style = MaterialTheme.typography.bodySmall)
            if (state.nextUnavailable) Text("下一集文件离线，请返回选集", color = Muted, style = MaterialTheme.typography.bodySmall)
            if (state.ended) Text(when {
                state.autoNext && state.next != null && !cancelThisNext -> "播放结束，${countdown} 秒后播放下一集 · 返回键取消本次连播"
                cancelThisNext -> "播放结束 · 已取消本次连播"
                else -> "播放结束"
            })
            Progress(state.position, state.duration, state.buffered)
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(24.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("${playbackTime(state.position)} / ${playbackTime(state.duration)}", maxLines = 1)
                Text("${state.rate}× · ${state.output}", Modifier.weight(1f), color = Muted,
                    maxLines = 1, overflow = TextOverflow.Ellipsis, textAlign = TextAlign.End)
            }
            LazyRow(contentPadding = PaddingValues(horizontal = 8.dp, vertical = 6.dp), horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                item { TvAction(if (state.ended) "重新播放" else if (state.playWhenReady) "暂停" else "播放", { controller.toggle(); interaction++ }, Modifier.focusRequester(playFocus), icon = if (state.ended) "replay" else if (state.playWhenReady) "pause" else "play") }
                item { TvAction("后退 10 秒", { controller.skip(-10.0); interaction++ }, icon = "rewind") }
                item { TvAction("前进 10 秒", { controller.skip(10.0); interaction++ }, icon = "forward10") }
                item { TvAction("播放设置", { menu = "settings" }, icon = "settings") }
                if (state.ended && state.autoNext && state.next != null && !cancelThisNext) item { TvAction("取消本次连播", { cancelThisNext = true }) }
                if (state.next != null) item { TvAction("下一集", ::startNext, icon = "next") }
                item { TvAction("退出播放", { controller.close(); close() }, icon = "close") }
            }
        }
        if (state.error != null) Column(Modifier.align(Alignment.Center).widthIn(max = 720.dp).background(DesignTokens.SurfaceRaised).padding(32.dp), verticalArrangement = Arrangement.spacedBy(20.dp)) {
            Text(state.error, style = MaterialTheme.typography.titleLarge)
            Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                TvAction("重试", { controller.retry() }, Modifier.focusRequester(errorFocus), icon = "refresh")
                TvAction("兼容播放", { controller.retry(true) }, icon = "play")
                TvAction("返回", { controller.close(); close() }, icon = "back")
            }
        }
        if (menu != null && state.error == null) Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha = .75f))) {
            key(menu) {
            LazyColumn(Modifier.align(Alignment.CenterEnd).fillMaxWidth(.5f).fillMaxSize().background(DesignTokens.SurfaceRaised).padding(30.dp),
                contentPadding = PaddingValues(vertical = 4.dp),
                verticalArrangement = Arrangement.spacedBy(16.dp)) {
                item {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(16.dp), verticalAlignment = Alignment.CenterVertically) {
                        TvAction("返回", { menu = if (menu == "settings") null else "settings" }, Modifier.focusRequester(menuFocus), icon = "back")
                        Text(when (menu) { "quality" -> "画质"; "audio" -> "音轨"; "subtitle" -> "字幕"; "rate" -> "播放速度"; else -> "播放设置" },
                            Modifier.weight(1f), style = MaterialTheme.typography.headlineSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
                when (menu) {
                    "settings" -> {
                        item { TvAction("画质 · ${qualityName(state.quality)}", { menu = "quality" }, Modifier.fillMaxWidth(), icon = "quality", fillContent = true) }
                        item { TvAction("音轨", { menu = "audio" }, Modifier.fillMaxWidth(), icon = "volume", fillContent = true) }
                        item { TvAction("字幕", { menu = "subtitle" }, Modifier.fillMaxWidth(), icon = "subtitles", fillContent = true) }
                        item { TvAction("倍速 · ${state.rate}×", { menu = "rate" }, Modifier.fillMaxWidth(), icon = "speed", fillContent = true) }
                        item {
                            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                Text("字幕大小", color = Muted, style = MaterialTheme.typography.labelLarge)
                                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                                    listOf("小", "中", "大").forEachIndexed { i, label ->
                                        TvAction(label, { controller.setSubtitleSize(i) }, Modifier.weight(1f),
                                            selected = state.subtitleSize == i, centerLabel = true)
                                    }
                                }
                            }
                        }
                        item {
                            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                Text("字幕延迟 ${"%+.1f".format(-state.subtitleDelay)} 秒", color = Muted, style = MaterialTheme.typography.labelLarge)
                                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                                    TvAction("提前 0.5 秒", { controller.adjustSubtitleDelay(.5) }, Modifier.weight(1f), centerLabel = true)
                                    TvAction("推迟 0.5 秒", { controller.adjustSubtitleDelay(-.5) }, Modifier.weight(1f), centerLabel = true)
                                }
                            }
                        }
                        item { TvAction("字幕延迟归零", { controller.adjustSubtitleDelay(-state.subtitleDelay) }, Modifier.fillMaxWidth(), icon = "refresh", fillContent = true) }
                        if (request.kind == "episode") item { TvAction("自动下一集 · ${if (state.autoNext) "开" else "关"}", { controller.toggleAutoNext() }, Modifier.fillMaxWidth(), icon = "next", fillContent = true) }
                        item { Text("音量使用遥控器音量键；图片字幕的外观由服务器烧录决定。", color = Muted) }
                    }
                    "quality" -> items(listOf("auto", "source", "1080p", "720p")) { quality -> TvAction(qualityName(quality), { controller.setQuality(quality); menu = null }, Modifier.fillMaxWidth(), selected = state.quality == quality, fillContent = true) }
                    "rate" -> items(PLAYBACK_RATES) { rate -> TvAction("${rate}×", { controller.setRate(rate); menu = null }, Modifier.fillMaxWidth(), selected = state.rate == rate, fillContent = true) }
                    "audio" -> items(state.audios) { track -> TvAction(track.label, { controller.setAudio(track.index); menu = null }, Modifier.fillMaxWidth(), selected = state.audio == track.index, fillContent = true) }
                    "subtitle" -> {
                        item { TvAction("关闭字幕", { controller.setSubtitle(null); menu = null }, Modifier.fillMaxWidth(), selected = state.subtitle == null, icon = "subtitles", fillContent = true) }
                        items(state.subtitles) { track -> TvAction(track.label + if (track.image) " · 图片字幕" else "", { controller.setSubtitle(track.index); menu = null }, Modifier.fillMaxWidth(), selected = state.subtitle == track.index, fillContent = true) }
                    }
                }
            }
            }
        }
    }
}

@Composable
private fun Progress(position: Double, duration: Double, buffered: Double) {
    val played = if (duration > 0) (position / duration).toFloat().coerceIn(0f, 1f) else 0f
    val loaded = if (duration > 0) (buffered / duration).toFloat().coerceIn(0f, 1f) else 0f
    Box(Modifier.fillMaxWidth().height(5.dp).background(DesignTokens.Border)) {
        Box(Modifier.fillMaxWidth(loaded).height(5.dp).background(DesignTokens.TextDim))
        Box(Modifier.fillMaxWidth(played).height(5.dp).background(Accent))
    }
}

private fun qualityName(value: String) = when (value) { "auto" -> "自动"; "source" -> "原画"; else -> value }
private tailrec fun Context.activity(): ComponentActivity? = when (this) {
    is ComponentActivity -> this
    is ContextWrapper -> baseContext.activity()
    else -> null
}
