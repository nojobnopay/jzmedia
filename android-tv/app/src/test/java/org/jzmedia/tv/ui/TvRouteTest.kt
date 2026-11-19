package org.jzmedia.tv.ui

import org.junit.Assert.*
import org.junit.Test

class TvRouteTest {
    @Test fun discoveryScopesPreserveTheSelectedMovieOrTvWall() {
        assertEquals("movie", discoveryRoute("movie").title)
        assertEquals("tv", discoveryRoute("show").title)
        assertEquals("all", discoveryRoute("collection").title)
        assertEquals("all", discoveryRoute().title)
    }

    @Test fun matchedMovieEditionSurvivesNavigationAndCannotSilentlyFallBack() {
        val route = TvRoute("movie", id = 42, versionId = 91)
        assertEquals(route, TvRoute.decode(route.encode()))
        assertEquals(91L, detailMovieVersion(route, listOf(42L, 91L), 91L))
        assertNull(detailMovieVersion(route, listOf(42L), 91L))
        assertNull(detailMovieVersion(route, emptyList(), 91L))
        val usual = TvRoute("movie", id = 42)
        assertEquals(42L, detailMovieVersion(usual, listOf(42L, 91L), 0L))
        assertEquals(42L, detailMovieVersion(usual, emptyList(), 0L))
    }

    @Test fun privateNavigationNeverPersistsTitlesActorsOrMediaIdentifiers() {
        val routes = listOf(TvRoute("search", title = "Private query"),
            TvRoute("actor-works", title = "Private actor", actorKey = "tmdb:99"),
            TvRoute("movie", id = 42, title = "Private movie"))
        val restored = savedViewingRoutes(routes, "private").map(TvRoute::decode)
        assertEquals(listOf(TvRoute("home", key = "root:home")), restored)
        assertEquals(routes, savedViewingRoutes(routes, "normal").map(TvRoute::decode))
        assertEquals(routes, savedViewingRoutes(routes, "children").map(TvRoute::decode))
    }
    @Test fun actorRoutesRetainOpaqueIdentityAcrossSavedNavigation() {
        val route = TvRoute("actor-works", title = "同名演员", key = "test-route", actorKey = "name:同 名演员&kind=show")
        assertEquals(route, TvRoute.decode(route.encode()))
    }

    @Test fun routesSavedBeforeActorSearchRemainReadable() {
        val route = TvRoute.decode("""{"kind":"season","id":42,"season":3,"title":"第三季","key":"old-route"}""")
        assertEquals("", route.actorKey)
        assertEquals("season", route.kind)
        assertEquals(42L, route.id)
        assertEquals(3, route.season)
        assertEquals("old-route", route.key)
    }
}
