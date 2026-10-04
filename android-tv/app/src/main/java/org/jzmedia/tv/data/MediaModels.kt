package org.jzmedia.tv.data

import org.json.JSONArray
import org.json.JSONObject
import java.net.URLEncoder
import java.util.Locale

fun JSONObject.text(key: String, fallback: String = ""): String = if (isNull(key)) fallback else optString(key, fallback)
fun JSONArray?.objects(): List<JSONObject> = if (this == null) emptyList() else (0 until length()).mapNotNull { optJSONObject(it) }
fun JSONObject.rows(key: String = "items"): List<JSONObject> = optJSONArray(key).objects()
fun queryPath(path: String, values: Map<String, Any?>): String {
    val query = values.filterValues { it != null && it.toString().isNotEmpty() }.entries.joinToString("&") {
        URLEncoder.encode(it.key, "UTF-8") + "=" + URLEncoder.encode(it.value.toString(), "UTF-8")
    }
    return path + if (query.isEmpty()) "" else "?$query"
}

fun mediaTitle(row: JSONObject): String = row.text("title").ifBlank { row.text("name").ifBlank { row.text("file_path").substringAfterLast('/').ifBlank { "未命名影片" } } }

/** library_name is a video source, not the parent media library shown in navigation. */
fun mediaLibraryLabel(row: JSONObject): String = row.text("media_library_name").trim().ifBlank {
    row.optLong("media_library_id").takeIf { it > 0 }?.let { "媒体库 #$it" }.orEmpty()
}

fun mediaRatingLabel(row: JSONObject): String {
    // The server stores tmdb_rating. vote_average supports older fixture/API payloads.
    for ((field, source) in listOf("tmdb_rating" to "TMDB", "vote_average" to "TMDB", "douban_rating" to "豆瓣", "custom_rating" to "自评")) {
        val value = row.optDouble(field)
        if (value.isFinite() && value > 0 && value <= 10) return "$source ${String.format(Locale.ROOT, "%.1f", value)}"
    }
    return ""
}

fun mediaCardSubtitle(row: JSONObject): String = listOf(
    row.text("year").takeUnless { it == "0" }.orEmpty(),
    if (row.optInt("watched") == 1) "已看" else "",
).filter { it.isNotBlank() }.joinToString(" · ")

fun posterPath(row: JSONObject): String {
    val stored = sequenceOf("poster_path", "show_poster", "cover", "season_poster")
        .map { row.text(it) }.firstOrNull { it.isNotBlank() } ?: return ""
    // Movie paths commonly include posters/; TV paths are relative to that directory.
    val relative = stored.trimStart('/').removePrefix("posters/")
    return "/posters/$relative"
}
fun episodeTitle(row: JSONObject): String {
    val end = row.optInt("episode_end")
    val start = row.optInt("episode")
    val label = "S${row.optInt("season").toString().padStart(2, '0')} · E${start.toString().padStart(2, '0')}" + if (end > start) "–$end" else ""
    return "$label  ${mediaTitle(row)}"
}
