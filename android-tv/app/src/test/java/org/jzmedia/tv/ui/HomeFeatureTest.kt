package org.jzmedia.tv.ui

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class HomeFeatureTest {
    private fun recent(kind: String, versionId: Int) = JSONObject().put("kind", kind).put("id", 12)
        .put("progress", JSONObject().put("version_id", versionId).put("position", 2180).put("duration", 7200))

    @Test fun promotedEpisodeUsesItsVersionAndTheShowBackdropFromRecentResults() {
        val episode = recent("episode", 81).put("backdrop_path", "series/wide.jpg").put("poster_path", "series.jpg")
            .put("subtitle", "S02E03 · 新的旅程")
        val feature = chooseHomeFeature(listOf(episode), listOf(JSONObject().put("id", 1)), emptyList())!!
        assertTrue(feature.resume)
        assertEquals(81L, feature.targetId)
        assertEquals("continue:episode:81", feature.focusKey)
        assertEquals("/posters/series/wide.jpg", feature.backdrop)
        assertEquals("/posters/series.jpg", feature.poster)
        assertTrue(feature.metadata.contains("S02E03"))
    }

    @Test fun legacyProgressUsesThePlayerThresholdAndUnavailableFilesNeverResume() {
        assertTrue(homeCanResume(recent("movie", 94)))
        assertFalse(homeCanResume(recent("movie", 0)))
        assertFalse(homeCanResume(recent("movie", 94).put("missing", true)))
        assertFalse(homeCanResume(recent("movie", 94).put("missing", 1)))
        assertFalse(homeCanResume(recent("movie", 94).put("watched", true)))
        assertFalse(homeCanResume(recent("movie", 94).put("can_resume", false)))
        val short = recent("movie", 94)
        short.getJSONObject("progress").put("position", 15)
        assertFalse(homeCanResume(short))
        short.getJSONObject("progress").put("position", 7080)
        assertFalse(homeCanResume(short))
    }

    @Test fun catalogueFallbackUsesTheFirstAvailableItemAndKeepsItsNavigationKey() {
        val offline = JSONObject().put("id", 5).put("exists", false)
        val show = JSONObject().put("id", 7).put("title", "合成剧集")
        val film = JSONObject().put("id", 9).put("tmdb_id", 702).put("title", "合成电影")
        val feature = chooseHomeFeature(emptyList(), listOf(offline, film), listOf(show))!!
        assertFalse(feature.resume)
        assertEquals("newmovie:9", feature.focusKey)
        assertEquals("/api/movies/9/backdrop", feature.backdrop)
        assertEquals("newshow:7", chooseHomeFeature(emptyList(), listOf(offline), listOf(show))!!.focusKey)
        assertNull(chooseHomeFeature(emptyList(), listOf(offline), emptyList()))
    }
}
