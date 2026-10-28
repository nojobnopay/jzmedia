package org.jzmedia.tv.playback

import android.content.Context
import android.hardware.display.DisplayManager
import android.os.Build
import android.view.Display
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.Format
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.TrackSelectionOverride
import androidx.media3.common.Tracks
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.HttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.mediacodec.MediaCodecUtil
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.session.MediaSession
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.supervisorScope
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withTimeoutOrNull
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.OnlineApi
import org.jzmedia.tv.data.OnlineDescriptor
import org.jzmedia.tv.data.onlineErrorMessage

/** Online media always stays on its original timeline and is decoded by this TV. */
@UnstableApi
class OnlinePlaybackController(context: Context, private val api: JzApi, val request: OnlinePlaybackRequest) {
    private val appContext = context.applicationContext
    private val online = OnlineApi(api)
    private val sessions = OnlineSessionOwner({ online.deleteSession(it) })
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val writes = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val http = onlineMediaHttpClient(api)
    private val packets = Channel<Pair<OnlineProgressWriter, OnlineProgressSnapshot>>(Channel.CONFLATED)
    private val saves = writes.launch { for ((writer, snapshot) in packets) writer.save(snapshot) }
    var state by mutableStateOf(OnlinePlaybackState(position = request.position))
        private set
    private var disposed = false
    private var generation = 0
    private var preparation: Job? = null
    private var nextJob: Job? = null
    private var nextGeneration = 0
    private var descriptor: OnlineDescriptor? = null
    private var writer = newWriter(request.scopeToken, request.writerRevision)
    private var endedHandled = false
    private var loadingSource = false
    private var renewalWindow = 0L
    private var renewalAttempts = 0
    private var exit: Job? = null

    val player: ExoPlayer = ExoPlayer.Builder(appContext, PlaybackRenderersFactory(appContext, strictDolbyFromMime = true))
        .setSeekBackIncrementMs(10_000).setSeekForwardIncrementMs(10_000).build().apply {
            setAudioAttributes(AudioAttributes.Builder().setUsage(C.USAGE_MEDIA)
                .setContentType(C.AUDIO_CONTENT_TYPE_MOVIE).build(), true)
            setHandleAudioBecomingNoisy(true)
            trackSelectionParameters = trackSelectionParameters.buildUpon().setTrackTypeDisabled(C.TRACK_TYPE_TEXT, true).build()
            addListener(object : Player.Listener {
                override fun onRenderedFirstFrame() {
                    if (!disposed && !loadingSource) {
                        state = state.copy(firstFrame = true, loading = false)
                        updatePosition(); queueSave()
                    }
                }
                override fun onPlaybackStateChanged(playbackState: Int) {
                    if (disposed || loadingSource || endedHandled || state.error != null) return
                    state = state.copy(loading = playbackState == Player.STATE_BUFFERING)
                    if (playbackState == Player.STATE_ENDED) finish()
                }
                override fun onIsPlayingChanged(isPlaying: Boolean) {
                    if (!disposed) state = state.copy(playing = isPlaying)
                }
                override fun onPlayWhenReadyChanged(playWhenReady: Boolean, reason: Int) {
                    if (!disposed && !loadingSource && !endedHandled) {
                        state = state.copy(playWhenReady = playWhenReady)
                        if (!playWhenReady) { updatePosition(); queueSave() }
                    }
                }
                override fun onTracksChanged(tracks: Tracks) {
                    if (!disposed && !loadingSource && state.error == null) {
                        val videos = tracks.groups.filter { it.type == C.TRACK_TYPE_VIDEO }
                        val selected = videos.flatMap { group -> (0 until group.length).filter { group.isTrackSelected(it) }
                            .map { group.getTrackFormat(it) to group.isTrackSupported(it) } }
                        val error = selected.firstNotNullOfOrNull { (format, supported) ->
                            val dolby = format.sampleMimeType == DolbyVisionCapabilities.MIME_TYPE
                            onlineVideoTrackError(format.sampleMimeType, format.codecs, supported,
                                !dolby || displaySupportsDolby(), !dolby || nativeDolbyDecoder(format))
                        } ?: if (videos.isNotEmpty() && selected.isEmpty())
                            "当前电视没有可解码的视频轨，请选择其他线路或播放设备。" else null
                        if (error != null) rejectTracks(error) else updateTracks(tracks)
                    }
                }
                override fun onPlayerError(error: PlaybackException) {
                    if (!disposed && !loadingSource) playbackError(error)
                }
            })
        }
    private val mediaSession = MediaSession.Builder(context.applicationContext, player).build()

