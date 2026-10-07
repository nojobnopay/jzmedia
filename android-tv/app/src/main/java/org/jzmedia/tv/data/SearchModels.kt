package org.jzmedia.tv.data

import org.json.JSONObject

const val SEARCH_PAGE_SIZE = 24
const val SEARCH_QUERY_LIMIT = 80
val TV_SEARCH_KEYS = ('A'..'Z').map(Char::toString) + ('0'..'9').map(Char::toString)

/** 键盘默认只放 A-Z（5 行）；数字使用极少，收进“数字”开关，展开后 36 键 6 行。 */
fun searchKeyboardKeys(showDigits: Boolean): List<String> =
    if (showDigits) TV_SEARCH_KEYS else TV_SEARCH_KEYS.take(26)

/** 分页常驻左列键盘下方；无翻页时不占位（右列结果区不再放分页）。 */
fun showSearchPagination(page: Int, hasMore: Boolean): Boolean = page > 0 || hasMore

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
