package org.jzmedia.tv.playback

import androidx.media3.common.util.ParsableByteArray
import androidx.media3.common.util.UnstableApi
import androidx.media3.container.DolbyVisionConfig
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Device test for the locked Media3 parser used for Matroska dvcC/dvvC configuration.
 * This is a configuration parser check, not an extractor, decoder or display test.
 */
@UnstableApi
class DolbyVisionContainerConfigTest {
    @Test fun media3RecognizesProfileFiveAndKeepsTheContainerConfigurationBytes() {
        // Synthetic dvcC: version 1.0, profile 5, level 6, RPU + base layer,
        // no enhancement layer and no backwards-compatible base colour signal.
        val bytes = configuration(profile = 5, level = 6)
        val original = bytes.copyOf()
        val input = ParsableByteArray(bytes)

        val config = DolbyVisionConfig.parse(input)

        assertNotNull(config)
        assertEquals(5, config!!.profile)
        assertEquals(6, config.level)
        assertEquals("dvhe.05.06", config.codecs)
        assertArrayEquals(original, bytes)
    }

    @Test fun media3RetainsTheExplicitLevelInsteadOfInferringItFromPictureDimensions() {
        val config = DolbyVisionConfig.parse(ParsableByteArray(configuration(profile = 5, level = 9)))

        assertNotNull(config)
        assertEquals(9, config!!.level)
        assertEquals("dvhe.05.09", config.codecs)
    }

    @Test fun unsupportedConfigurationDoesNotProduceADolbyVisionCodecString() {
        assertNull(DolbyVisionConfig.parse(ParsableByteArray(configuration(profile = 3, level = 6))))
    }

    private fun configuration(profile: Int, level: Int): ByteArray = ByteArray(24).apply {
        this[0] = 1
        this[1] = 0
        this[2] = ((profile shl 1) or (level shr 5)).toByte()
        this[3] = (((level and 31) shl 3) or 0b101).toByte()
    }
}
