package org.jzmedia.tv.ui

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class DetailModelsTest {
    @Test fun playbackBlocksExplicitOfflineKnowledgeAndAcceptsLegacyRows() {
        assertFalse(knownOffline(null))
        assertFalse(knownOffline(JSONObject()))
        assertFalse(knownOffline(JSONObject().put("exists", true).put("missing", 0)))
        assertTrue(knownOffline(JSONObject().put("exists", false)))
        assertTrue(knownOffline(JSONObject().put("exists", true).put("missing", 1)))
    }

    @Test fun offlineShortcutCannotBecomeTheInitialOrRestoredPlayTarget() {
        val next = JSONObject().put("id", 202).put("season", 1).put("exists", false)
        val row = JSONObject().put("next_episode", next).put("seasons", JSONArray()
            .put(JSONObject().put("season", 0).put("done", true))
            .put(JSONObject().put("season", 1)))
        assertEquals("season:1", detailPrimaryFocusKey("show", row))
        assertFalse("play" in detailFocusKeys("show", row, 0))
        assertEquals("season:1", resolveDetailFocusKey("play", detailPrimaryFocusKey("show", row), detailFocusKeys("show", row, 0), null))
        row.put("next_episode", next.put("exists", true))
        assertEquals("play", detailPrimaryFocusKey("show", row))
        assertTrue("play" in detailFocusKeys("show", row, 0))
    }

    @Test fun offlineNextEpisodeStillAllowsInspectingItsEpisodeButton() {
        val row = JSONObject().put("next_episode", JSONObject().put("id", 202).put("missing", 1))
            .put("episodes", JSONArray()
                .put(JSONObject().put("id", 202).put("missing", 1))
                .put(JSONObject().put("id", 203).put("exists", true)))
        assertEquals("episode:203", detailPrimaryFocusKey("season", row))
        val keys = detailFocusKeys("season", row, 0)
        assertFalse("play" in keys)
        assertTrue("episode:202" in keys)
        assertTrue("episode:203" in keys)
        assertFalse("next-episode" in detailFocusKeys("episode", row, 0))
    }

    @Test fun unavailableMovieVersionKeepsVersionSelectionReachable() {
        val row = JSONObject().put("versions", JSONArray().put(JSONObject().put("id", 1)).put(JSONObject().put("id", 2)))
        val offlineVersion = JSONObject().put("id", 1).put("missing", 1)
        assertEquals("version", detailPrimaryFocusKey("movie", row, offlineVersion))
        assertFalse("play" in detailFocusKeys("movie", row, 0, offlineVersion))
        assertTrue("version" in detailFocusKeys("movie", row, 0, offlineVersion))
        assertEquals("play", detailPrimaryFocusKey("movie", row, JSONObject().put("id", 2)))
    }

    @Test fun returningFocusIsPreservedAndRemovedTargetsFallBack() {
        val available = setOf("more", "play", "season:7")
        assertEquals("season:7", resolveDetailFocusKey("season:7", "play", available, null))
        assertEquals("play", resolveDetailFocusKey("season:8", "play", available, null))
        assertEquals("play", resolveDetailFocusKey("detail:primary", "play", available, null))
        assertEquals("back", resolveDetailFocusKey("back", "play", available, null))
        assertEquals("nav:settings", resolveDetailFocusKey("nav:settings", "play", available, null))
        assertEquals("episode:251", resolveDetailFocusKey("episode:first", "play", available, "episode:251"))
        assertEquals("more", resolveDetailFocusKey("episode:first", "play", available, null))
    }

    @Test fun recommendationFocusWaitsForItsIndependentResponseBeforeFallback() {
        val saved = "similar:321"
        assertFalse(detailFocusReady(saved, false))
        assertTrue(detailFocusReady("play", false))
        assertTrue(detailFocusReady("season:7", false))
        assertTrue(detailFocusReady(saved, true))
        assertEquals(saved, resolveDetailFocusKey(saved, "play", setOf("play", saved), null))
        // Once the response has settled, an error/removed recommendation can recover.
        assertEquals("play", resolveDetailFocusKey(saved, "play", setOf("play"), null))
    }

    @Test fun emptyDetailsAlwaysHaveAReachablePrimaryAction() {
        for (kind in listOf("show", "season", "collection")) {
            assertEquals("more", detailPrimaryFocusKey(kind, JSONObject()))
            assertTrue("more" in detailFocusKeys(kind, JSONObject(), 0))
        }
        assertEquals("more", detailPrimaryFocusKey("episode", JSONObject().put("exists", false)))
    }

    @Test fun brokenSeasonArtHasANormalizedIndependentShowFallback() {
        val row = JSONObject().put("poster_path", "tv/broken-season.png").put("show_poster", "tv/show.png")
        assertEquals("/posters/tv/show.png", detailFallbackPoster(row))
        assertEquals("/posters/tv/show.png", detailFallbackPoster(row.put("show_poster", "posters/tv/show.png")))
        assertEquals("", detailFallbackPoster(row.put("show_poster", "")))
    }

    @Test fun visibleFirstEpisodeDoesNotScrollTheCompactHeroAway() {
        assertEquals(0f, seasonFocusScrollDistance(244f, 56f, 360f, 96f, 16f, true), 0f)
        // Play and More actions above the episode context do not inherit its exclusion zone.
        assertEquals(0f, seasonFocusScrollDistance(48f, 40f, 360f, 96f, 16f, false), 0f)
        assertEquals(-20f, seasonFocusScrollDistance(-4f, 40f, 360f, 96f, 16f, false), 0f)
    }

    @Test fun episodeScrollingMovesOnlyEnoughToClearTheProtectedEdges() {
        assertEquals(32f, seasonFocusScrollDistance(320f, 56f, 360f, 96f, 16f, true), 0f)
        assertEquals(-32f, seasonFocusScrollDistance(80f, 56f, 360f, 96f, 16f, true), 0f)
        assertEquals(0f, seasonFocusScrollDistance(112f, 56f, 360f, 96f, 16f, true), 0f)
        assertEquals(0f, seasonFocusScrollDistance(-20f, 400f, 360f, 96f, 16f, true), 0f)
    }

    @Test fun seasonStateIncludesUnfinishedProgressWithoutCompletedEpisodes() {
        val row = JSONObject().put("season", 2).put("name", "第 2 季").put("total", 12).put("watched_count", 0).put("has_partial", true)
        val active = seasonCardModel(row)
        assertEquals("第 2 季", active.title)
        assertEquals("正在看 · 0/12 集", active.subtitle)
        assertNull(active.progress)
        val complete = seasonCardModel(row.put("watched_count", 12).put("done", true))
        assertEquals("已看完 · 12 集", complete.subtitle)
        assertEquals(1f, complete.progress!!, 0f)
        val partial = seasonCardModel(row.put("done", false).put("watched_count", 3))
        assertEquals(.25f, partial.progress!!, 0f)
        assertEquals("共 0 集", seasonCardModel(JSONObject().put("season", 0).put("name", "特别篇")).subtitle)
    }

    @Test fun seasonCardsKeepSpecialAndDistinctNamesWithoutDuplicatingGenericNames() {
        assertEquals("特别篇", seasonCardModel(JSONObject().put("season", 0).put("name", "Specials")).title)
        assertEquals("第 3 季", seasonCardModel(JSONObject().put("season", 3).put("name", "Season 3")).title)
        assertEquals("第 4 季\n群岛来信", seasonCardModel(JSONObject().put("season", 4).put("name", "群岛来信")).title)
        assertEquals(5, seasonGridColumns(880))
        assertEquals(1, seasonGridColumns(150))
    }
}
