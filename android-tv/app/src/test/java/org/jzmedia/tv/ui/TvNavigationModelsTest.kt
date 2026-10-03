package org.jzmedia.tv.ui

import org.junit.Assert.assertEquals
import org.junit.Test

class TvNavigationModelsTest {
    private fun routes(vararg kinds: String) = kinds.map { TvRoute(it) }

    @Test fun currentNavigationPagesSelectTheirOwnSection() {
        for (kind in listOf("home", "movies", "shows", "collections", "search", "settings", "libraries")) {
            assertEquals(kind, navigationSection(routes("search", "actor-works", kind)))
        }
    }

    @Test fun nestedSearchResultsKeepTheSearchSection() {
        assertEquals("search", navigationSection(routes("home", "search", "movie")))
        assertEquals("search", navigationSection(routes("search", "collection", "movie")))
        assertEquals("search", navigationSection(routes("search", "actor-works", "show", "season", "episode")))
    }

    @Test fun detailsOpenedFromHomeKeepTheHomeSection() {
        for (kind in listOf("movie", "show", "season", "episode", "collection", "actor-works")) {
            assertEquals("home", navigationSection(routes("home", kind)))
        }
    }

    @Test fun detailsWithoutAChannelUseTheirMediaType() {
        val expected = mapOf(
            "movie" to "movies", "show" to "shows", "season" to "shows",
            "episode" to "shows", "collection" to "collections", "actor-works" to "search",
        )
        for ((kind, section) in expected) {
            assertEquals(section, navigationSection(routes(kind)))
            assertEquals(section, navigationSection(routes("settings", "libraries", kind)))
        }
    }

    @Test fun theMostRecentChannelWins() {
        assertEquals("movies", navigationSection(routes("home", "search", "movies", "movie")))
        assertEquals("shows", navigationSection(routes("movies", "shows", "show", "season")))
        assertEquals("collections", navigationSection(routes("search", "collections", "collection", "movie")))
    }

    @Test fun oldSettingsAndLibrariesDoNotReplaceTheDetailSource() {
        assertEquals("search", navigationSection(routes("search", "settings", "libraries", "actor-works", "movie")))
        assertEquals("home", navigationSection(routes("home", "libraries", "settings", "episode")))
    }

    @Test fun emptyAndUnknownRoutesFallBackToHome() {
        assertEquals("home", navigationSection(emptyList()))
        assertEquals("home", navigationSection(routes("unknown")))
        assertEquals("home", navigationSection(routes("root")))
        assertEquals("home", navigationSection(routes("search", "unknown")))
    }
}
