package org.jzmedia.tv.ui

import org.jzmedia.tv.data.episodeTitle
import org.jzmedia.tv.playback.playbackTime
import org.json.JSONObject

internal const val EpisodeButtonMinimumWidth = 88
internal const val EpisodeButtonMaximumWidth = 100
internal const val EpisodeButtonGap = 12

internal fun episodeGridColumns(availableWidth: Int): Int =
    ((availableWidth + EpisodeButtonGap) / (EpisodeButtonMinimumWidth + EpisodeButtonGap)).coerceAtLeast(1)

internal data class EpisodeButtonModel(
    val id: Long,
    val number: String,
    val badge: String,
    val description: String,
    val continuing: Boolean,
    val watched: Boolean,
)

internal fun episodeButtonModel(row: JSONObject, showVersion: Boolean): EpisodeButtonModel {
    val start = row.optInt("episode")
    val end = row.optInt("episode_end")
    val number = start.toString().padStart(2, '0') + if (end > start) "–${end.toString().padStart(2, '0')}" else ""
    val watched = row.optInt("watched") == 1
    val offline = !row.optBoolean("exists", true) || row.optInt("missing") == 1
    val position = row.optJSONObject("progress")?.optDouble("position", 0.0)?.takeIf { it.isFinite() && it > 0 } ?: 0.0
    val continuing = !watched && !offline && position > 0
    val state = when {
        offline -> "离线"
        watched -> "已看"
        continuing -> "续播"
        else -> ""
    }
    val version = row.optInt("version", 1).coerceAtLeast(1)
    val badge = listOf(if (showVersion || version > 1) "V$version" else "", state).filter { it.isNotBlank() }.joinToString(" · ")
    val description = listOf(
        episodeTitle(row),
        if (showVersion || version > 1) "版本 $version" else "",
        if (offline) "文件离线" else "",
        if (watched) "已看" else if (position > 0) "已观看 ${playbackTime(position)}" else "未看",
    ).filter { it.isNotBlank() }.joinToString(" · ")
    return EpisodeButtonModel(row.optLong("id"), number, badge, description, continuing, watched)
}