    init {
        begin(request.position, paused = false)
        scope.launch {
            var tick = 0
            while (true) {
                delay(250)
                if (!disposed && !endedHandled) {
                    updatePosition()
                    if (++tick % 40 == 0) queueSave()
                }
            }
        }
    }

    private fun newWriter(scopeToken: String, revision: Long): OnlineProgressWriter {
        lateinit var next: OnlineProgressWriter
        next = OnlineProgressWriter(online, request.identity, scopeToken, revision) { notice ->
            scope.launch { if (!disposed && writer === next) state = state.copy(notice = notice) }
        }
        return next
    }

    private fun begin(position: Double, paused: Boolean, freshWriter: Boolean = false) {
        if (disposed) return
        cancelNext()
        val ticket = ++generation
        preparation?.cancel()
        loadingSource = true; endedHandled = false
        val previous = descriptor
        descriptor = null
        player.pause(); player.stop(); player.clearMediaItems()
        http.dispatcher.cancelAll()
        if (previous != null) writes.launch { deleteSession(previous.sessionId) }
        state = state.copy(loading = true, playing = false, firstFrame = false, ended = false, error = null,
            position = position, playWhenReady = !paused, seekable = false,
            audios = emptyList(), subtitles = emptyList(), next = null)
        preparation = scope.launch {
            val lease = PlaybackSessionLease({ !disposed && generation == ticket }, ::deleteSession)
            try {
                if (freshWriter) {
                    // Retry is an explicit intent; a session refresh never changes its frozen scope.
                    val saved = online.playbackState(request.identity)
                    if (disposed || generation != ticket) return@launch
                    writer = newWriter(saved?.scopeToken.orEmpty(), saved?.selectedProgress?.writerRevision ?: 0)
                }
                val result = lease.create({ withTimeout(45_000) { online.createSession(request.identity) } }) { it.sessionId }
                sessions.own(result.sessionId)
                descriptor = result
                // A decoded frame must never race final DELETE with an unfinished initial claim.
                writer.prepare(result.sessionId)
                if (disposed || generation != ticket) return@launch
                state = state.copy(transport = result.transport, duration = result.durationSeconds ?: 0.0, seekable = result.seekable)
                val item = MediaItem.Builder().setMediaId(result.sessionId).setUri(api.absoluteUrl(result.mediaUrl))
                    .setMimeType(if (result.transport == "hls") MimeTypes.APPLICATION_M3U8 else MimeTypes.VIDEO_MP4).build()
                val factory = DefaultMediaSourceFactory(OnlineMediaDataSourceFactory(api, http, result.transport, result.mediaUrl))
                loadingSource = false
                player.setMediaSource(factory.createMediaSource(item), (position * 1000).toLong())
                player.prepare(); player.playWhenReady = !paused
            } catch (error: CancellationException) { throw error }
            catch (error: Exception) {
                if (!disposed && generation == ticket) {
                    descriptor = null
                    state = state.copy(loading = false, playing = false, error = onlineErrorMessage(error))
                }
            } finally {
                if (generation == ticket) loadingSource = false
                lease.releaseUnlessAdopted(descriptor?.sessionId)
            }
        }
    }

    private fun updatePosition() {
        if (disposed || !state.firstFrame || endedHandled) return
        val duration = player.duration.takeIf { it != C.TIME_UNSET && it > 0 }?.div(1000.0) ?: state.duration
        val position = player.currentPosition.coerceAtLeast(0) / 1000.0
        state = state.copy(position = if (duration > 0) position.coerceAtMost(duration) else position,
            duration = duration, buffered = player.bufferedPosition.coerceAtLeast(0) / 1000.0)
    }

