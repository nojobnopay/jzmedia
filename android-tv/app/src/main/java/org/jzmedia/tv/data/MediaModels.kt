package org.jzmedia.tv.data

import org.json.JSONArray
import org.json.JSONObject
import java.net.URLEncoder
import java.text.Normalizer
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
).filter { it.isNotBlank() }.joinToString(" · ")

/** 海报“已看”徽与网页一致：电影/分集看 watched==1；剧/季按全集看完。 */
fun mediaWatched(row: JSONObject): Boolean {
    val total = row.optInt("episode_count", row.optInt("total", 0))
    if (total > 0 || row.has("watched_count")) {
        val watched = row.optInt("watched_count")
        return total > 0 && watched >= total
    }
    return row.optInt("watched") == 1
}

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

/** 详情页背景：电影走独立背景端点（无 tmdb 不请求），剧/季/集用剧背景，合集无背景。 */
fun detailBackdropPath(kind: String, row: JSONObject, routeId: Long): String {
    return when (kind) {
        "movie" -> {
            val tmdb = row.optLong("tmdb_id", 0)
            if (tmdb > 0) "/api/movies/$routeId/backdrop" else ""
        }
        "show" -> backdropUrl(row.text("backdrop_path"))
        "season", "episode" -> backdropUrl(row.text("show_backdrop_path"))
        else -> ""
    }
}

private fun backdropUrl(stored: String): String {
    if (stored.isBlank()) return ""
    val relative = stored.trimStart('/').removePrefix("posters/")
    return "/posters/$relative"
}

/** 演职员头像：TMDB 路径走代理，本地相对路径走 /posters，'-' 表示确认无图。 */
fun castAvatarPath(p: JSONObject): String {
    val profile = p.text("profile_path").ifBlank { p.text("profile_tmdb_path") }
    if (profile.startsWith("/")) return "/api/tv/cast-avatar?path=" + URLEncoder.encode(profile, "UTF-8")
    val avatar = p.text("avatar")
    if (avatar.startsWith("/")) return "/api/tv/cast-avatar?path=" + URLEncoder.encode(avatar, "UTF-8")
    if (avatar.isNotBlank() && avatar != "-") {
        val relative = avatar.trimStart('/').removePrefix("posters/")
        return "/posters/$relative"
    }
    return ""
}

/** 角色名为贡献者自由文本：仅英文原语言展示，与网页一致。 */
fun castCharacter(p: JSONObject, originalLanguage: String): String {
    if (!originalLanguage.lowercase().startsWith("en")) return ""
    return p.text("character").ifBlank { p.text("character_name") }
}

/** 电影详情只展示演员（导演另行文字行），剧/季/集直接用 cast。 */
fun detailCastList(kind: String, row: JSONObject): List<JSONObject> {
    val raw = row.rows(if (kind == "movie") "persons" else "cast")
    if (kind == "movie") return raw.filter { it.text("role") == "actor" }
    return raw
}

/** 演职员卡点进演员作品页的 key，与服务端 tv_actors 索引同口径：
 * 有 TMDB id 用 `tmdb:{id}`，无 id 用 `name:{小写名}`（NFKC+空白收敛）。
 * 服务端用 Python casefold，TV 用 ROOT locale 小写，CJK/常见拉丁名一致，
 * 极个别特殊字符不一致时人物页走 404 提示，不崩。 */
fun castActorKey(p: JSONObject): String {
    val id = p.optLong("id", p.optLong("tmdb_id", 0))
    if (id > 0) return "tmdb:$id"
    val name = Normalizer.normalize(p.text("name"), Normalizer.Form.NFKC)
        .split("\\s+".toRegex()).filter { it.isNotEmpty() }.joinToString(" ")
    return "name:" + name.lowercase(Locale.ROOT)
}
