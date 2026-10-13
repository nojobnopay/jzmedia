package org.jzmedia.tv.playback

import android.content.Context
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.audio.AudioTrackAudioOutputProvider
import androidx.media3.exoplayer.audio.DefaultAudioSink
import androidx.media3.exoplayer.audio.DefaultAudioTrackBufferSizeProvider

/** Movie playback tolerates a larger PCM queue than interactive, low-latency audio. */
@UnstableApi
internal class PlaybackRenderersFactory(context: Context) : DefaultRenderersFactory(context) {
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