    private fun snapshot(completed: Boolean = false): OnlineProgressSnapshot? {
        if (!state.firstFrame || !state.position.isFinite() || state.position < 0) return null
        return OnlineProgressSnapshot(state.position, state.duration.takeIf { it.isFinite() && it > 0 },
            paused = !state.playWhenReady, completed = completed)
    }
    private fun queueSave(completed: Boolean = false) { snapshot(completed)?.let { packets.trySend(writer to it) } }

    private fun finish() {
        updatePosition()
        val completed = onlineCompletion(state.firstFrame, true, state.position, state.duration)
        endedHandled = true
        state = state.copy(ended = true, playing = false, playWhenReady = false, loading = false,
            nextLoading = request.autoplay && request.identity.kind == "episode" && completed)
        queueSave(completed)
        player.pause(); player.stop(); http.dispatcher.cancelAll()
        descriptor?.let { old -> writes.launch { deleteSession(old.sessionId) } }
        descriptor = null
        if (!state.nextLoading) return
        val ticket = ++nextGeneration
        nextJob = scope.launch {
            try {
                val fresh = online.detail(request.identity.sourceId, request.externalId)
                val entry = fresh.nextEpisode(request.identity)
                if (disposed || nextGeneration != ticket) return@launch
                if (entry == null) {
                    state = state.copy(nextLoading = false, notice = "当前线路没有可连续播放的下一集，请手动选集。")
                    return@launch
                }
                val identity = entry.identity ?: return@launch
                val saved = online.playbackState(identity)
                if (disposed || nextGeneration != ticket) return@launch
                val progress = saved?.selectedProgress
                state = state.copy(nextLoading = false, next = OnlinePlaybackRequest(identity,
                    fresh.item.title + " · " + entry.label, request.externalId,
                    if (progress?.completed == false) progress.position else 0.0,
                    saved?.scopeToken.orEmpty(), progress?.writerRevision ?: 0, autoplay = true))
            } catch (error: CancellationException) { throw error }
            catch (error: Exception) {
                if (!disposed && nextGeneration == ticket) state = state.copy(nextLoading = false,
                    notice = "下一集未能加载，请手动选集。" + onlineErrorMessage(error))
            }
        }
    }

