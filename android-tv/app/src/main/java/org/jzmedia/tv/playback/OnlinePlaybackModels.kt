package org.jzmedia.tv.playback

import org.jzmedia.tv.data.OnlineIdentity

/** Everything needed to open one exact online version; never a local media ID or upstream URL. */
data class OnlinePlaybackRequest(val identity: OnlineIdentity, val title: String, val externalId: String,
                                 val position: Double = 0.0, val scopeToken: String,
                                 val writerRevision: Long = 0, val autoplay: Boolean = false) {
    init {
        require(position.isFinite() && position >= 0)
        require(writerRevision >= 0)
        require(Regex("[A-Za-z0-9_-]{1,128}").matches(externalId))
        require(scopeToken.isEmpty() || Regex("[0-9a-f]{64}").matches(scopeToken))
    }
}

data class OnlineTrackOption(val group: Int, val track: Int, val label: String, val selected: Boolean)

data class OnlinePlaybackState(val loading: Boolean = true, val playing: Boolean = false,
                               val playWhenReady: Boolean = true, val firstFrame: Boolean = false,
                               val ended: Boolean = false, val error: String? = null, val notice: String? = null,
                               val position: Double = 0.0, val duration: Double = 0.0, val buffered: Double = 0.0,
                               val seekable: Boolean = false,
                               val rate: Float = 1f, val transport: String = "",
                               val audios: List<OnlineTrackOption> = emptyList(),
                               val subtitles: List<OnlineTrackOption> = emptyList(),
                               val nextLoading: Boolean = false, val next: OnlinePlaybackRequest? = null)

/** A native ended event plus a decoded frame is needed; high percentages never complete online progress. */
internal fun onlineCompletion(firstFrame: Boolean, ended: Boolean, position: Double, duration: Double): Boolean =
    firstFrame && ended && position.isFinite() && duration.isFinite() && duration > 0
        && position >= 0 && kotlin.math.abs(position - duration) <= 1

internal fun onlineCanRenew(status: Int?, firstFrame: Boolean, expiresAt: Long, nowSeconds: Long,
                             previousAttempts: Int): Boolean =
    status in listOf(404, 410) && (firstFrame || nowSeconds >= expiresAt) && previousAttempts < 2

internal fun onlineVideoTrackError(mime: String?, codecs: String?, supported: Boolean,
                                    displayDolby: Boolean, nativeDolby: Boolean): String? {
    if (mime == null || !mime.startsWith("video/") || !supported)
        return "当前电视无法解码此视频轨，请选择其他线路或播放设备。"
    val p5 = Regex("dv(?:he|h1)\\.05\\.").containsMatchIn(codecs.orEmpty())
    if (p5 && !isDolbyVisionP5Format(mime, codecs))
        return "该杜比视频轨无法保持原生格式，请选择其他线路或播放设备。"
    if (mime == DolbyVisionCapabilities.MIME_TYPE && (codecs.isNullOrBlank() || !displayDolby || !nativeDolby))
        return "当前电视无法确认此杜比视频的原生显示与硬件解码支持，请选择其他线路或播放设备。"
    return null
}

internal data class OnlineProgressSnapshot(val position: Double, val duration: Double?,
                                           val paused: Boolean, val completed: Boolean)
