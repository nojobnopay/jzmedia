package org.jzmedia.tv.playback

import android.content.Context
import android.os.Handler
import androidx.media3.common.Format
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.Renderer
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.audio.AudioTrackAudioOutputProvider
import androidx.media3.exoplayer.audio.DefaultAudioSink
import androidx.media3.exoplayer.audio.DefaultAudioTrackBufferSizeProvider
import androidx.media3.exoplayer.mediacodec.MediaCodecSelector
import androidx.media3.exoplayer.mediacodec.MediaCodecInfo
import androidx.media3.exoplayer.video.MediaCodecVideoRenderer
import androidx.media3.exoplayer.video.VideoRendererEventListener

/** Movie playback tolerates a larger PCM queue than interactive, low-latency audio. */
@UnstableApi
internal class PlaybackRenderersFactory(
    context: Context,
    private val strictDolbyFromMime: Boolean = false,
    private val nativeP5: () -> Boolean = { false },
) : DefaultRenderersFactory(context) {
    override fun buildVideoRenderers(
        context: Context, extensionRendererMode: Int, mediaCodecSelector: MediaCodecSelector,
        enableDecoderFallback: Boolean, eventHandler: Handler,
        eventListener: VideoRendererEventListener, allowedVideoJoiningTimeMs: Long,
        out: ArrayList<Renderer>,
    ) {
        val firstVideoRenderer = out.size
        val selector = MediaCodecSelector { mime, secure, tunneling ->
            val decoders = mediaCodecSelector.getDecoderInfos(mime, secure, tunneling)
            if (nativeP5() && mime.startsWith("video/")) {
                // The capability preflight and actual playback must use the same native path.
                if (mime == DolbyVisionCapabilities.MIME_TYPE) {
                    decoders.filter { it.mimeType == DolbyVisionCapabilities.MIME_TYPE &&
                        it.hardwareAccelerated && !it.softwareOnly }
                } else emptyList()
            } else decoders
        }
        if (strictDolbyFromMime) {
            out.add(OnlineMediaCodecVideoRenderer(MediaCodecVideoRenderer.Builder(context)
                .setCodecAdapterFactory(getCodecAdapterFactory()).setMediaCodecSelector(selector)
                .setAllowedJoiningTimeMs(allowedVideoJoiningTimeMs).setEnableDecoderFallback(enableDecoderFallback)
                .setEventHandler(eventHandler).setEventListener(eventListener)
                .setMaxDroppedFramesToNotify(DefaultRenderersFactory.MAX_DROPPED_VIDEO_FRAME_COUNT_TO_NOTIFY)))
        } else {
            super.buildVideoRenderers(context, extensionRendererMode, selector,
                enableDecoderFallback, eventHandler, eventListener, allowedVideoJoiningTimeMs, out)
        }
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

/** Dolby restrictions belong to one queried Format, not to the other HLS variants. */
@UnstableApi
internal fun onlineFormatDecoderSelector(selector: MediaCodecSelector, format: Format): MediaCodecSelector {
    if (format.sampleMimeType != DolbyVisionCapabilities.MIME_TYPE) return selector
    return MediaCodecSelector { mime, secure, tunneling ->
        if (mime != DolbyVisionCapabilities.MIME_TYPE) emptyList()
        else selector.getDecoderInfos(mime, secure, tunneling).filter {
            it.mimeType == DolbyVisionCapabilities.MIME_TYPE && it.hardwareAccelerated && !it.softwareOnly
        }
    }
}

@UnstableApi
private class OnlineMediaCodecVideoRenderer(builder: MediaCodecVideoRenderer.Builder) : MediaCodecVideoRenderer(builder) {
    override fun supportsFormat(mediaCodecSelector: MediaCodecSelector, format: Format): Int =
        super.supportsFormat(onlineFormatDecoderSelector(mediaCodecSelector, format), format)

    override fun getDecoderInfos(mediaCodecSelector: MediaCodecSelector, format: Format,
                                  requiresSecureDecoder: Boolean): List<MediaCodecInfo> =
        super.getDecoderInfos(onlineFormatDecoderSelector(mediaCodecSelector, format), format, requiresSecureDecoder)
}

@UnstableApi
internal fun pcmBufferSizeProvider(): DefaultAudioTrackBufferSizeProvider =
    DefaultAudioTrackBufferSizeProvider.Builder()
        // Observed decoder/output scheduling gaps exceed the 500 ms Media3 default.
        // 1.5 s gives headroom without changing network buffering or encoded passthrough.
        .setTargetPcmBufferDurationUs(1_500_000)
        .build()
