package org.jzmedia.tv.ui

import org.junit.Assert.*
import org.junit.Test

class TvRouteTest {
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