    fun cancelNext() {
        nextGeneration++; nextJob?.cancel(); nextJob = null
        if (!disposed) state = state.copy(nextLoading = false, next = null)
    }
    /** A queued Compose effect must revalidate cancellation and may hand off only once. */
    fun consumeNext(expected: OnlinePlaybackRequest): OnlinePlaybackRequest? {
        if (disposed || state.next !== expected) return null
        cancelNext()
        return expected
    }
    fun toggle() { setPlaying(!state.playWhenReady) }
    fun setPlaying(playing: Boolean) {
        if (disposed || state.error != null || endedHandled) return
        player.playWhenReady = playing
        state = state.copy(playWhenReady = playing)
        updatePosition(); queueSave()
    }
    fun seek(position: Double) {
        if (disposed || state.error != null || endedHandled || loadingSource || descriptor?.seekable != true
            || !position.isFinite() || state.duration <= 0) return
        player.seekTo((position.coerceIn(0.0, state.duration) * 1000).toLong())
        // The actual post-seek position is sampled rather than persisting a guessed target.
        scope.launch { delay(200); updatePosition(); queueSave() }
    }
    fun skip(amount: Double) { seek(state.position + amount) }
    fun setRate(rate: Float) {
        if (disposed || rate !in PLAYBACK_RATES) return
        player.setPlaybackSpeed(rate); state = state.copy(rate = rate)
    }
    fun setTrack(type: Int, option: OnlineTrackOption?) {
        if (disposed) return
        val parameters = player.trackSelectionParameters.buildUpon().clearOverridesOfType(type)
            .setTrackTypeDisabled(type, option == null)
        if (option != null) {
            val group = player.currentTracks.groups.getOrNull(option.group) ?: return
            if (group.type != type || option.track !in 0 until group.length || !group.isTrackSupported(option.track)) return
            parameters.setOverrideForType(TrackSelectionOverride(group.mediaTrackGroup, option.track))
        }
        player.trackSelectionParameters = parameters.build()
    }
    private fun updateTracks(tracks: Tracks) {
        fun options(type: Int) = tracks.groups.flatMapIndexed { groupIndex, group ->
            if (group.type != type) emptyList() else (0 until group.length).filter { group.isTrackSupported(it) }.map { index ->
                val format = group.getTrackFormat(index)
                OnlineTrackOption(groupIndex, index, format.label?.takeIf { it.isNotBlank() }
                    ?: format.language?.takeIf { it.isNotBlank() } ?: (if (type == C.TRACK_TYPE_AUDIO) "音轨" else "字幕") + " ${index + 1}",
                    group.isTrackSelected(index))
            }
        }
        state = state.copy(audios = options(C.TRACK_TYPE_AUDIO), subtitles = options(C.TRACK_TYPE_TEXT))
    }
    private fun displaySupportsDolby(): Boolean = Build.VERSION.SDK_INT >= 24 && runCatching {
        val display = (appContext.getSystemService(Context.DISPLAY_SERVICE) as? DisplayManager)?.getDisplay(Display.DEFAULT_DISPLAY)
        display != null && (Build.VERSION.SDK_INT < 26 || display.isHdr) &&
            Display.HdrCapabilities.HDR_TYPE_DOLBY_VISION in display.hdrCapabilities.supportedHdrTypes
    }.getOrDefault(false)
    private fun nativeDolbyDecoder(format: Format): Boolean = runCatching {
        MediaCodecUtil.getDecoderInfos(DolbyVisionCapabilities.MIME_TYPE, false, false).any {
            it.mimeType == DolbyVisionCapabilities.MIME_TYPE && it.hardwareAccelerated && !it.softwareOnly
                && it.isFormatSupported(appContext, format)
        }
    }.getOrDefault(false)
    private fun rejectTracks(message: String) {
        updatePosition(); queueSave()
        loadingSource = true
        player.pause(); player.stop(); http.dispatcher.cancelAll()
        descriptor?.let { old -> writes.launch { deleteSession(old.sessionId) } }; descriptor = null
        state = state.copy(loading = false, playing = false, error = message)
        loadingSource = false
    }
    private fun playbackError(error: PlaybackException) {
        val current = descriptor
        val now = System.currentTimeMillis() / 1000
        if (now - renewalWindow > 60) { renewalWindow = now; renewalAttempts = 0 }
        val status = generateSequence<Throwable>(error) { it.cause?.takeIf { next -> next !== it } }.take(12).mapNotNull {
            when (it) { is OnlineMediaResponseException -> it.status; is HttpDataSource.InvalidResponseCodeException -> it.responseCode; else -> null }
        }.firstOrNull()
        if (current != null && onlineCanRenew(status, state.firstFrame, current.expiresAt, now, renewalAttempts)) {
            renewalAttempts++
            updatePosition(); queueSave()
            begin(state.position, paused = !state.playWhenReady)
        } else {
            updatePosition(); queueSave()
            player.pause(); http.dispatcher.cancelAll()
            descriptor?.let { old -> writes.launch { deleteSession(old.sessionId) } }; descriptor = null
            state = state.copy(loading = false, playing = false,
                error = "当前在线版本播放失败（${error.errorCodeName}），请重试或选择其他线路、播放设备。")
        }
    }
    fun retry() {
        if (!disposed) begin(if (state.ended) 0.0 else state.position,
            paused = if (state.ended) false else !state.playWhenReady, freshWriter = true)
    }
    private suspend fun deleteSession(session: String) {
        sessions.release(session)
    }

    /** Stop local decoding/sound synchronously, then finish bounded writes independently of the route. */
    fun close(): Job? {
        if (disposed) return exit
        updatePosition(); queueSave(state.ended && onlineCompletion(state.firstFrame, true, state.position, state.duration))
        disposed = true; generation++; cancelNext()
        preparation?.cancel(); descriptor = null
        player.pause(); http.dispatcher.cancelAll()
        player.stop(); mediaSession.release(); player.release()
        scope.cancel(); packets.close()
        exit = writes.launch {
            try {
                supervisorScope {
                    launch { withTimeoutOrNull(8500) { saves.join() }; saves.cancel() }
                    launch { sessions.close() }
                }
            } finally { http.connectionPool.evictAll(); writes.cancel() }
        }
        return exit
    }
}
