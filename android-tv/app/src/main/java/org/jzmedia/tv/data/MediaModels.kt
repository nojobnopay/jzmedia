package org.jzmedia.tv.data

import org.json.JSONArray
import org.json.JSONObject
import java.net.URLEncoder

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
fun posterPath(row: JSONObject): String = sequenceOf("poster_path", "show_poster", "cover", "season_poster")
    .map { row.text(it) }.firstOrNull { it.isNotBlank() }.orEmpty().let { if (it.isBlank()) "" else "/" + it.trimStart('/') }
fun episodeTitle(row: JSONObject): String {
    val end = row.optInt("episode_end")
    val start = row.optInt("episode")
    val label = "S${row.optInt("season").toString().padStart(2, '0')} · E${start.toString().padStart(2, '0')}" + if (end > start) "–$end" else ""
    return "$label  ${mediaTitle(row)}"
}
