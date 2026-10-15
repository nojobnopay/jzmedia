package org.jzmedia.tv.data

import org.json.JSONArray
import org.json.JSONObject

const val VIEWING_IDLE_MILLIS = 15 * 60 * 1000L

/** The credential identifies this device. Passwords and private grants are never saved. */
data class ViewingSession(
    val mode: String = "normal",
    val enabled: Boolean = false,
    val managementUnlocked: Boolean = false,
    val generation: Long = 0,
    val token: String = "",
) {
    val label: String get() = when (mode) { "children" -> "儿童模式"; "private" -> "隐私模式"; else -> "普通模式" }
    val protected: Boolean get() = mode == "private" || managementUnlocked
    val historyScope: String get() = if (mode == "children") "children" else "normal"

    companion object {
        fun fromJson(value: JSONObject, previousToken: String = ""): ViewingSession {
            val mode = value.text("mode")
            require(mode in listOf("normal", "children", "private")) { "服务器返回了无法识别的观看模式" }
            val token = value.text("session_token").ifBlank { previousToken }
            require(token.isNotBlank() && token.length <= 4096 && token.none { it == '\r' || it == '\n' }) { "观看模式会话无效，请重新连接" }
            if (previousToken.isNotBlank() && token != previousToken) {
                throw ApiException(401, "本机观看会话已失效，请重新建立连接", "viewing_locked")
            }
            return ViewingSession(mode, value.optBoolean("enabled"), value.optBoolean("management_unlocked"),
                value.optLong("generation"), token)
        }
    }
}

fun supportsViewingModes(info: JSONObject): Boolean = when (val features = info.opt("features")) {
    is JSONObject -> features.optBoolean("viewing_modes")
    is JSONArray -> (0 until features.length()).any { features.optString(it) == "viewing_modes" }
    else -> false
}

fun viewingIdleExpired(lastInteraction: Long, now: Long, playing: Boolean): Boolean =
    !playing && now - lastInteraction >= VIEWING_IDLE_MILLIS

/** Only stable error codes are trusted; upstream response text can contain credentials. */
fun viewingErrorMessage(code: String): String? = when (code) {
    "protection_setup_required" -> "请先设置保护密码"
    "protection_password_required" -> "请输入保护密码"
    "protection_password_invalid" -> "保护密码不正确，请重试"
    "protection_rate_limited" -> "密码尝试过多，请 5 分钟后重试"
    "invalid_password" -> "保护密码应为 6–128 个字符"
    "viewing_locked" -> "观看模式已锁定，请重新进入"
    "management_required" -> "请先输入保护密码解锁内容保护设置"
    else -> null
}
