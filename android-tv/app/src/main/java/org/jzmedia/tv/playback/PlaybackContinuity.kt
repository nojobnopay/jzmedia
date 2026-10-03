package org.jzmedia.tv.playback

import java.text.Normalizer
import java.util.Locale
import org.json.JSONObject

/** Only passed to the next episode of this viewing session; never stored globally. */
data class ContinuationPreferences(
    val rate: Float = 1f,
    val audio: MediaTrack? = null,
    val subtitlesEnabled: Boolean = false,
    val subtitle: MediaTrack? = null,
)

private fun normalizedTitle(value: String): String =
    Normalizer.normalize(value, Normalizer.Form.NFKC).trim().lowercase(Locale.ROOT).replace(Regex("\\s+"), " ")

private fun languageKey(value: String): String {
    val normalized = value.trim().lowercase(Locale.ROOT).replace('_', '-').substringBefore('-')
    return when (normalized) {
        "", "und", "null" -> ""
        "chi", "zho", "cmn" -> "zh"
        "eng" -> "en"
        "jpn" -> "ja"
        "kor" -> "ko"
        "fre", "fra" -> "fr"
        "ger", "deu" -> "de"
        "spa" -> "es"
        else -> normalized
    }
}

private fun commentary(track: MediaTrack): Boolean =
    Regex("commentary|comment|评论|導評|导评|解说|解說", RegexOption.IGNORE_CASE).containsMatchIn(track.title)

/** IDs and ordinal indexes belong to a single file, so never use them to match episodes. */
fun preferredTrack(tracks: List<MediaTrack>, preference: MediaTrack?): Int? {
    preference ?: return null
    val language = languageKey(preference.language)
    val title = normalizedTitle(preference.title)
    if (language.isEmpty() && title.isEmpty()) return null
    return tracks.filter {
        (if (language.isNotEmpty()) languageKey(it.language) == language else normalizedTitle(it.title) == title) &&
            it.forced == preference.forced && commentary(it) == commentary(preference)
    }.maxByOrNull {
        (if (title.isNotEmpty() && normalizedTitle(it.title) == title) 16 else 0) +
            (if (it.image == preference.image) 8 else 0) +
            (if (preference.channels > 0 && it.channels == preference.channels) 4 else 0) +
            (if (preference.codec.isNotBlank() && it.codec == preference.codec) 2 else 0)
    }?.index
}

data class ContinuationSelection(val audio: Int, val subtitle: Int?, val notice: String? = null)

fun continuationSelection(audios: List<MediaTrack>, subtitles: List<MediaTrack>, defaultAudio: Int,
                          defaultSubtitle: Int?, preference: ContinuationPreferences?): ContinuationSelection {
    if (preference == null) return ContinuationSelection(defaultAudio, defaultSubtitle)
    val notices = mutableListOf<String>()
    val audio = preferredTrack(audios, preference.audio) ?: defaultAudio.also {
        if (preference.audio != null && audios.isNotEmpty()) notices += "本集没有匹配的音轨，已使用默认音轨"
    }
    val subtitle = if (!preference.subtitlesEnabled) null else preferredTrack(subtitles, preference.subtitle).also {
        if (it == null) notices += "本集没有匹配的字幕，已关闭字幕"
    }
    return ContinuationSelection(audio, subtitle, notices.joinToString("；").ifEmpty { null })
}

/** Explicitly missing next episodes stay visible as an explanation, never as a play request. */
fun nextPlaybackRequest(next: JSONObject?): PlaybackRequest? {
    if (next == null || next.optLong("id") <= 0 || !next.optBoolean("exists", true) || next.optInt("missing") != 0) return null
    return PlaybackRequest("episode", next.getLong("id"),
        "第${next.optInt("season")}季 第${next.optInt("episode")}集 ${next.optString("title")}", false)
}

fun nextRequestWithPreferences(state: PlaybackState): PlaybackRequest? = state.next?.copy(continuation = ContinuationPreferences(
    rate = state.rate, audio = state.audios.firstOrNull { it.index == state.audio },
    subtitlesEnabled = state.subtitle != null, subtitle = state.subtitles.firstOrNull { it.index == state.subtitle },
))

/** Preparing an HLS timeline must not overwrite a pause requested while it loads. */
internal class PlaybackIntent {
    var wantsPlay: Boolean = true
        private set
    var preparing: Boolean = true
        private set
    val playerShouldPlay: Boolean get() = wantsPlay && !preparing

    fun request(play: Boolean) { wantsPlay = play }
    fun beginPreparation() { preparing = true }
    fun prepared() { preparing = false }
    fun observePlayer(play: Boolean) { if (!preparing) wantsPlay = play }
}

fun shouldCountDownNext(ended: Boolean, autoNext: Boolean, nextAvailable: Boolean,
                       menuOpen: Boolean, cancelled: Boolean, hasError: Boolean): Boolean =
    ended && autoNext && nextAvailable && !menuOpen && !cancelled && !hasError
