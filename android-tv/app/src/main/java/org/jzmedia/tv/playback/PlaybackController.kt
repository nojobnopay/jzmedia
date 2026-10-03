package org.jzmedia.tv.playback

import android.content.Context
import android.util.Log
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.TrackSelectionOverride
import androidx.media3.common.Tracks
import androidx.media3.common.Timeline
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.session.MediaSession
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.OkHttpClient
import org.json.JSONArray
import org.json.JSONObject
import org.jzmedia.tv.data.JzApi
import java.io.IOException
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

@UnstableApi
class PlaybackController(context: Context, private val api: JzApi, val request: PlaybackRequest) {
    private val appContext = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val writes = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val saveLock = Mutex()
    private val preferences = appContext.getSharedPreferences("playback", Context.MODE_PRIVATE)
    private val intent = PlaybackIntent()
    private var selectionNotice: String? = null
    private val delayKey = "delay.${api.baseUrl}.${request.kind}.${request.id}"
    var state by mutableStateOf(PlaybackState(
        subtitleDelay = preferences.getFloat(delayKey, 0f).toDouble(),
        subtitleSize = preferences.getInt("subtitleSize", 1),
        autoNext = preferences.getBoolean("autoNext", true),
        rate = request.continuation?.rate?.takeIf { it in PLAYBACK_RATES } ?: 1f,
    ))
        private set
    private var media = JSONObject()
    private var capabilities = JSONObject()
    private var sessionId: String? = null
    private var mediaStart = 0.0
    private var complete = false
    private var generation = 0
    private var subtitleGeneration = 0
    private var disposed = false
    private var ready = false
    private var reload: Job? = null
    private var subtitleJob: Job? = null
    private var timeline = SubtitleTimeline(emptyList())
    private var subtitleMode = "none"
    private var segmentType = "fmp4"
    private var validPosition: Double? = null
    private var markedWatched = false
    private var initialized = false
    private var initialization: Job? = null
    private var watchedJob: Job? = null
    private var initialHlsSeek: Pair<String, Long>? = null
    private val saveSequence = AtomicLong()

    private val http = OkHttpClient.Builder()
        .followRedirects(false).followSslRedirects(false)
        .connectTimeout(10, TimeUnit.SECONDS).readTimeout(35, TimeUnit.SECONDS)
        .addInterceptor { chain ->
            val url = chain.request().url.toString()
            val headers = api.authHeadersFor(url)
            // absoluteUrl validates the origin; no server playlist can send credentials elsewhere.
            try { api.absoluteUrl(url) }
            catch (error: IllegalArgumentException) { throw IOException("播放地址不属于当前服务器", error) }
            val builder = chain.request().newBuilder()
            headers.forEach { (name, value) -> builder.header(name, value) }
            val response = chain.proceed(builder.build())
            if (response.isRedirect) {
                response.close()
                throw IOException("播放地址发生重定向，请检查服务器地址")
            }
            response
        }.build()

