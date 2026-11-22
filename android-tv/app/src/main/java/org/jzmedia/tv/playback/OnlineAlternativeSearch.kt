package org.jzmedia.tv.playback

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.jzmedia.tv.data.ApiException
import org.jzmedia.tv.data.OnlineAlternative
import org.jzmedia.tv.data.OnlineAlternativeTask
import org.jzmedia.tv.data.OnlineApi
import org.jzmedia.tv.data.OnlineIdentity

internal enum class AlternativeTrigger { Inactive, Waiting, Error }

internal fun alternativeTrigger(state: OnlinePlaybackState, supported: Boolean): AlternativeTrigger = when {
    !supported || !state.networkPlayback || state.ended -> AlternativeTrigger.Inactive
    state.error != null -> AlternativeTrigger.Error
    state.playWhenReady && (state.awaitingSeek || state.loading || !state.firstFrame) -> AlternativeTrigger.Waiting
    else -> AlternativeTrigger.Inactive
}

/** Polls one bounded step at a time. A playback error leaves the current job and its results available. */
internal class OnlineAlternativeSearch(private val online: OnlineApi, private val scope: CoroutineScope,
                                       private val origin: OnlineIdentity) {
    var items by mutableStateOf(emptyList<OnlineAlternative>())
        private set
    var notice by mutableStateOf<String?>(null)
        private set
    var busy by mutableStateOf(false)
        private set
    var selecting by mutableStateOf(false)
        private set
    private var generation = 0
    private var work: Job? = null
    private var switchJob: Job? = null

    fun clear() {
        generation++
        work?.cancel(); work = null
        busy = false; items = emptyList(); notice = null
    }

    fun probe() {
        if (busy || selecting) return
        clear()
        val turn = generation
        busy = true
        notice = "正在探测同一节目的备用线路…"
        work = scope.launch {
            var taskId: String? = null
            try {
                suspend fun awaitSlot(request: suspend () -> OnlineAlternativeTask): OnlineAlternativeTask {
                    while (true) {
                        try { return request() }
                        catch (error: ApiException) {
                            if (error.status != 429) throw error
                            if (turn == generation) notice = "正在等待探测名额…"
                            delay(500)
                        }
                    }
                }
                val completed = withTimeoutOrNull(45_000) {
                    var task = awaitSlot { online.startAlternatives(origin) }
                    taskId = task.id
                    while (true) {
                        currentCoroutineContext().ensureActive()
                        if (turn != generation) return@withTimeoutOrNull false
                        // Stable insertion order and IDs keep an already focused action bound to its original target.
                        val updated = task.items.associateBy { it.identity }
                        items = items.map { updated[it.identity] ?: it } + task.items.filter { row -> items.none { it.identity == row.identity } }
                        notice = when {
                            items.isNotEmpty() && task.status == "partial" -> "部分线路未能测完。切换后使用所选线路自己的观看进度。"
                            items.isNotEmpty() -> "切换后使用所选线路自己的观看进度。"
                            task.status == "queued" -> "正在等待探测名额…"
                            task.status == "done" -> "暂未找到可推荐的备用线路。"
                            task.status == "partial" -> "部分线路未能完成探测，可重新探测。"
                            else -> "正在试读同一节目的备用线路…"
                        }
                        if (task.finished) break
                        if (task.retryAfterMs > 0 || task.status == "queued")
                            delay(task.retryAfterMs.coerceAtLeast(250))
                        task = awaitSlot { online.advanceAlternatives(task.id, origin) }
                    }
                    true
                }
                if (turn == generation && completed == null) notice = "探测等待超时，可重新探测。"
            } catch (error: CancellationException) { throw error }
            catch (error: Exception) {
                if (turn == generation) {
                    if ((error as? ApiException)?.status in listOf(401, 403, 409)) items = emptyList()
                    notice = "备用线路探测未完成，可重新探测。"
                }
            } finally {
                if (turn == generation) busy = false
                taskId?.let { id -> withContext(NonCancellable) {
                    withTimeoutOrNull(2500) { runCatching { online.cancelAlternatives(id) } }
                } }
            }
        }
    }

    fun cancelSelection() {
        switchJob?.cancel()
        if (selecting) notice = "已取消切换。"
    }

    suspend fun choose(target: OnlineAlternative, originRequest: OnlinePlaybackRequest, handoff: (OnlinePlaybackRequest) -> Unit) {
        if (selecting || items.none { it == target }) return
        val token = target.recommendation
        generation++
        work?.cancel(); busy = false
        switchJob = currentCoroutineContext()[Job]
        selecting = true; notice = "正在核对并准备所选线路…"
        var pending: PendingOnlineSession? = null
        try {
            // Do not read any progress twice, release the current decoder, or create a target writer before verification.
            val destination = coroutineScope {
                val readProgress = async {
                    val state = withTimeoutOrNull(5000) { online.itemState(target.identity) }
                        ?: throw ApiException(0, "读取观看进度超时")
                    if (!state.available || originRequest.scopeToken.isNotEmpty() && state.scopeToken != originRequest.scopeToken)
                        throw ApiException(409, "观看身份已变化")
                    state
                }
                val descriptor = online.createSession(target.identity, recommendation = token)
                pending = PendingOnlineSession(descriptor, online::deleteSession)
                val saved = readProgress.await()
                val progress = saved.selectedProgress
                OnlinePlaybackRequest(target.identity,
                    if (target.identity.kind == "episode") target.title + " · " + target.entryLabel else target.title,
                    target.externalId, if (progress?.completed == false) progress.position else 0.0,
                    saved.scopeToken, progress?.writerRevision ?: 0, autoplay = originRequest.autoplay,
                    localCache = descriptor.cached, preparedSession = pending)
            }
            currentCoroutineContext().ensureActive()
            handoff(destination)
            pending = null
        } catch (error: CancellationException) { throw error }
        catch (error: Exception) {
            val status = (error as? ApiException)?.status
            if (status in listOf(401, 403, 409)) items = emptyList()
            notice = when (status) {
                409 -> "来源或节目已变化，请重新探测。"
                429 -> "播放会话已满，可稍后重试。"
                else -> "无法准备所选线路，可重新探测或继续原线路。"
            }
        } finally {
            pending?.discard()
            switchJob = null
            selecting = false
        }
    }
}
