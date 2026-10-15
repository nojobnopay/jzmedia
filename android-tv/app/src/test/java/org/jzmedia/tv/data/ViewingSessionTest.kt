package org.jzmedia.tv.data

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class ViewingSessionTest {
    @Test fun negotiatesOptionalFeatureWithoutRaisingPlaybackProtocol() {
        assertTrue(supportsViewingModes(JSONObject("""{"features":["android_tv","viewing_modes"]}""")))
        assertTrue(supportsViewingModes(JSONObject("""{"features":{"viewing_modes":true}}""")))
        assertFalse(supportsViewingModes(JSONObject("""{"protocol_version":1,"features":["android_tv"]}""")))
        assertFalse(supportsViewingModes(JSONObject()))
    }

    @Test fun sessionKeepsCredentialButDoesNotInferPrivateFromManagement() {
        val session = ViewingSession.fromJson(JSONObject("""{"mode":"normal","enabled":true,"management_unlocked":true,"generation":7}"""), "device-session")
        assertEquals("normal", session.mode)
        assertTrue(session.protected)
        assertEquals("device-session", session.token)
        assertEquals(7L, session.generation)
        assertEquals("普通模式", session.label)
        assertThrows(IllegalArgumentException::class.java) {
            ViewingSession.fromJson(JSONObject("""{"mode":"unknown","session_token":"token"}"""))
        }
        assertThrows(IllegalArgumentException::class.java) { ViewingSession.fromJson(JSONObject()) }
    }

    @Test fun playbackDoesNotExpireButPausedPlaybackDoes() {
        assertFalse(viewingIdleExpired(100, 100 + VIEWING_IDLE_MILLIS - 1, false))
        assertTrue(viewingIdleExpired(100, 100 + VIEWING_IDLE_MILLIS, false))
        assertFalse(viewingIdleExpired(100, 100 + VIEWING_IDLE_MILLIS * 2, true))
    }

    @Test fun unknownSessionCannotSilentlyBecomeANewNormalSession() {
        val api = JzApi("http://server", viewing = ViewingSession(mode = "children", token = "existing-device"))
        try {
            api.withViewing(JSONObject("""{"mode":"normal","session_token":"replacement-device"}"""))
            fail("Unknown credentials must not downgrade the active mode")
        } catch (e: ApiException) { assertEquals("viewing_locked", e.code) }
        assertEquals("children", api.viewing?.mode)
        assertEquals("existing-device", api.viewingToken)
    }

    @Test fun managementExpiryKeepsPlaybackContextButCannotChangeViewingScope() {
        val session = ViewingSession(mode = "private", managementUnlocked = true, generation = 4, token = "device")
        val api = JzApi("http://server", viewing = session)
        val scope = api.cacheScope
        api.privateHistory.recordQuery("http://server", 0, "title", "Private query")
        api.updateViewingMetadata(session.copy(managementUnlocked = false))
        assertEquals(scope, api.cacheScope)
        assertEquals("private", api.viewing?.mode)
        assertFalse(api.viewing!!.managementUnlocked)
        assertEquals(listOf("Private query"), api.privateHistory.queries("http://server", 0, "title"))
        assertThrows(IllegalStateException::class.java) { api.updateViewingMetadata(session.copy(mode = "normal")) }
        assertThrows(IllegalStateException::class.java) { api.updateViewingMetadata(session.copy(generation = 5)) }
    }

    @Test fun privateHistoryIsMemoryOnlyAndNeverSharedAcrossSessions() {
        val privateApi = JzApi("http://server", viewing = ViewingSession(mode = "private", token = "private-session"))
        privateApi.privateHistory.recordQuery("http://server", 1, "title", "Private movie")
        privateApi.privateHistory.recordActor("http://server", 1, RecentActor("tmdb:1", "Private actor"))
        val reopened = JzApi("http://server", viewing = ViewingSession(mode = "private", token = "private-session"))
        assertTrue(reopened.privateHistory.queries("http://server", 1, "title").isEmpty())
        assertTrue(reopened.privateHistory.actors("http://server", 1).isEmpty())
        assertNotEquals(privateApi.cacheScope, reopened.cacheScope)
    }
}
