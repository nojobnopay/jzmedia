package org.jzmedia.tv.ui

import android.app.Activity
import android.content.ActivityNotFoundException
import android.speech.RecognizerIntent
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.withResumed
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import kotlinx.coroutines.CancellationException
import org.jzmedia.tv.data.ApiException
import org.jzmedia.tv.data.DiscoveryApi
import org.jzmedia.tv.data.DiscoveryTarget
import org.jzmedia.tv.data.JzApi

@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun AiDiscoveryScreen(
    api: JzApi, library: Long, entry: String, state: AiDiscoveryScreenState, memory: FocusMemory, enterPress: DiscoveryEnterPress,
    onDetail: (DiscoveryTarget.Detail) -> Unit, onOnlineSearch: (String) -> Unit, isCurrent: () -> Boolean,
) {
    val context = LocalContext.current
    val keyboard = LocalSoftwareKeyboardController.current
    val lifecycle = (context as? ComponentActivity)?.lifecycle
    val current by rememberUpdatedState(isCurrent)
    val service = remember(api) { DiscoveryApi(api) }
    val request = state.request
    val editable = request == null && !state.expired
    val inputFocus = remember { FocusRequester() }
    val restartFocus = remember { FocusRequester() }
    val retryFocus = remember { FocusRequester() }
    var openKeyboard by remember { mutableIntStateOf(0) }
    var recognizerAvailable by remember(state) {
        mutableStateOf(discoverySpeechIntent().resolveActivity(context.packageManager) != null)
    }
    val voiceAvailable = recognizerAvailable && api.viewing?.protected != true
    val voice = remember(state) { DiscoveryVoiceTurn() }
    var transcript by remember(state) { mutableStateOf<String?>(null) }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { answer ->
        if (voice.waiting) {
            val ok = answer.resultCode == Activity.RESULT_OK
            transcript = voice.finish(ok, answer.data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS))
            if (ok && transcript == null) openKeyboard++
        }
    }
    fun startInput() {
        if (!current() || state.request != null || state.expired || voice.waiting) return
        if (voiceAvailable && api.viewing?.protected != true && voice.launch()) {
            keyboard?.hide()
            try {
                launcher.launch(discoverySpeechIntent())
                return
            } catch (_: ActivityNotFoundException) {
                recognizerAvailable = false
            } catch (_: SecurityException) {
                recognizerAvailable = false
            }
            voice.finish(false, null)
        }
        openKeyboard++
    }

    LaunchedEffect(state) {
        withFrameNanos { }
        if (state.launchInput) {
            state.launchInput = false
            startInput()
        } else state.restore()
    }
    LaunchedEffect(openKeyboard) {
        if (openKeyboard == 0) return@LaunchedEffect
        withFrameNanos { }
        if (current() && state.request == null && !state.expired) {
            inputFocus.requestFocus()
            withFrameNanos { }
            keyboard?.show()
        }
    }
    // The system speech Activity can deliver its result during onStart, before foreground focus returns.
    LaunchedEffect(transcript) {
        val text = transcript ?: return@LaunchedEffect
        lifecycle?.withResumed { }
        if (current() && voice.isFresh()) state.submit(text)
        transcript = null
    }
    DisposableEffect(state, lifecycle) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_STOP) {
                state.request?.let { state.fail(it, "找片已中断，可重试", null) }
            }
        }
        lifecycle?.addObserver(observer)
        onDispose {
            lifecycle?.removeObserver(observer)
            voice.finish(false, null)
            state.cancel()
        }
    }
    LaunchedEffect(request) {
        val attempt = request ?: return@LaunchedEffect
        try {
            val result = when (attempt) {
                is DiscoveryRequest.Search -> service.search(library, entry, attempt.query, attempt.session, attempt.source, attempt.reuse)
                is DiscoveryRequest.Refresh -> service.refresh(library, entry, attempt.session)
            }
            if (current()) state.complete(attempt, result)
            else state.fail(attempt, "找片已中断，可重试", null)
        } catch (e: CancellationException) { throw e }
        catch (e: Exception) {
            state.fail(attempt, e.message ?: "找片失败，请重试", (e as? ApiException)?.code)
        }
    }
    BackHandler(enabled = request != null) { state.stopCurrent() }
    fun submit() {
        if (!current() || voice.waiting || !editable || state.draft.isBlank()) return
        enterPress.submitted()
        keyboard?.hide()
        state.submit()
    }

    var notes by remember { mutableStateOf<List<String>?>(null) }
    val list = state.result
    val entryLabel = when (entry) { "movie" -> "电影"; "tv" -> "剧集"; else -> "" }
    LaunchedEffect(state.error, list) {
        withFrameNanos { }
        if (!current() || state.request != null) return@LaunchedEffect
        val next = when {
            state.expired -> restartFocus
            state.retry != null -> retryFocus
            state.error.isNotBlank() || list?.items?.isEmpty() == true -> inputFocus
            else -> return@LaunchedEffect
        }
        next.requestFocus()
    }
    Row(Modifier.fillMaxSize(), horizontalArrangement = Arrangement.spacedBy(24.dp)) {
        Column(Modifier.weight(.35f).fillMaxHeight().verticalScroll(rememberScrollState()).padding(4.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            SectionHeading("智能找片")
            state.lastSearch?.let {
                Text(it.query, style = MaterialTheme.typography.bodyLarge, maxLines = 3, overflow = TextOverflow.Ellipsis)
            }
            DiscoveryInput(state.draft, state::change,
                if (list == null || state.editing) "想看什么电影或剧集？" else "继续说说你的要求",
                Modifier.focusMemory(memory, "discovery:input", inputFocus), enabled = !state.expired,
                readOnly = request != null, submit = ::submit)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (voiceAvailable) TvAction(if (list == null) "说出想看的" else "语音追问", ::startInput,
                    Modifier.focusMemory(memory, "discovery:voice"), enabled = editable && !voice.waiting, icon = "ai")
                TvAction(if (list != null && !state.editing) "追问" else "搜索", ::submit,
                    Modifier.focusMemory(memory, "discovery:submit"), enabled = editable && state.draft.isNotBlank(), icon = "search")
                if (request != null) TvAction("取消", state::stopCurrent, icon = "close")
                if (state.lastSearch != null && editable && !state.editing) TvAction("修改原话", {
                    state.editLast()
                    openKeyboard++
                })
            }
            if (list?.source == "library") TvAction("查公开片目", state::searchPublic,
                Modifier.focusMemory(memory, "discovery:public"), enabled = editable, icon = "search")
            if (state.lastSearch != null || state.expired) TvAction("重新找片", {
                state.restart()
                startInput()
            }, Modifier.focusMemory(memory, "discovery:restart", restartFocus), enabled = request == null, icon = "refresh")
            if (!list?.warnings.isNullOrEmpty()) TvAction("找片说明", { notes = list?.warnings })
        }
        Column(Modifier.weight(.65f).fillMaxHeight().padding(4.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            SectionHeading(if (list?.source == "public") "公开片目" else "当前媒体库", entryLabel)
            when {
                request != null -> Status(if (request is DiscoveryRequest.Refresh) "正在核对片单…" else "正在找片…", icon = "loading")
                state.error.isNotBlank() -> {
                    Status(state.error, icon = "info")
                    if (state.retry != null) TvAction("重试", state::retryFailed,
                        Modifier.focusMemory(memory, "discovery:retry", retryFocus), icon = "refresh")
                }
                list != null -> {
                    val answer = list.clarification.ifBlank { list.summary }
                    if (answer.isNotBlank()) Text(answer, style = MaterialTheme.typography.bodyLarge)
                    DiscoveryResults(api, list, state, memory, onOpen = { item, index, offset ->
                        if (current()) when (val target = item.target) {
                            is DiscoveryTarget.Detail -> {
                                state.leaveForDetail(item.key, index, offset)
                                onDetail(target)
                            }
                            is DiscoveryTarget.Online -> {
                                state.leaveForDetail(item.key, index, offset)
                                onOnlineSearch(target.title)
                            }
                            null -> Unit
                        }
                    })
                }
                else -> {
                    Text("可以这样找", style = MaterialTheme.typography.labelLarge, color = Muted)
                    val examples = if (entry == "tv") listOf("今年评分最高的 10 部剧集", "找几部我没看过的悬疑剧")
                        else listOf("今年评分最高的 10 部电影", "推荐几部我没看过的文艺片")
                    examples.forEach { example ->
                        TvAction(example, { if (current()) { keyboard?.hide(); state.submit(example) } },
                            Modifier.fillMaxWidth(), fillContent = true)
                    }
                }
            }
        }
    }
    notes?.let { TextDialog("找片说明", it) { notes = null } }
}
