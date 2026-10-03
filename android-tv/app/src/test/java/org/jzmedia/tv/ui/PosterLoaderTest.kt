package org.jzmedia.tv.ui

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.cancel
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.jzmedia.tv.data.ApiException
import org.jzmedia.tv.data.JzApi
import org.junit.Assert.*
import org.junit.Test
import java.io.IOException
import java.util.concurrent.TimeUnit

class PosterLoaderTest {
    @Test fun httpFailureFallsBackAndPreservesTheServerPrefix() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setResponseCode(404))
            server.enqueue(MockResponse().setBody("show-image"))
            val api = JzApi(server.url("/jzmedia/").toString())
            val failures = mutableListOf<Pair<Int, Exception?>>()
            val image = loadPosterWithFallback("/posters/season.jpg", "/posters/show.jpg",
                onFailure = { attempt, error -> failures += attempt to error }) {
                api.getBytes(it).toString(Charsets.UTF_8)
            }
            assertEquals("show-image", image)
            assertEquals(1, failures.size)
            assertEquals(0, failures.single().first)
            assertEquals(404, (failures.single().second as ApiException).status)
            assertEquals("/jzmedia/posters/season.jpg", server.takeRequest(1, TimeUnit.SECONDS)!!.path)
            assertEquals("/jzmedia/posters/show.jpg", server.takeRequest(1, TimeUnit.SECONDS)!!.path)
            assertEquals(2, server.requestCount)
        }
    }

    @Test fun nullDecodeTriesTheFallback() = runBlocking {
        val attempts = mutableListOf<String>()
        val failures = mutableListOf<Pair<Int, Exception?>>()
        val expected = Any()
        val image = loadPosterWithFallback("season", "show", onFailure = { attempt, error -> failures += attempt to error }) {
            attempts += it
            if (it == "season") null else expected
        }
        assertSame(expected, image)
        assertEquals(listOf("season", "show"), attempts)
        assertEquals(listOf(0 to null), failures)
    }

    @Test fun firstSuccessfulImageDoesNotRequestTheFallback() = runBlocking {
        val attempts = mutableListOf<String>()
        val expected = Any()
        val image = loadPosterWithFallback("season", "show", onFailure = { _, _ -> fail("Unexpected image failure") }) {
            attempts += it
            expected
        }
        assertSame(expected, image)
        assertEquals(listOf("season"), attempts)
    }

    @Test fun blankAndDuplicatePathsAreNotRequested() = runBlocking {
        val attempts = mutableListOf<String>()
        val failures = mutableListOf<Int>()
        suspend fun load(primary: String, fallback: String): String? =
            loadPosterWithFallback(primary, fallback, onFailure = { attempt, _ -> failures += attempt }) {
                attempts += it
                null
            }
        assertNull(load("", " \t"))
        assertTrue(attempts.isEmpty())
        assertTrue(failures.isEmpty())
        assertNull(load("", "show"))
        assertEquals(listOf("show"), attempts)
        attempts.clear()
        failures.clear()
        assertNull(load("show", "show"))
        assertEquals(listOf("show"), attempts)
        assertEquals(listOf(0), failures)
    }

    @Test fun exhaustedPathsReturnNullForTheTextPlaceholder() = runBlocking {
        val failures = mutableListOf<Pair<Int, Exception?>>()
        val networkError = IOException("Image unavailable")
        val image = loadPosterWithFallback<String>("season", "show", onFailure = { attempt, error -> failures += attempt to error }) {
            if (it == "season") throw networkError
            null
        }
        assertNull(image)
        assertEquals(2, failures.size)
        assertSame(networkError, failures[0].second)
        assertEquals(1 to null, failures[1])
    }

    @Test fun cancellationIsRethrownWithoutRequestingTheFallback() = runBlocking {
        val attempts = mutableListOf<String>()
        val cancellation = CancellationException("Image source changed")
        try {
            loadPosterWithFallback<String>("season", "show", onFailure = { _, _ -> fail("Cancellation is not an image failure") }) {
                attempts += it
                throw cancellation
            }
            fail("Expected cancellation")
        } catch (error: CancellationException) {
            assertSame(cancellation, error)
        }
        assertEquals(listOf("season"), attempts)
    }

    @Test fun cancelledCoroutineCannotPublishALateSuccessfulImage() = runBlocking {
        val attempts = mutableListOf<String>()
        var published: String? = null
        val request = launch {
            published = loadPosterWithFallback("season", "show", onFailure = { _, _ -> fail("Cancellation is not an image failure") }) {
                attempts += it
                currentCoroutineContext().cancel()
                "old-season-image"
            }
        }
        request.join()
        assertTrue(request.isCancelled)
        assertNull(published)
        assertEquals(listOf("season"), attempts)
    }
}
