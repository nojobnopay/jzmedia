package org.jzmedia.tv.data

import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class MediaModelsTest {
    @Test fun showAndSeasonPostersAreRelativeToThePosterDirectory() {
        // TV scanning persists POSTER_DIR-relative paths; recent-played returns them unchanged.
        listOf("tv/123.jpg", "/tv/123.jpg", "tv/123_s1.jpg").forEach { stored ->
            assertEquals("/posters/" + stored.trimStart('/'), posterPath(JSONObject().put("poster_path", stored)))
        }
    }

    @Test fun alreadyPrefixedMovieAndTvPostersKeepOnePrefix() {
        listOf("posters/movies/123.jpg", "/posters/movies/123.jpg", "posters/tv/123.jpg", "/posters/tv/123.jpg").forEach { stored ->
            assertEquals("/" + stored.trimStart('/'), posterPath(JSONObject().put("poster_path", stored)))
        }
    }

    @Test fun legacyFilenamesAreLeftForTheServerToResolve() {
        assertEquals("/posters/tv_123.jpg", posterPath(JSONObject().put("poster_path", "tv_123.jpg")))
        assertEquals("/posters/123.jpg", posterPath(JSONObject().put("poster_path", "123.jpg")))
    }

    @Test fun episodeAndCollectionFallbacksUseTheSamePathContract() {
        val episode = JSONObject().put("poster_path", JSONObject.NULL).put("show_poster", "tv/123.jpg").put("season_poster", "tv/123_s1.jpg")
        assertEquals("/posters/tv/123.jpg", posterPath(episode))
        episode.put("show_poster", " ")
        assertEquals("/posters/tv/123_s1.jpg", posterPath(episode))
        assertEquals("/posters/movies/123.jpg", posterPath(JSONObject().put("cover", "movies/123.jpg")))
    }

    @Test fun absentPostersDoNotRequestThePosterDirectory() {
        assertEquals("", posterPath(JSONObject()))
        assertEquals("", posterPath(JSONObject().put("poster_path", JSONObject.NULL).put("show_poster", " ").put("cover", "")))
    }

    @Test fun recentEpisodeImageRequestsPreserveTheDeploymentPrefix() = runBlocking {
        MockWebServer().use { server ->
            val recentEpisode = JSONObject("""{"kind":"episode","poster_path":"tv/123.jpg","progress":{"version_id":456}}""")
            for (prefix in listOf("/", "/jzmedia/")) {
                server.enqueue(MockResponse().setHeader("Content-Type", "image/png").setBody("synthetic-image"))
                val api = JzApi(server.url(prefix).toString())
                assertArrayEquals("synthetic-image".toByteArray(), api.getBytes(posterPath(recentEpisode)))
                assertEquals("${prefix}posters/tv/123.jpg", server.takeRequest(1, TimeUnit.SECONDS)!!.path)
            }
        }
    }
}
