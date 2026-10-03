package org.jzmedia.tv.playback

import android.content.Context
import android.hardware.display.DisplayManager
import android.os.Build
import android.view.Display
import androidx.media3.common.Format
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.mediacodec.MediaCodecUtil
import org.json.JSONArray
import org.json.JSONObject

/** Decode capability only; never advertises Dolby/DTS passthrough based on a brand name. */
@UnstableApi
object NativeCapabilities {
    private val videoMimes = mapOf(
        "h264" to "video/avc", "h264_hi10p" to "video/avc", "hevc" to "video/hevc",
        "hevc10" to "video/hevc", "av1" to "video/av01", "vp9" to "video/x-vnd.on2.vp9",
        "mpeg2" to "video/mpeg2", "vc1" to "video/wvc1",
    )
    private val audioMimes = mapOf(
        "aac" to "audio/mp4a-latm", "mp3" to "audio/mpeg", "ac3" to "audio/ac3",
        "eac3" to "audio/eac3", "dts" to "audio/vnd.dts", "truehd" to "audio/true-hd",
        "flac" to "audio/flac", "opus" to "audio/opus", "vorbis" to "audio/vorbis", "pcm" to "audio/raw",
    )

    fun detect(context: Context, media: JSONObject? = null): JSONObject {
        fun supported(mime: String, codecs: String? = null, audio: JSONObject? = null, source: Boolean = false): Boolean = runCatching {
            val builder = Format.Builder().setSampleMimeType(mime).setCodecs(codecs)
            if (mime.startsWith("video/")) {
                builder.setWidth(if (source) media?.optInt("width", 1920)?.takeIf { it > 0 } ?: 1920 else 1920)
                    .setHeight(if (source) media?.optInt("height", 1080)?.takeIf { it > 0 } ?: 1080 else 1080)
                val fps = if (source) media?.optDouble("fps", 0.0) ?: 0.0 else 30.0
                if (fps.isFinite() && fps > 0) builder.setFrameRate(fps.toFloat())
            } else if (audio != null) {
                val channels = audio.optInt("channels", 0)
                val rate = audio.optInt("sample_rate", 0)
                if (channels > 0) builder.setChannelCount(channels)
                if (rate > 0) builder.setSampleRate(rate)
            }
            MediaCodecUtil.getDecoderInfos(mime, false, false).any { it.isFormatSupported(context, builder.build()) }
        }.getOrDefault(false)

        val video = JSONObject()
        videoMimes.forEach { (key, mime) ->
            val codec = when (key) {
                "h264_hi10p" -> "avc1.6E0028"
                "hevc10" -> "hvc1.2.4.L150.B0"
                "hevc" -> "hvc1.1.6.L120.B0"
                else -> null
            }
            video.put(key, supported(mime, codec))
        }
        val audio = JSONObject()
        audioMimes.forEach { (key, mime) -> audio.put(key, supported(mime)) }
        val probes = JSONObject()
        fun probeStrings(strings: JSONArray?, mime: String, track: JSONObject? = null) {
            if (strings == null) return
            for (i in 0 until strings.length().coerceAtMost(32)) {
                val value = strings.optString(i)
                val codec = Regex("codecs=[\"']([^\"']+)").find(value)?.groupValues?.get(1)
                if (value.isNotBlank()) probes.put(value, probes.optBoolean(value, true) && supported(mime, codec, track, source = true))
            }
        }
        if (media != null) {
            val videoMime = videoMimes[media.optString("vcodec")]
            if (videoMime != null) probeStrings(media.optJSONArray("vcaps"), videoMime)
            val tracks = media.optJSONArray("audio") ?: JSONArray()
            for (i in 0 until tracks.length()) {
                val track = tracks.optJSONObject(i) ?: continue
                val mime = audioMimes[track.optString("codec")] ?: continue
                probeStrings(track.optJSONArray("caps"), mime, track)
                // Source-channel limits belong to probes. Keep the generic AAC capability
                // for stereo server output even when a multichannel source is unsupported.
            }
        }
        val hdr = JSONArray()
        if (Build.VERSION.SDK_INT >= 24 && video.optBoolean("hevc10")) {
            val display = (context.getSystemService(Context.DISPLAY_SERVICE) as DisplayManager).getDisplay(Display.DEFAULT_DISPLAY)
            val supported = display?.hdrCapabilities?.supportedHdrTypes ?: intArrayOf()
            if (Display.HdrCapabilities.HDR_TYPE_HDR10 in supported) hdr.put("hdr10")
            if (Display.HdrCapabilities.HDR_TYPE_HLG in supported) hdr.put("hlg")
        }
        return JSONObject()
            .put("video", video).put("audio", audio).put("probes", probes)
            .put("containers", JSONObject().apply {
                listOf("mp4", "mov", "m4v", "mkv", "webm", "mpegts").forEach { put(it, true) }
            })
            .put("hls", true).put("audio_track_selection", true)
            .put("hls_audio", JSONObject().put("aac", true).put("mp3", true))
            .put("hdr_formats", hdr).put("subtitles", JSONObject().put("webvtt", true))
    }
}

/** Lower the source video requirement without forgetting negative source audio probes. */
fun compatibleCapabilities(original: JSONObject, media: JSONObject): JSONObject {
    val result = JSONObject(original.toString())
    val videos = result.optJSONObject("video") ?: JSONObject()
    videos.keys().asSequence().toList().filter { it != "h264" }.forEach { videos.put(it, false) }
    val probes = result.optJSONObject("probes") ?: JSONObject()
    val sourceProbes = media.optJSONArray("vcaps") ?: JSONArray()
    for (i in 0 until sourceProbes.length()) probes.put(sourceProbes.getString(i), false)
    return result.put("video", videos).put("probes", probes).put("hdr_formats", JSONArray())
}
