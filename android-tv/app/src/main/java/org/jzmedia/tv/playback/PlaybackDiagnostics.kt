package org.jzmedia.tv.playback

import android.util.Log
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.analytics.AnalyticsListener
import androidx.media3.exoplayer.analytics.AnalyticsListener.EventTime

/** Debug builds only: distinguish source starvation from output stalls without logging media URLs. */
@UnstableApi
internal class PlaybackDiagnostics : AnalyticsListener {
    override fun onPlaybackStateChanged(eventTime: EventTime, state: Int) {
        val name = when (state) {
            Player.STATE_IDLE -> "idle"
            Player.STATE_BUFFERING -> "buffering"
            Player.STATE_READY -> "ready"
            Player.STATE_ENDED -> "ended"
            else -> "unknown"
        }
        log(eventTime, "state=$name")
    }

    override fun onAudioUnderrun(
        eventTime: EventTime, bufferSize: Int, bufferSizeMs: Long, elapsedSinceLastFeedMs: Long,
    ) {
        log(eventTime, "audioUnderrun bufferBytes=$bufferSize bufferMs=$bufferSizeMs feedGapMs=$elapsedSinceLastFeedMs")
    }

    override fun onVideoDecoderInitialized(
        eventTime: EventTime, decoderName: String, initializedTimestampMs: Long, initializationDurationMs: Long,
    ) {
        log(eventTime, "videoDecoder=$decoderName initMs=$initializationDurationMs")
    }

    override fun onAudioDecoderInitialized(
        eventTime: EventTime, decoderName: String, initializedTimestampMs: Long, initializationDurationMs: Long,
    ) {
        log(eventTime, "audioDecoder=$decoderName initMs=$initializationDurationMs")
    }

    override fun onDroppedVideoFrames(eventTime: EventTime, droppedFrames: Int, elapsedMs: Long) {
        log(eventTime, "droppedFrames=$droppedFrames elapsedMs=$elapsedMs")
    }

    private fun log(eventTime: EventTime, event: String) {
        Log.d("JzPlayback", "$event positionMs=${eventTime.currentPlaybackPositionMs} bufferedMs=${eventTime.totalBufferedDurationMs}")
    }
}
