package org.jzmedia.tv.playback

import org.junit.Assert.*
import org.junit.Test
import org.jzmedia.tv.data.JzApi
import java.io.IOException

class OnlineSessionMediaUrlTest {
    private val session = "s".repeat(43)
    private val resource = "r".repeat(32)
    private val path = "/api/online/sessions/$session/"
    @Test fun rewrittenRootResourcesAndAlreadyPrefixedResourcesUseTheConfiguredServerPrefix() {
        val api = JzApi("http://localhost:8080/prefix/")
        val expected = "http://localhost:8080/prefix${path}resources/$resource"
        assertEquals(expected, onlineSessionMediaUrl(api, session, "http://localhost:8080${path}resources/$resource"))
        assertEquals(expected, onlineSessionMediaUrl(api, session, "${path}resources/$resource"))
        assertEquals(expected, onlineSessionMediaUrl(api, session, expected))
        assertEquals("http://localhost:8080/prefix${path}media", onlineSessionMediaUrl(api, session, "${path}media"))
    }
    @Test fun onlyTheCurrentOpaqueSessionResourcesCanBeRelocated() {
        val api = JzApi("http://localhost:8080/prefix/")
        listOf("http://other.example${path}media", "https://localhost:8080${path}media",
            "http://localhost:8081${path}media", "http://user:secret@localhost:8080${path}media",
            "${path}media?token=secret", "${path}media#fragment", "//other.example${path}media",
            "/api/online/sessions/${"x".repeat(43)}/media", "/api/stream/1/media", "/posters/image.jpg",
            "${path}resources/invalid", "${path}resources/$resource/extra", "${path}resources/%2Fbad")
            .forEach { value -> assertThrows(IOException::class.java) { onlineSessionMediaUrl(api, session, value) } }
    }
}
