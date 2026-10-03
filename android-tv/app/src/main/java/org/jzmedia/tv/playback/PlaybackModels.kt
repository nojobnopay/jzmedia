package org.jzmedia.tv.playback

data class PlaybackRequest(val kind: String, val id: Long, val title: String, val resume: Boolean = true)

data class MediaTrack(val index: Int, val label: String, val image: Boolean = false, val ffIndex: Int = -1)

/** Extractor IDs are container-specific (Matroska TrackNumber is not ffprobe ff_index). */
data class AudioCandidate(val id: String?, val label: String?, val supported: Boolean)

fun audioCandidateIndex(candidates: List<AudioCandidate>, selected: Int, sourceCount: Int, direct: Boolean, singleAudio: Boolean): Int? {
    if (selected !in 0 until sourceCount) return null
    val index = when {
        singleAudio -> if (candidates.size == 1) 0 else -1
        direct -> if (candidates.size == sourceCount) selected else -1
        else -> candidates.indexOfFirst { it.label == "audio${selected + 1}" }.takeIf { it >= 0 }
            ?: if (candidates.size == sourceCount.coerceAtMost(8)) selected else -1
    }
    return index.takeIf { candidates.getOrNull(it)?.supported == true }
}

data class PlaybackState(
    val loading: Boolean = true,
    val playing: Boolean = false,
    val ended: Boolean = false,
    val error: String? = null,
    val notice: String? = null,
    val position: Double = 0.0,
    val duration: Double = 0.0,
    val buffered: Double = 0.0,
    val method: String = "",
    val output: String = "",
    val quality: String = "auto",
    val audio: Int = 0,
    val subtitle: Int? = null,
    val audios: List<MediaTrack> = emptyList(),
    val subtitles: List<MediaTrack> = emptyList(),
    val rate: Float = 1f,
    val subtitleText: String = "",
    val subtitleDelay: Double = 0.0,
    val subtitleSize: Int = 1,
    val autoNext: Boolean = true,
    val next: PlaybackRequest? = null,
)

val PLAYBACK_RATES = listOf(0.5f, 0.75f, 1f, 1.25f, 1.5f, 2f)

fun playbackTime(seconds: Double): String {
    val total = if (seconds.isFinite()) seconds.toLong().coerceAtLeast(0) else 0
    return if (total >= 3600) "%d:%02d:%02d".format(total / 3600, total / 60 % 60, total % 60)
    else "%d:%02d".format(total / 60, total % 60)
}

/** Server HLS zero is mediaStart; initialTime and player position are stream-relative. */
fun sourcePosition(playerMillis: Long, mediaStart: Double, duration: Double): Double {
    val value = mediaStart + playerMillis.coerceAtLeast(0) / 1000.0
    return if (duration > 0) value.coerceIn(0.0, duration) else value.coerceAtLeast(0.0)
}

fun shouldMarkWatched(position: Double, duration: Double): Boolean {
    val remaining = duration - position
    return duration > 0 && remaining > 0 && (remaining / duration < 0.05 || remaining < 300)
}
