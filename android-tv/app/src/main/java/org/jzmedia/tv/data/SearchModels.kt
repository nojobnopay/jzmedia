package org.jzmedia.tv.data

import org.json.JSONObject

const val SEARCH_PAGE_SIZE = 24
const val SEARCH_QUERY_LIMIT = 80
// 腾讯式全键盘顺序：A-Z 后跟 1-9、0 殿后（电话盘惯例）
val TV_SEARCH_KEYS = ('A'..'Z').map(Char::toString) + ('1'..'9').map(Char::toString) + "0"

/** 腾讯式全键盘：36 键固定 6x6（字母+数字不分家，无折叠）。 */
fun searchKeyboardRows(): List<List<String>> = TV_SEARCH_KEYS.chunked(6)

fun searchKind(value: String): String = when (value) {
    "movie", "movies" -> "movie"
    "show", "shows" -> "show"
    "collection", "collections" -> "collection"
    else -> "all"
}

fun appendSearchInput(query: String, text: String): String {
    val value = query + text.filterNot(Char::isISOControl)
    return value.substring(0, value.offsetByCodePoints(0, minOf(SEARCH_QUERY_LIMIT, value.codePointCount(0, value.length))))
}
fun deleteSearchInput(query: String): String = if (query.isEmpty()) "" else query.substring(0, query.offsetByCodePoints(query.length, -1))

fun tvSearchPath(query: String, kind: String, library: Long, page: Int): String = queryPath("/api/tv-client/search", linkedMapOf(
    "q" to appendSearchInput("", query).trim(), "kind" to searchKind(kind),
    "media_library" to library.takeIf { it > 0 }, "limit" to SEARCH_PAGE_SIZE,
    "offset" to page.coerceAtLeast(0).toLong() * SEARCH_PAGE_SIZE,
))

fun actorSearchPath(query: String, library: Long, page: Int): String = queryPath("/api/tv-client/actors", linkedMapOf(
    "q" to appendSearchInput("", query).trim(), "media_library" to library.takeIf { it > 0 },
    "limit" to SEARCH_PAGE_SIZE, "offset" to page.coerceAtLeast(0).toLong() * SEARCH_PAGE_SIZE,
))

fun actorWorksPath(actorKey: String, kind: String, library: Long, page: Int): String = queryPath("/api/tv-client/actor-works", linkedMapOf(
    "actor" to actorKey, "kind" to kind.takeIf { it == "movie" || it == "show" }.orEmpty().ifEmpty { "all" },
    "media_library" to library.takeIf { it > 0 }, "limit" to SEARCH_PAGE_SIZE,
    "offset" to page.coerceAtLeast(0).toLong() * SEARCH_PAGE_SIZE,
))

fun actorAvatarPath(row: JSONObject): String {
    val path = row.text("avatar_path")
    // Actor images use the same local poster contract. Never fetch an external avatar.
    if (path.isBlank() || path.startsWith("//") || path.contains(":") || path.contains('\\') ||
        path.split('/').any { it == "." || it == ".." }) return ""
    return posterPath(JSONObject().put("poster_path", path))
}

fun actorWorkSummary(row: JSONObject): String = "电影 ${row.optInt("movie_count").coerceAtLeast(0)} · 剧集 ${row.optInt("show_count").coerceAtLeast(0)}"
