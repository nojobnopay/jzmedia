package org.jzmedia.tv.playback

import androidx.media3.common.Format
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.mediacodec.MediaCodecSelector
import org.junit.Assert.*
import org.junit.Test

@UnstableApi
class OnlineFormatDecoderSelectorTest {
    @Test fun aDolbyFormatNeverQueriesTheHevcFallbackButStillQueriesNativeDolby() {
        val queries = mutableListOf<String>()
        val platform = MediaCodecSelector { mime, _, _ -> queries.add(mime); emptyList() }
        val selector = onlineFormatDecoderSelector(platform, Format.Builder()
            .setSampleMimeType("video/dolby-vision").setCodecs("dvhe.05.06").build())
        assertTrue(selector.getDecoderInfos("video/hevc", false, false).isEmpty())
        assertTrue(queries.isEmpty())
        selector.getDecoderInfos("video/dolby-vision", false, false)
        assertEquals(listOf("video/dolby-vision"), queries)
    }
    @Test fun anUnselectedDolbyCandidateCannotDisableTheFollowingSdrVariant() {
        val queries = mutableListOf<String>()
        val platform = MediaCodecSelector { mime, _, _ -> queries.add(mime); emptyList() }
        onlineFormatDecoderSelector(platform, Format.Builder().setSampleMimeType("video/dolby-vision").build())
            .getDecoderInfos("video/dolby-vision", false, false)
        val sdr = onlineFormatDecoderSelector(platform, Format.Builder().setSampleMimeType("video/avc").build())
        assertSame(platform, sdr)
        sdr.getDecoderInfos("video/avc", false, false)
        assertEquals(listOf("video/dolby-vision", "video/avc"), queries)
    }
}