    val player: ExoPlayer = ExoPlayer.Builder(appContext)
        .setMediaSourceFactory(DefaultMediaSourceFactory(OkHttpDataSource.Factory(http)))
        .setSeekBackIncrementMs(30_000)
        .setSeekForwardIncrementMs(30_000)
        .build().apply {
            setAudioAttributes(AudioAttributes.Builder().setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_MOVIE).build(), true)
            setHandleAudioBecomingNoisy(true)
            trackSelectionParameters = trackSelectionParameters.buildUpon().setTrackTypeDisabled(C.TRACK_TYPE_TEXT, true).build()
            addListener(object : Player.Listener {
                override fun onTimelineChanged(timeline: Timeline, reason: Int) {
                    val pending = initialHlsSeek ?: return
                    if (disposed || timeline.isEmpty || player.currentMediaItem?.mediaId != pending.first) return
                    val window = timeline.getWindow(player.currentMediaItemIndex, Timeline.Window())
                    if (window.isPlaceholder) return
                    // Media3's MaskingMediaSource treats an initial zero as the live default.
                    // Growing server HLS is a movie: seek only after the real timeline exists,
                    // and keep playback paused until this explicit source-relative seek.
                    initialHlsSeek = null
                    player.seekTo(pending.second)
                    intent.prepared()
                    player.playWhenReady = intent.playerShouldPlay
                }

                override fun onPlaybackStateChanged(playbackState: Int) {
                    if (disposed) return
                    if (playbackState == Player.STATE_READY) ready = true
                    val ended = playbackState == Player.STATE_ENDED
                    state = state.copy(loading = playbackState == Player.STATE_BUFFERING || (!ready && playbackState != Player.STATE_ENDED), ended = ended)
                    if (ended) {
                        updatePosition()
                        queueSave()
                        markWatched()
                    }
                }

                override fun onIsPlayingChanged(isPlaying: Boolean) {
                    if (!disposed) state = state.copy(playing = isPlaying)
                }

                override fun onPlayWhenReadyChanged(playWhenReady: Boolean, reason: Int) {
                    if (!disposed && state.error == null) {
                        intent.observePlayer(playWhenReady)
                        state = state.copy(playWhenReady = intent.wantsPlay)
                    }
                }

                override fun onTracksChanged(tracks: Tracks) {
                    if (!disposed && !tracks.isEmpty && state.audios.isNotEmpty() && !applyAudioTrack()) {
                        val current = generation
                        scope.launch {
                            if (!disposed && current == generation) recoverAudioMapping()
                        }
                    }
                }

                override fun onPlayerError(error: PlaybackException) {
                    if (!disposed) state = state.copy(loading = false, playing = false,
                        error = "播放失败（${error.errorCodeName}），可重试或选择兼容播放")
                    Log.w("JzPlayback", "Playback error code=${error.errorCode}")
                }
            })
        }
    private val mediaSession = MediaSession.Builder(appContext, player).build()

    init {
        initialize()
        scope.launch {
            var tick = 0
            while (true) {
                delay(200)
                if (!disposed) {
                    updatePosition()
                    if (++tick % 50 == 0) {
                        queueSave()
                        if (ready && shouldMarkWatched(state.position, state.duration)) markWatched()
                    }
                    if (tick % 100 == 0) {
                        val sid = sessionId
                        if (sid != null) launch {
                            try { api.post("/api/stream/sessions/$sid/ping", JSONObject()) }
                            catch (error: CancellationException) { throw error }
                            catch (error: Exception) {
                                if (sessionId == sid && !disposed) state = state.copy(notice = "播放会话保活失败，可重试播放")
                                Log.w("JzPlayback", "Session heartbeat failed")
                            }
                        }
                    }
                }
            }
        }
    }

    private fun initialize(compatible: Boolean = false) {
        initialization?.cancel()
        state = state.copy(loading = true, error = null)
        initialization = scope.launch {
            try {
                media = api.getPlayback("/api/stream/${request.id}/media?kind=${request.kind}")
                ensureActive()
                capabilities = withContext(Dispatchers.Default) { NativeCapabilities.detect(appContext, media) }
                val audios = media.optJSONArray("audio") ?: JSONArray()
                val subs = media.optJSONArray("subs") ?: JSONArray()
                val audio = (0 until audios.length()).firstOrNull { audios.optJSONObject(it)?.optInt("default") == 1 } ?: 0
                val sub = (0 until subs.length()).firstOrNull {
                    val item = subs.optJSONObject(it)
                    item?.optInt("default") == 1 && item.optInt("image") != 1
                }
                val audioTracks = trackList(audios)
                val subtitleTracks = trackList(subs)
                val selection = continuationSelection(audioTracks, subtitleTracks, audio, sub, request.continuation)
                selectionNotice = selection.notice
                state = state.copy(duration = media.optDouble("duration", 0.0).finiteOrZero(), audio = selection.audio,
                    subtitle = selection.subtitle, audios = audioTracks, subtitles = subtitleTracks)
                val progress = if (request.resume) api.get(progressPath()) else {
                    api.delete(progressPath())
                    JSONObject()
                }
                ensureActive()
                val position = progress.optDouble("position", 0.0).finiteOrZero().coerceAtLeast(0.0)
                val start = if (position > 15 && !shouldMarkWatched(position, state.duration)) position else 0.0
                initialized = true
                if (compatible) useCompatibleCapabilities()
                open(start)
                if (request.kind == "episode") fetchNext()
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                if (!disposed) state = state.copy(loading = false, error = error.message ?: "无法获取播放信息")
            }
        }
    }

    private fun progressPath() = "/api/stream/progress?version_id=${request.id}&kind=${request.kind}"

    private fun trackList(items: JSONArray): List<MediaTrack> = (0 until items.length()).map { i ->
        val item = items.optJSONObject(i) ?: JSONObject()
        val title = listOf(item.optString("lang"), item.optString("title"), item.optString("codec"))
            .filter { it.isNotBlank() && it != "null" && it != "und" }.distinct().joinToString(" · ")
        MediaTrack(i, "${i + 1}. ${title.ifEmpty { "未标注" }}", item.optInt("image") == 1, item.optInt("ff_index", -1),
            language = item.optString("lang").takeUnless { it == "null" }.orEmpty(),
            title = item.optString("title").takeUnless { it == "null" }.orEmpty(),
            codec = item.optString("codec").takeUnless { it == "null" }.orEmpty(),
            channels = item.optInt("channels"), forced = item.optInt("forced") == 1)
    }

    private fun body(start: Double) = JSONObject().put("client", "android_tv").put("kind", request.kind)
        .put("caps", capabilities).put("quality", state.quality).put("audio", state.audio)
        .put("sub", state.subtitle ?: JSONObject.NULL).put("start", start)

    private fun open(start: Double) {
        if (disposed) return
        val current = ++generation
        reload?.cancel()
        initialHlsSeek = null
        intent.beginPreparation()
        player.pause()
        player.stop()
        ready = false
        state = state.copy(loading = true, error = null, ended = false, playing = false, position = start, subtitleText = "")
        val oldSession = sessionId
        sessionId = null
        closeSession(oldSession)
        reload = scope.launch {
            var createdSession: String? = null
            try {
                val decision = api.postPlayback("/api/stream/${request.id}/decide", body(start))
                ensureActive()
                if (disposed || current != generation) return@launch
                val method = decision.optString("method")
                if (method == "blocked") throw IOException("这台电视暂不支持该片源，请选择兼容播放或其他版本")
                val plan = decision.optJSONObject("plan") ?: JSONObject()
                segmentType = plan.optString("seg", "fmp4")
                subtitleMode = decision.optString("subtitle_mode", "none")
                val url: String
                val initial: Double
                if (method == "direct") {
                    url = api.absoluteUrl(decision.getString("direct_url"))
                    mediaStart = 0.0
                    initial = start
                    complete = true
                } else {
                    val session = api.postPlayback("/api/stream/${request.id}/sessions", body(start))
                    createdSession = session.getString("session_id")
                    ensureActive()
                    if (disposed || current != generation) return@launch
                    url = api.absoluteUrl(session.getString("playlist_url"))
                    mediaStart = session.optDouble("media_start", 0.0).finiteOrZero()
                    initial = session.optDouble("initial_time", 0.0).finiteOrZero()
                    complete = session.optBoolean("complete")
                    sessionId = createdSession
                }
                val itemId = "${request.kind}:${request.id}:$current"
                val item = MediaItem.Builder().setUri(url).setMediaId(itemId)
                if (method != "direct") item.setMimeType(MimeTypes.APPLICATION_M3U8)
                val size = if (plan.optInt("height") > 0) "${plan.optInt("height")}p" else "${media.optInt("height")}p"
                state = state.copy(method = method, output = "$size · ${if (method == "direct" || plan.optBoolean("vcopy")) "原视频" else "视频转码"}",
                    notice = listOfNotNull(selectionNotice, reasons(decision.optJSONArray("reasons"))).joinToString("；").ifEmpty { null })
                val initialMs = (initial * 1000).toLong()
                initialHlsSeek = if (method == "direct") null else itemId to initialMs
                if (method == "direct") intent.prepared()
                player.playWhenReady = intent.playerShouldPlay
                player.setMediaItem(item.build(), initialMs)
                player.setPlaybackSpeed(state.rate)
                player.prepare()
                loadSubtitle()
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                if (!disposed && current == generation) state = state.copy(loading = false, error = error.message ?: "无法开始播放")
            } finally {
                if (createdSession != null && (disposed || current != generation || sessionId != createdSession)) {
                    withContext(NonCancellable) { deleteSession(createdSession) }
                }
            }
        }
    }

    private fun reasons(values: JSONArray?): String? {
        val reasons = (0 until (values?.length() ?: 0)).map { values!!.optString(it) }
        return when {
            "hdr_no_tonemap" in reasons || "dovi_no_base_tonemap" in reasons -> "此片需要 HDR 色彩转换，当前服务器可能无法准确还原色彩。可返回选择 SDR 版本。"
            subtitleMode == "burn" -> "图片字幕由服务器烧录，切换字幕会重新准备播放"
            state.subtitle?.let { media.optJSONArray("subs")?.optJSONObject(it)?.optString("codec") in listOf("ass", "ssa") } == true -> "ASS 字幕使用兼容文本，部分样式会简化"
            else -> null
        }
    }

    private fun updatePosition() {
        if (!ready || disposed) return
        val position = sourcePosition(player.currentPosition, mediaStart, state.duration)
        validPosition = position
        val duration = state.duration.takeIf { it > 0 } ?: player.duration.takeIf { it > 0 }?.let { mediaStart + it / 1000.0 } ?: 0.0
        state = state.copy(position = position, duration = duration,
            buffered = sourcePosition(player.bufferedPosition, mediaStart, duration),
            subtitleText = timeline.textAt(position + state.subtitleDelay))
    }

    fun toggle() {
        if (state.ended) { setPlaying(true); seek(0.0); return }
        setPlaying(!intent.wantsPlay)
    }

    fun setPlaying(play: Boolean) {
        if (disposed) return
        intent.request(play)
        state = state.copy(playWhenReady = play)
        player.playWhenReady = intent.playerShouldPlay
        if (!play) queueSave()
    }

    fun seek(target: Double) {
        if (disposed || !target.isFinite()) return
        val value = if (state.duration > 0) target.coerceIn(0.0, (state.duration - 0.1).coerceAtLeast(0.0)) else target.coerceAtLeast(0.0)
        validPosition = value
        val relative = value - mediaStart
        state = state.copy(position = value, ended = false)
        if (ready && relative >= 0 && player.isCurrentMediaItemSeekable &&
            ((complete && player.duration > 0 && relative * 1000 < player.duration) || relative * 1000 <= player.bufferedPosition)) {
            player.seekTo((relative * 1000).toLong())
            player.playWhenReady = intent.playerShouldPlay
        } else open(value)
    }

    fun skip(seconds: Double) { seek(state.position + seconds) }

    fun retry(compatible: Boolean = false) {
        if (!initialized) { initialize(compatible); return }
        if (compatible) useCompatibleCapabilities()
        open(validPosition ?: state.position)
    }

    private fun useCompatibleCapabilities() {
        capabilities = compatibleCapabilities(capabilities, media)
        state = state.copy(quality = "720p")
    }

    fun setQuality(value: String) {
        if (value == state.quality) return
        state = state.copy(quality = value)
        open(state.position)
    }

    fun setRate(value: Float) {
        if (value !in PLAYBACK_RATES) return
        state = state.copy(rate = value)
        player.setPlaybackSpeed(value)
    }

    fun setAudio(index: Int) {
        if (index !in state.audios.indices || index == state.audio) return
        state = state.copy(audio = index)
        if (!ready || (state.method != "direct" && segmentType == "ts")) open(state.position)
        else if (!applyAudioTrack()) recoverAudioMapping()
    }

    private fun applyAudioTrack(): Boolean {
        val groups = player.currentTracks.groups.filter { it.type == C.TRACK_TYPE_AUDIO }
        val candidates = groups.flatMap { group -> (0 until group.length).map { group to it } }
        val selected = audioCandidateIndex(candidates.map { (group, i) ->
            val format = group.getTrackFormat(i)
            AudioCandidate(format.id, format.label, group.isTrackSupported(i))
        }, state.audio, state.audios.size, direct = state.method == "direct", singleAudio = state.method != "direct" && segmentType == "ts") ?: return false
        val (group, index) = candidates[selected]
        if (!group.isTrackSelected(index)) player.trackSelectionParameters = player.trackSelectionParameters.buildUpon()
            .setOverrideForType(TrackSelectionOverride(group.mediaTrackGroup, index)).build()
        return true
    }

    private fun recoverAudioMapping() {
        if (state.method == "direct") {
            // A missing/unsupported native track must not silently select another language.
            capabilities.put("containers", JSONObject()).put("audio_track_selection", false)
            val track = media.optJSONArray("audio")?.optJSONObject(state.audio)
            val probes = capabilities.optJSONObject("probes") ?: JSONObject()
            val formats = track?.optJSONArray("caps") ?: JSONArray()
            for (i in 0 until formats.length()) probes.put(formats.getString(i), false)
            capabilities.put("probes", probes)
            open(state.position)
        } else {
            state = state.copy(loading = false, error = "无法选择该音轨，请返回选择其他版本或重试")
            player.pause()
        }
    }

    fun setSubtitle(index: Int?) {
        if (index != null && index !in state.subtitles.indices) return
        val wasBurn = subtitleMode == "burn"
        state = state.copy(subtitle = index, subtitleText = "")
        if (!ready || wasBurn || index?.let { state.subtitles[it].image } == true) open(state.position)
        else { subtitleMode = if (index == null) "none" else "webvtt"; loadSubtitle() }
    }

    private fun loadSubtitle() {
        val current = ++subtitleGeneration
        subtitleJob?.cancel()
        timeline = SubtitleTimeline(emptyList())
        state = state.copy(subtitleText = "")
        val index = state.subtitle ?: return
        if (subtitleMode != "webvtt") return
        subtitleJob = scope.launch {
            try {
                val text = api.getText("/api/stream/${request.id}/sub/$index.vtt?kind=${request.kind}")
                val parsed = withContext(Dispatchers.Default) { SubtitleTimeline(parseWebVtt(text)) }
                ensureActive()
                if (!disposed && current == subtitleGeneration) timeline = parsed
            } catch (error: CancellationException) { throw error }
            catch (error: Exception) {
                if (!disposed && current == subtitleGeneration) state = state.copy(notice = "字幕加载失败，可重新选择字幕重试")
                Log.w("JzPlayback", "Subtitle load failed")
            }
        }
    }

    fun adjustSubtitleDelay(delta: Double) {
        val value = ((state.subtitleDelay + delta) * 10).toInt().coerceIn(-100, 100) / 10.0
        state = state.copy(subtitleDelay = value)
        preferences.edit().putFloat(delayKey, value.toFloat()).apply()
    }

    fun setSubtitleSize(value: Int) {
        state = state.copy(subtitleSize = value.coerceIn(0, 2))
        preferences.edit().putInt("subtitleSize", state.subtitleSize).apply()
    }

    fun toggleAutoNext() {
        state = state.copy(autoNext = !state.autoNext)
        preferences.edit().putBoolean("autoNext", state.autoNext).apply()
    }

    private suspend fun fetchNext() {
        try {
            val next = api.get("/api/tv/episodes/${request.id}/next").optJSONObject("next")
            if (!disposed) state = state.copy(next = nextPlaybackRequest(next),
                nextUnavailable = next != null && (!next.optBoolean("exists", true) || next.optInt("missing") != 0))
        } catch (error: CancellationException) { throw error }
        catch (error: Exception) { if (!disposed) state = state.copy(notice = "下一集信息暂不可用，可返回选集") }
    }

    fun nextRequest(): PlaybackRequest? = nextRequestWithPreferences(state)

    private fun markWatched() {
        if (markedWatched || request.kind == "extra") return
        markedWatched = true
        watchedJob = writes.launch {
            try {
                withTimeoutOrNull(10_000) {
                    if (request.kind == "episode") api.post("/api/tv/episodes/${request.id}/watched", JSONObject().put("watched", true))
                    else api.post("/api/movies/batch", JSONObject().put("ids", JSONArray().put(request.id)).put("ops", JSONObject().put("watched", true)))
                } ?: throw IOException("Watched update timeout")
            } catch (error: CancellationException) { throw error }
            catch (error: Exception) {
                withContext(Dispatchers.Main) {
                    markedWatched = false
                    if (!disposed) state = state.copy(notice = "已看状态保存失败，稍后自动重试")
                }
                Log.w("JzPlayback", "Unable to save watched state")
            }
        }
    }

    private fun queueSave() {
        val position = validPosition ?: return
        val duration = state.duration
        val sequence = saveSequence.incrementAndGet()
        writes.launch { save(position, duration, sequence) }
    }

    private suspend fun save(position: Double, duration: Double, sequence: Long) = saveLock.withLock {
        if (sequence < saveSequence.get()) return@withLock
        try {
            withTimeoutOrNull(10_000) { api.post(progressPath(), JSONObject().put("position", position).put("duration", duration)) }
                ?: throw IOException("Progress update timeout")
        } catch (error: CancellationException) { throw error }
        catch (error: Exception) {
            withContext(Dispatchers.Main) { if (!disposed) state = state.copy(notice = "观看进度保存失败，稍后自动重试") }
            Log.w("JzPlayback", "Unable to save progress")
        }
    }

    private fun closeSession(sid: String?) { if (sid != null) writes.launch { deleteSession(sid) } }
    private suspend fun deleteSession(sid: String?) {
        if (sid == null) return
        try { withTimeoutOrNull(15_000) { api.delete("/api/stream/sessions/$sid") } }
        catch (error: Exception) { Log.w("JzPlayback", "Unable to release session; server TTL will reclaim it") }
    }

    fun close() {
        if (disposed) return
        updatePosition()
        disposed = true
        initialHlsSeek = null
        generation++
        subtitleGeneration++
        val sid = sessionId
        sessionId = null
        val position = validPosition
        val duration = state.duration
        val sequence = saveSequence.incrementAndGet()
        player.pause()
        mediaSession.release()
        player.release()
        scope.cancel()
        writes.launch {
            coroutineScope {
                launch { if (position != null) save(position, duration, sequence) }
                launch { deleteSession(sid) }
            }
            watchedJob?.join()
            http.dispatcher.cancelAll()
            http.connectionPool.evictAll()
            writes.cancel()
        }
    }
}

private fun Double.finiteOrZero(): Double = takeIf { it.isFinite() } ?: 0.0
