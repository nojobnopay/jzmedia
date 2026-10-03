package org.jzmedia.tv.ui

private val navigationPages = setOf("home", "movies", "shows", "collections", "search", "settings", "libraries")
private val navigationSources = setOf("home", "movies", "shows", "collections", "search")
private val detailSections = mapOf(
    "movie" to "movies",
    "show" to "shows",
    "season" to "shows",
    "episode" to "shows",
    "collection" to "collections",
    "actor-works" to "search",
)

/** Keep a detail page in the channel that opened it, including nested search results. */
fun navigationSection(routes: List<TvRoute>): String {
    val current = routes.lastOrNull()?.kind ?: return "home"
    if (current in navigationPages) return current
    val fallback = detailSections[current] ?: return "home"
    return routes.asReversed().firstOrNull { it.kind in navigationSources }?.kind ?: fallback
}
