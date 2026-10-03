package org.jzmedia.tv.data

import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.*
import org.junit.Test

class SearchModelsTest {
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
}
