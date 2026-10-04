package org.jzmedia.tv.data

import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class MediaModelsTest {
    @Test fun cardsIdentifyTheMediaLibraryInsteadOfTheVideoSource() {
        val row = JSONObject("""{"media_library_id":2,"media_library_name":"动画收藏","library_name":"NAS 视频目录"}""")
        assertEquals("动画收藏", mediaLibraryLabel(row))
        row.remove("media_library_name")
        assertEquals("媒体库 #2", mediaLibraryLabel(row))
        row.remove("media_library_id")
        assertEquals("", mediaLibraryLabel(row))
        assertEquals("", mediaLibraryLabel(JSONObject("""{"media_library_id":null,"media_library_name":null}""")))
    }

    @Test fun ratingsUseRealApiFieldsWithAnExplicitSourceAndOneDecimal() {
        assertEquals("TMDB 8.3", mediaRatingLabel(JSONObject("""{"tmdb_rating":8.25,"vote_average":6.2,"douban_rating":9.1}""")))
        assertEquals("TMDB 7.0", mediaRatingLabel(JSONObject("""{"vote_average":7}""")))
        assertEquals("豆瓣 9.1", mediaRatingLabel(JSONObject("""{"tmdb_rating":null,"douban_rating":"9.1"}""")))
        assertEquals("自评 8.0", mediaRatingLabel(JSONObject("""{"custom_rating":8}""")))
    }

    @Test fun missingAndInvalidRatingsDoNotBecomeBadges() {
        listOf("null", "0", "-1", "11", "\"NaN\"", "\"Infinity\"", "\"unknown\"").forEach { value ->
            assertEquals("", mediaRatingLabel(JSONObject("""{"tmdb_rating":$value}""")))
        }
        assertEquals("", mediaRatingLabel(JSONObject()))
        assertEquals("TMDB 10.0", mediaRatingLabel(JSONObject("""{"tmdb_rating":10}""")))
    }

    @Test fun metadataKeepsYearAndWatchedWithoutDuplicatingTheRatingBadge() {
        assertEquals("2026 · 已看", mediaCardSubtitle(JSONObject("""{"year":2026,"watched":1,"tmdb_rating":8.3}""")))
        assertEquals("", mediaCardSubtitle(JSONObject("""{"year":0,"tmdb_rating":8.3}""")))
    }

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
