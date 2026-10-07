package org.jzmedia.tv.data

import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class SearchModelsTest {
    @Test fun keyboardFoldsDigitsByDefaultAndPaginationStaysInTheLeftRail() {
        val letters = searchKeyboardKeys(false)
        assertEquals(26, letters.size)
        assertEquals(5, letters.chunked(6).size)
        assertEquals(36, searchKeyboardKeys(true).size)
        assertFalse(showSearchPagination(0, false))
        assertTrue(showSearchPagination(1, false))
        assertTrue(showSearchPagination(0, true))
    }

    @Test fun remoteAndChineseInputCanBeEditedWithoutSplittingUnicode() {
        assertEquals("SQ", appendSearchInput("S", "Q"))
        assertEquals("沙丘", appendSearchInput("", "沙丘\n"))
        assertEquals("沙丘", deleteSearchInput("沙丘\uD83C\uDFAC"))
        assertEquals("沙", deleteSearchInput("沙丘"))
        assertEquals("", deleteSearchInput(""))
        val longQuery = "\uD83C\uDFAC".repeat(81)
        assertEquals("\uD83C\uDFAC".repeat(80), appendSearchInput("", longQuery))
    }

    @Test fun searchLinksPreserveExplicitScopeAndPagingWithoutParameterInjection() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setBody("""{"items":[],"total":0,"has_more":false}"""))
            val api = JzApi(server.url("/jzmedia/").toString())
            api.get(tvSearchPath(" SQ&kind=movie ", "shows", 7, 2))
            val request = server.takeRequest(1, TimeUnit.SECONDS)!!.requestUrl!!
            assertEquals("/jzmedia/api/tv-client/search", request.encodedPath)
            assertEquals("SQ&kind=movie", request.queryParameter("q"))
            assertEquals("show", request.queryParameter("kind"))
            assertEquals("7", request.queryParameter("media_library"))
            assertEquals("48", request.queryParameter("offset"))
            assertEquals("24", request.queryParameter("limit"))
        }
    }

    @Test fun globalSearchOmitsLibraryAndInvalidPagesCannotBecomeNegativeOffsets() {
        assertEquals("/api/tv-client/search?q=SQ&kind=all&limit=24&offset=0", tvSearchPath("SQ", "unknown", 0, -1))
        assertEquals("collection", searchKind("collections"))
        assertEquals("movie", searchKind("movies"))
    }

    @Test fun actorSearchAllowsBrowsingWithoutAQueryAndKeepsTheLibraryScope() {
        assertEquals("/api/tv-client/actors?limit=24&offset=0", actorSearchPath("", 0, -1))
        assertEquals("/api/tv-client/actors?q=ZXC&media_library=7&limit=24&offset=24", actorSearchPath(" ZXC ", 7, 1))
    }

    @Test fun actorKeysRemainOpaqueAndCannotInjectWorkFilters() = runBlocking {
        MockWebServer().use { server ->
            val actorKey = "name:同 名演员&kind=show/演员"
            server.enqueue(MockResponse().setBody("""{"items":[],"total":0,"has_more":false}"""))
            JzApi(server.url("/jzmedia/").toString()).get(actorWorksPath(actorKey, "movie", 5, 2))
            val request = server.takeRequest(1, TimeUnit.SECONDS)!!.requestUrl!!
            assertEquals("/jzmedia/api/tv-client/actor-works", request.encodedPath)
            assertEquals(actorKey, request.queryParameter("actor"))
            assertEquals(listOf("movie"), request.queryParameterValues("kind"))
            assertEquals("5", request.queryParameter("media_library"))
            assertEquals("48", request.queryParameter("offset"))
        }
        assertEquals("/api/tv-client/actor-works?actor=tmdb%3A123&kind=all&limit=24&offset=0", actorWorksPath("tmdb:123", "collection", 0, -1))
    }

    @Test fun actorAvatarsOnlyUseLocalPosterPaths() {
        for (path in listOf("persons/123.jpg", "posters/persons/123.jpg", "/posters/persons/123.jpg")) {
            assertEquals("/posters/persons/123.jpg", actorAvatarPath(JSONObject().put("avatar_path", path)))
        }
        for (path in listOf("", "https://image.tmdb.org/actor.jpg", "//other.example/actor.jpg", "../actor.jpg", "persons\\actor.jpg")) {
            assertEquals("", actorAvatarPath(JSONObject().put("avatar_path", path)))
        }
        assertEquals("电影 2 · 剧集 3", actorWorkSummary(JSONObject().put("movie_count", 2).put("show_count", 3)))
    }
}
