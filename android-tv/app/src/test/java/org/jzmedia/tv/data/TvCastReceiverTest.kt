package org.jzmedia.tv.data

import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class TvCastReceiverTest {
    private fun command(action: String = "play", payload: JSONObject = JSONObject()
        .put("kind", "movie").put("version_id", 42).put("position", 125.5).put("audio", 1).put("sub", -1)) =
        JSONObject().put("id", "cmd_one").put("session_id", "tv_one").put("action", action)
            .put("valid_for_seconds", 60).put("payload", payload)

    @Test fun explicitSourcePositionAndSourceTrackOrdinalsAreNotDefaults() {
        val value = TvCastCommand.fromJson(command())
        assertTrue(value.startsMedia)
        assertEquals(125.5, value.position!!, 0.0)
        assertEquals(1, value.audio)
        assertEquals(-1, value.subtitle)
        val resume = TvCastCommand.fromJson(command("play", JSONObject()))
        assertFalse(resume.startsMedia)
        assertNull(resume.versionId)
    }

    @Test fun invalidCommandsCannotPickAnotherMediaOrTrackSilently() {
        listOf(command("launch"), command(payload = JSONObject().put("kind", "file").put("version_id", 42).put("position", 0)),
            command(payload = JSONObject().put("kind", "movie").put("version_id", 0).put("position", 0)),
            command(payload = JSONObject().put("kind", "movie").put("version_id", "42").put("position", 0)),
            command(payload = JSONObject().put("kind", "movie").put("version_id", 42.5).put("position", 0)),
            command(payload = JSONObject().put("kind", "movie").put("version_id", 42).put("position", -1)),
            command(payload = JSONObject().put("kind", "movie").put("version_id", 42).put("position", 0).put("audio", "wrong")),
            command(payload = JSONObject().put("kind", "movie").put("version_id", 42).put("position", 0).put("sub", 0.5)),
            command("seek", JSONObject())).forEach { value ->
            assertThrows(Exception::class.java) { TvCastCommand.fromJson(value) }
        }
    }

    @Test fun subtitleOrdinalsMatchTheServerLimitIndependentlyOfAudio() {
        val payload = JSONObject().put("kind", "movie").put("version_id", 42).put("position", 0)
            .put("audio", 63).put("sub", 1024)
        val value = TvCastCommand.fromJson(command(payload = payload))
        assertEquals(63, value.audio)
        assertEquals(1024, value.subtitle)
        assertThrows(Exception::class.java) { TvCastCommand.fromJson(command(payload = JSONObject(payload.toString()).put("audio", 64))) }
        assertThrows(Exception::class.java) { TvCastCommand.fromJson(command(payload = JSONObject(payload.toString()).put("sub", 1025))) }
    }

    @Test fun anAckRetryNeverReplaysAnAcceptedOrCancelledCommand() {
        val value = TvCastCommand.fromJson(command())
        val history = TvCastCommandHistory()
        assertTrue(history.claim(value))
        assertFalse(history.accepted(value))
        assertFalse(history.claim(value))
        history.complete(value, true)
        assertFalse(history.claim(value))
        assertTrue(history.accepted(value))
        val cancelled = value.copy(id = "cmd_cancelled")
        assertTrue(history.claim(cancelled))
        history.complete(cancelled, false)
        assertFalse(history.claim(cancelled))
        assertFalse(history.accepted(cancelled))
    }

    @Test fun commandExpiryUsesMonotonicReceiptTimeAndRejectsMissingLifetime() {
        val value = TvCastCommand.fromJson(command().put("valid_for_seconds", 0.5))
        assertTrue(value.isValid(value.receivedAtNanos))
        assertTrue(value.isValid(value.receivedAtNanos + 499_999_999))
        assertFalse(value.isValid(value.receivedAtNanos + 500_000_000))
        assertFalse(TvCastCommand.fromJson(command().put("valid_for_seconds", 0)).isValid())
        assertThrows(Exception::class.java) { TvCastCommand.fromJson(command().put("valid_for_seconds", -1)) }
        assertThrows(Exception::class.java) { TvCastCommand.fromJson(command().apply { remove("valid_for_seconds") }) }
    }

    @Test fun receiverCredentialUsesOnlyItsHeaderAndCurrentViewingScope() = runBlocking {
        MockWebServer().use { server ->
            val api = JzApi(server.url("/prefix/").toString(), "api-secret", ViewingSession(token = "tv-viewing"))
            val receiver = TvCastReceiver(api, "stable-installation", "Test TV")
            server.enqueue(MockResponse().setBody("""{"receiver_id":"app_one","receiver_token":"receiver-secret","heartbeat_seconds":3}"""))
            receiver.register()
            val register = server.takeRequest(1, TimeUnit.SECONDS)!!
            assertEquals("/prefix/api/cast/apps/register", register.path)
            assertEquals("stable-installation", JSONObject(register.body.readUtf8()).getString("device_id"))
            assertNull(register.getHeader("X-Receiver-Token"))
            server.enqueue(MockResponse().setBody(JSONObject().put("command", command()).toString()))
            val delivered = receiver.poll(TvCastSnapshot())!!
            val poll = server.takeRequest(1, TimeUnit.SECONDS)!!
            assertEquals("/prefix/api/cast/apps/app_one/poll", poll.path)
            assertEquals("receiver-secret", poll.getHeader("X-Receiver-Token"))
            assertEquals("tv-viewing", poll.getHeader("X-Viewing-Session"))
            assertEquals("api-secret", poll.getHeader("X-Api-Token"))
            val heartbeat = JSONObject(poll.body.readUtf8())
            assertTrue(heartbeat.getBoolean("foreground"))
            assertEquals("IDLE", heartbeat.getString("state"))
            assertTrue(heartbeat.isNull("position"))
            server.enqueue(MockResponse().setBody("{}"))
            receiver.acknowledge(delivered, false, "电视已离开前台")
            val ack = server.takeRequest(1, TimeUnit.SECONDS)!!
            assertFalse(JSONObject(ack.body.readUtf8()).getBoolean("accepted"))
            assertFalse(ack.path!!.contains("secret"))
            server.enqueue(MockResponse().setBody("{}"))
            receiver.unregister()
            val unregister = server.takeRequest(1, TimeUnit.SECONDS)!!
            assertEquals("DELETE", unregister.method)
            assertEquals("receiver-secret", unregister.getHeader("X-Receiver-Token"))
        }
    }

    @Test fun delayedPollResponseConsumesTheRemainingServerLifetime() = runBlocking {
        MockWebServer().use { server ->
            val receiver = TvCastReceiver(JzApi(server.url("/").toString()), "installation", "TV")
            server.enqueue(MockResponse().setBody("""{"receiver_id":"app_one","receiver_token":"synthetic"}"""))
            receiver.register()
            server.enqueue(MockResponse().setBody(JSONObject().put("command", command().put("valid_for_seconds", 0.1)).toString())
                .setBodyDelay(250, TimeUnit.MILLISECONDS))
            assertFalse(receiver.poll(TvCastSnapshot())!!.isValid())
        }
    }
}
