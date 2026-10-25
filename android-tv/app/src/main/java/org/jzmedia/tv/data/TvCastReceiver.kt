package org.jzmedia.tv.data

import org.json.JSONObject

data class TvCastCommand(val id: String, val sessionId: String, val action: String,
                         val kind: String? = null, val versionId: Long? = null, val title: String = "",
                         val position: Double? = null, val audio: Int? = null, val subtitle: Int? = null,
                         val receivedAtNanos: Long, val validForNanos: Long) {
    val startsMedia: Boolean get() = action == "play" && versionId != null
    fun isValid(nowNanos: Long = System.nanoTime()): Boolean = nowNanos - receivedAtNanos in 0 until validForNanos

    companion object {
        fun fromJson(value: JSONObject, receivedAtNanos: Long = System.nanoTime()): TvCastCommand {
            fun identifier(name: String): String = value.getString(name).also {
                require(it.length in 1..128 && it.matches(Regex("[A-Za-z0-9_-]+"))) { "电视控制命令无效" }
            }
            val id = identifier("id")
            val session = identifier("session_id")
            val action = value.getString("action")
            require(action in listOf("play", "pause", "seek", "stop")) { "电视控制命令不受支持" }
            val validity = (value.opt("valid_for_seconds") as? Number)?.toDouble()
                ?: throw IllegalArgumentException("缺少电视控制有效期")
            require(validity.isFinite() && validity in 0.0..60.0) { "电视控制有效期无效" }
            val payload = value.optJSONObject("payload") ?: JSONObject()
            fun position(required: Boolean): Double? {
                if (payload.isNull("position")) { require(!required) { "缺少起播位置" }; return null }
                val number = payload.opt("position") as? Number ?: throw IllegalArgumentException("起播位置无效")
                return number.toDouble().also { require(it.isFinite() && it in 0.0..604800.0) { "起播位置无效" } }
            }
            fun track(name: String, minimum: Int, maximum: Int): Int? {
                if (payload.isNull(name)) return null
                val number = (payload.opt(name) as? Number)?.toDouble() ?: throw IllegalArgumentException("轨道选择无效")
                require(number.isFinite() && number == number.toInt().toDouble() && number in minimum.toDouble()..maximum.toDouble()) { "轨道选择无效" }
                return number.toInt()
            }
            val startsMedia = action == "play" && payload.has("version_id")
            val kind = if (startsMedia) payload.getString("kind").also {
                require(it in listOf("movie", "episode")) { "投屏媒体类型不受支持" }
            } else null
            val media = if (startsMedia) {
                val number = payload.opt("version_id") as? Number ?: throw IllegalArgumentException("投屏媒体无效")
                number.toLong().also {
                    require(it > 0 && number.toDouble().isFinite() && number.toDouble() == it.toDouble()) { "投屏媒体无效" }
                }
            } else null
            val title = payload.text("title").take(200)
            return TvCastCommand(id, session, action, kind, media, title,
                position(startsMedia || action == "seek"), track("audio", 0, 63), track("sub", -1, 1024),
                receivedAtNanos, (validity * 1_000_000_000).toLong())
        }
    }
}

data class TvCastSnapshot(val sessionId: String? = null, val state: String = "IDLE",
                          val position: Double? = null, val duration: Double? = null, val error: String = "") {
    fun json(): JSONObject = JSONObject().put("foreground", true).put("session_id", sessionId ?: JSONObject.NULL)
        .put("state", state).put("position", position?.takeIf { it.isFinite() && it >= 0 } ?: JSONObject.NULL)
        .put("duration", duration?.takeIf { it.isFinite() && it > 0 } ?: JSONObject.NULL).put("error", error.take(200))
}

/** Only the foreground owner calls this transport. Receiver credentials never enter resource URLs. */
class TvCastReceiver(private val api: JzApi, private val deviceId: String, private val name: String) {
    private var receiverId: String? = null
    private var receiverToken = ""
    var heartbeatMillis: Long = 3000
        private set

    suspend fun register() {
        val value = api.post("/api/cast/apps/register", JSONObject().put("device_id", deviceId)
            .put("name", name.take(80)).put("protocol_version", 1))
        val id = value.getString("receiver_id")
        val token = value.getString("receiver_token")
        require(id.startsWith("app_") && id.length <= 128 && id.matches(Regex("[A-Za-z0-9_-]+"))) { "电视接收标识无效" }
        require(token.isNotBlank() && token.length <= 512 && token.none { it == '\r' || it == '\n' }) { "电视接收凭据无效" }
        receiverId = id; receiverToken = token
        heartbeatMillis = value.optLong("heartbeat_seconds", 3).coerceIn(1, 5) * 1000
    }

    suspend fun poll(snapshot: TvCastSnapshot): TvCastCommand? {
        val id = checkNotNull(receiverId)
        // The remaining lifetime was calculated before the response travelled here.
        // Charge the whole request against it, conservatively rejecting late delivery.
        val requestedAt = System.nanoTime()
        val value = api.postReceiver("/api/cast/apps/$id/poll", receiverToken, snapshot.json())
        return value.optJSONObject("command")?.let { TvCastCommand.fromJson(it, requestedAt) }
    }

    suspend fun acknowledge(command: TvCastCommand, accepted: Boolean, error: String = "") {
        val id = checkNotNull(receiverId)
        api.postReceiver("/api/cast/apps/$id/ack", receiverToken, JSONObject().put("command_id", command.id)
            .put("accepted", accepted).put("error", error.take(200)))
    }

    suspend fun unregister() {
        val id = receiverId ?: return
        receiverId = null
        try { api.deleteReceiver("/api/cast/apps/$id", receiverToken) }
        finally { receiverToken = "" }
    }
}

/** Retries of an acknowledged or cancelled foreground command never restart its media. */
class TvCastCommandHistory {
    private val received = LinkedHashMap<String, Boolean>()
    fun claim(command: TvCastCommand): Boolean {
        if (received.containsKey(command.id)) return false
        received[command.id] = false
        while (received.size > 64) received.remove(received.keys.first())
        return true
    }
    fun complete(command: TvCastCommand, accepted: Boolean) { received[command.id] = accepted }
    fun accepted(command: TvCastCommand): Boolean = received[command.id] == true
}
