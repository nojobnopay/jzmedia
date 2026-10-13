package org.jzmedia.tv.playback

import androidx.media3.common.C
import androidx.media3.common.Format
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.audio.DefaultAudioSink
import androidx.media3.exoplayer.audio.DefaultAudioTrackBufferSizeProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

@UnstableApi
class PlaybackAudioBufferTest {
    @Test
    fun pcmQueueCoversObservedSchedulingGapsAtDifferentFormatsAndSpeeds() {
        val provider = pcmBufferSizeProvider()
        for ((rate, frameSize) in listOf(48_000 to 4, 44_100 to 2, 96_000 to 16)) {
            for (speed in listOf(1.0, 2.0)) {
                val bytes = provider.getBufferSizeInBytes(
                    4096, C.ENCODING_PCM_16BIT, DefaultAudioSink.OUTPUT_MODE_PCM,
                    frameSize, rate, Format.NO_VALUE, speed,
                )
                val durationMs = bytes * 1000.0 / frameSize / rate / speed
                assertTrue("queue must cover 800 ms feed gaps with headroom", durationMs >= 1200)
                assertTrue("movie audio queue remains bounded", durationMs <= 2000)
                assertEquals(0, bytes % frameSize)
            }
        }
    }

    @Test
    fun platformMinimumAndEncodedOutputKeepMedia3Rules() {
        val provider = pcmBufferSizeProvider()
        val bytes = provider.getBufferSizeInBytes(
            400_003, C.ENCODING_PCM_16BIT, DefaultAudioSink.OUTPUT_MODE_PCM,
            4, 48_000, Format.NO_VALUE, 1.0,
        )
        assertTrue(bytes >= 400_003)
        assertEquals(0, bytes % 4)

        val defaults = DefaultAudioTrackBufferSizeProvider.Builder().build()
        for (mode in listOf(DefaultAudioSink.OUTPUT_MODE_PASSTHROUGH, DefaultAudioSink.OUTPUT_MODE_OFFLOAD)) {
            assertEquals(
                defaults.getBufferSizeInBytes(4096, C.ENCODING_AC3, mode, 1, 48_000, 640_000, 1.0),
                provider.getBufferSizeInBytes(4096, C.ENCODING_AC3, mode, 1, 48_000, 640_000, 1.0),
            )
        }
    }
}
