package org.jzmedia.tv.ui

import org.jzmedia.tv.data.JzApi

internal val JzApi.canFindWithAi: Boolean get() = supportsAiDiscovery && viewing?.mode != "children"

internal fun discoveryRoute(searchKind: String = "all") = TvRoute("discovery", title = when (searchKind) {
    "movie" -> "movie"
    "show" -> "tv"
    else -> "all"
})

private val navigationPages = setOf("home", "movies", "shows", "collections", "online", "search", "settings", "libraries")
private val navigationSources = setOf("home", "movies", "shows", "collections", "online", "search")
private val detailSections = mapOf(
    "movie" to "movies",
    "show" to "shows",
    "season" to "shows",
    "episode" to "shows",
    "collection" to "collections",
    "actor-works" to "search",
    "discovery" to "search",
)

/** Keep a detail page in the channel that opened it, including nested search results. */
fun navigationSection(routes: List<TvRoute>): String {
    val current = routes.lastOrNull()?.kind ?: return "home"
    if (current in navigationPages) return current
    val fallback = detailSections[current] ?: return "home"
    return routes.asReversed().firstOrNull { it.kind in navigationSources }?.kind ?: fallback
}
