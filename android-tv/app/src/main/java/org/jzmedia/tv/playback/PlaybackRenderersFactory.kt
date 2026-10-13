package org.jzmedia.tv.playback

import android.content.Context
import android.os.Handler
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.Renderer
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.audio.AudioTrackAudioOutputProvider
import androidx.media3.exoplayer.audio.DefaultAudioSink
import androidx.media3.exoplayer.audio.DefaultAudioTrackBufferSizeProvider
import androidx.media3.exoplayer.mediacodec.MediaCodecSelector
import androidx.media3.exoplayer.video.MediaCodecVideoRenderer
import androidx.media3.exoplayer.video.VideoRendererEventListener

/** Movie playback tolerates a larger PCM queue than interactive, low-latency audio. */
@UnstableApi
internal class PlaybackRenderersFactory(context: Context) : DefaultRenderersFactory(context) {
    override fun buildVideoRenderers(
        context: Context, extensionRendererMode: Int, mediaCodecSelector: MediaCodecSelector,
        enableDecoderFallback: Boolean, eventHandler: Handler,
        eventListener: VideoRendererEventListener, allowedVideoJoiningTimeMs: Long,
        out: ArrayList<Renderer>,
    ) {
        val firstVideoRenderer = out.size
        super.buildVideoRenderers(context, extensionRendererMode, mediaCodecSelector,
            enableDecoderFallback, eventHandler, eventListener, allowedVideoJoiningTimeMs, out)
        // Video and audio share the playback thread. A slow software decoder / surface must
        // yield between batches instead of draining video until the audio queue runs dry.
        // This bounds a batch, not an individual platform MediaCodec call.
        out.drop(firstVideoRenderer).filterIsInstance<MediaCodecVideoRenderer>().forEach {
            it.setRenderTimeLimitMs(10)
        }
    }

    override fun buildAudioSink(
        context: Context, enableFloatOutput: Boolean, enableAudioOutputPlaybackParams: Boolean,
    ): AudioSink = DefaultAudioSink.Builder(context)
        .setEnableFloatOutput(enableFloatOutput)
        .setEnableAudioOutputPlaybackParameters(enableAudioOutputPlaybackParams)
        .setAudioOutputProvider(AudioTrackAudioOutputProvider.Builder(context)
            .setAudioTrackBufferSizeProvider(pcmBufferSizeProvider())
            .build())
        .build()
}

@UnstableApi
internal fun pcmBufferSizeProvider(): DefaultAudioTrackBufferSizeProvider =
    DefaultAudioTrackBufferSizeProvider.Builder()
        // Observed decoder/output scheduling gaps exceed the 500 ms Media3 default.
        // 1.5 s gives headroom without changing network buffering or encoded passthrough.
        .setTargetPcmBufferDurationUs(1_500_000)
        .build()
