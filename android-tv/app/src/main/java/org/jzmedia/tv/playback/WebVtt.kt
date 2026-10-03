package org.jzmedia.tv.playback

data class SubtitleCue(val start: Double, val end: Double, val text: String)

/** Plain-text rendering deliberately excludes ASS effects and WebVTT HTML layout. */
fun parseWebVtt(source: String): List<SubtitleCue> {
    val timestamp = Regex("^(?:(\\d+):)?(\\d{2}):(\\d{2})[.,](\\d{3})$")
    fun seconds(raw: String): Double? {
        val match = timestamp.matchEntire(raw) ?: return null
        val (hours, minutes, secs, millis) = match.destructured
        if (minutes.toInt() >= 60 || secs.toInt() >= 60) return null
        return (hours.toLongOrNull() ?: 0) * 3600.0 + minutes.toInt() * 60 + secs.toInt() + millis.toInt() / 1000.0
    }
    val lines = source.removePrefix("\uFEFF").replace("\r\n", "\n").replace('\r', '\n').split('\n')
    val result = mutableListOf<SubtitleCue>()
    var index = 0
    while (index < lines.size) {
        val line = lines[index++].trim()
        if (line == "NOTE" || line.startsWith("NOTE ") || line == "STYLE" || line == "REGION") {
            while (index < lines.size && lines[index].isNotBlank()) index++
            continue
        }
        val timing = line.split(Regex("\\s+-->\\s+"), limit = 2)
        if (timing.size != 2) continue
        val start = seconds(timing[0]) ?: continue
        val end = seconds(timing[1].substringBefore(' ')) ?: continue
        val body = mutableListOf<String>()
        while (index < lines.size && lines[index].isNotBlank()) body += lines[index++]
        val text = body.joinToString("\n")
            .replace(Regex("<br\\s*/?>", RegexOption.IGNORE_CASE), "\n")
            .replace(Regex("<[^>]*>"), "")
            .replace("&lt;", "<").replace("&gt;", ">")
            .replace("&nbsp;", " ").replace("&quot;", "\"").replace("&apos;", "'").replace("&amp;", "&")
            .trim()
        if (end > start && text.isNotEmpty()) result += SubtitleCue(start, end, text)
    }
    return result.sortedBy { it.start }
}

class SubtitleTimeline(private val cues: List<SubtitleCue>) {
    private val maxEnd = DoubleArray(cues.size).apply {
        cues.forEachIndexed { i, cue -> this[i] = maxOf(cue.end, if (i == 0) 0.0 else this[i - 1]) }
    }

    fun textAt(sourceSeconds: Double): String {
        var low = 0
        var high = cues.size
        while (low < high) {
            val mid = (low + high) / 2
            if (cues[mid].start <= sourceSeconds) low = mid + 1 else high = mid
        }
        val active = mutableListOf<String>()
        var index = low - 1
        while (index >= 0 && maxEnd[index] > sourceSeconds) {
            if (cues[index].end > sourceSeconds) active += cues[index].text
            index--
        }
        return active.asReversed().distinct().joinToString("\n")
    }
}
