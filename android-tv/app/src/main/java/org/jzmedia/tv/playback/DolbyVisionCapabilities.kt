package org.jzmedia.tv.playback

import org.json.JSONArray
import org.json.JSONObject

/** A source-specific Dolby Vision probe, never a general HEVC capability. */
internal data class DolbyVisionP5Probe(
    val width: Int,
    val height: Int,
    val frameRate: Double,
    val level: Int,
) {
    val codecs: String get() = "dvhe.05.${level.toString().padStart(2, '0')}"
}

internal object DolbyVisionCapabilities {
    const val MIME_TYPE = "video/dolby-vision"

    private data class Level(val id: Int, val maxWidth: Int, val maxPixelRate: Long)

    // Dolby's level limits are pixel rate and decoded width, not the example
    // resolution/frame-rate pairs. Use the same table when an older server has
    // profile/geometry but no dv_level; the decoder still checks the actual size.
    // https://ott.dolby.com/OnDelKits/Dolby_Vision_Online_Delivery_Kit/v1/Documentation/Specs/Visio_Profiles/help_files/topics/c_dovi_levels.html
    private val levels = listOf(
        Level(1, 1280, 22_118_400), Level(2, 1280, 27_648_000),
        Level(3, 1920, 49_766_400), Level(4, 2560, 62_208_000),
        Level(5, 3840, 124_416_000), Level(6, 3840, 199_065_600),
        Level(7, 3840, 248_832_000), Level(8, 3840, 398_131_200),
        Level(9, 3840, 497_664_000), Level(10, 3840, 995_328_000),
        Level(11, 7680, 995_328_000), Level(12, 7680, 1_990_656_000),
        Level(13, 7680, 3_981_312_000),
    )

    fun isP5Source(media: JSONObject?): Boolean = media?.integer("dv_profile") == 5

    fun probe(media: JSONObject?): DolbyVisionP5Probe? {
        if (media == null || !isP5Source(media)
            || media.optString("vcodec") != "hevc"
            || media.integer("bit_depth") != 10
            || media.integer("dv_bl_compat") != 0
        ) return null

        // Profile 5 is single-layer BL+RPU. Older server responses do not carry
        // these flags; an explicitly contradictory record must never be ignored.
        if (!media.matchesOptionalFlag("dv_rpu_present", true)
            || !media.matchesOptionalFlag("dv_bl_present", true)
            || !media.matchesOptionalFlag("dv_el_present", false)
        ) return null

        val width = media.integer("width")?.takeIf { it > 0 } ?: return null
        val height = media.integer("height")?.takeIf { it > 0 } ?: return null
        val fps = media.number("fps")?.takeIf { it > 0 } ?: return null
        val pixelRate = width.toDouble() * height * fps
        fun Level.fits() = width <= maxWidth && pixelRate <= maxPixelRate
        val level = if (media.has("dv_level") && !media.isNull("dv_level")) {
            val declared = media.integer("dv_level") ?: return null
            levels.firstOrNull { it.id == declared && it.fits() } ?: return null
        } else {
            levels.firstOrNull { it.fits() } ?: return null
        }
        return DolbyVisionP5Probe(width, height, fps, level.id)
    }

    /** Both the display and a decoder for this exact source must qualify. */
    fun supportsP5(
        media: JSONObject?,
        displaySupportsDolbyVision: Boolean,
        supportsDecoder: (DolbyVisionP5Probe) -> Boolean,
    ): Boolean {
        if (!displaySupportsDolbyVision) return false
        val probe = probe(media) ?: return false
        return runCatching { supportsDecoder(probe) }.getOrDefault(false)
    }

    fun applySourceVideoSupport(media: JSONObject?, video: JSONObject, probes: JSONObject, supported: Boolean) {
        if (!isP5Source(media)) return
        video.put("hevc10", supported)
        val sourceProbes = media?.optJSONArray("vcaps") ?: JSONArray()
        for (i in 0 until sourceProbes.length().coerceAtMost(32)) {
            val value = sourceProbes.optString(i)
            if (value.isNotBlank()) probes.put(value, supported)
        }
    }

    private fun JSONObject.number(key: String): Double? = when (val value = opt(key)) {
        is Number -> value.toDouble()
        is String -> value.toDoubleOrNull()
        else -> null
    }?.takeIf { it.isFinite() }

    private fun JSONObject.integer(key: String): Int? = number(key)?.takeIf {
        it >= Int.MIN_VALUE && it <= Int.MAX_VALUE && it % 1.0 == 0.0
    }?.toInt()

    private fun JSONObject.matchesOptionalFlag(key: String, expected: Boolean): Boolean {
        if (!has(key) || isNull(key)) return true
        val actual = when (val value = opt(key)) {
            is Boolean -> value
            else -> when (integer(key)) { 0 -> false; 1 -> true; else -> return false }
        }
        return actual == expected
    }
}
