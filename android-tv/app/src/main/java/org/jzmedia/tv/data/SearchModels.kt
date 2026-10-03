package org.jzmedia.tv.data

const val SEARCH_PAGE_SIZE = 24
const val SEARCH_QUERY_LIMIT = 80
val TV_SEARCH_KEYS = ('A'..'Z').map(Char::toString) + ('0'..'9').map(Char::toString)

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
