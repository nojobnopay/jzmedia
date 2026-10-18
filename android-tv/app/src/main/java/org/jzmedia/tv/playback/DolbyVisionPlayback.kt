package org.jzmedia.tv.playback

import org.json.JSONObject

/** Older servers can still offer ordinary HDR conversion for P5. Never start that session. */
internal fun dolbyVisionPlaybackError(media: JSONObject, capabilities: JSONObject, decision: JSONObject): String? {
    if (!DolbyVisionCapabilities.isP5Source(media)) return null
    val formats = capabilities.optJSONArray("hdr_formats")
    val nativeDolbyVision = formats != null && (0 until formats.length()).any {
        formats.optString(it) == "dolby_vision"
    }
    val method = decision.optString("method")
    val preservesVideo = method == "direct" ||
        (method in setOf("remux", "audio_transcode") && decision.optJSONObject("plan")?.opt("vcopy") == true)
    return if (nativeDolbyVision && preservesVideo) null else
        "该杜比片源无法以当前设置原生播放。请使用自动或原画画质、文本字幕，或选择兼容版本。"
}

/** A P5 HEVC base layer has no HDR10 fallback. Its actual extracted track must remain Dolby. */
internal fun isDolbyVisionP5Format(mime: String?, codecs: String?): Boolean =
    mime == "video/dolby-vision" && codecs != null &&
        Regex("dv(?:he|h1)\\.05\\.(?:0[1-9]|1[0-3])").matches(codecs)
