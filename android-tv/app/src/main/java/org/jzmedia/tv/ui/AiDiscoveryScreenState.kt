package org.jzmedia.tv.ui

import android.os.SystemClock
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import org.jzmedia.tv.data.DISCOVERY_QUERY_LIMIT
import org.jzmedia.tv.data.DiscoveryResult

internal sealed interface DiscoveryRequest {
    data class Search(val query: String, val source: String, val session: String?, val reuse: Boolean = false) : DiscoveryRequest
    data class Refresh(val session: String) : DiscoveryRequest
}

/** One route owns this state in memory, including while a result detail is on top. */
internal class AiDiscoveryScreenState(var launchInput: Boolean = true) {
    var draft by mutableStateOf("")
        private set
    var result by mutableStateOf<DiscoveryResult?>(null)
        private set
    var request by mutableStateOf<DiscoveryRequest?>(null)
        private set
    var lastSearch by mutableStateOf<DiscoveryRequest.Search?>(null)
        private set
    var error by mutableStateOf("")
        private set
    var expired by mutableStateOf(false)
        private set
    var editing by mutableStateOf(false)
        private set
    var retry by mutableStateOf<DiscoveryRequest?>(null)
        private set
    var restoreFocus: String? = null
    var firstIndex = 0
    var firstOffset = 0
    private var restoreSession: String? = null

    fun change(text: String) {
        val count = text.codePointCount(0, text.length)
        draft = if (count <= DISCOVERY_QUERY_LIMIT) text else text.substring(0, text.offsetByCodePoints(0, DISCOVERY_QUERY_LIMIT))
    }

    fun editLast() {
        val previous = lastSearch ?: return
        if (request != null || expired) return
        editing = true
        change(previous.query)
    }

    fun submit(text: String = draft) {
        if (request != null || expired || restoreSession != null) return
        val query = text.trim()
        if (query.isEmpty()) return
        if (query.codePointCount(0, query.length) > DISCOVERY_QUERY_LIMIT) {
            change(query)
            error = "找片描述最多 ${DISCOVERY_QUERY_LIMIT} 字，请缩短后再搜索"
            return
        }
        val previous = lastSearch.takeIf { editing && it?.source == result?.source }
        draft = query
        start(DiscoveryRequest.Search(query, previous?.source ?: result?.source ?: "library",
            if (previous != null) previous.session else result?.sessionId))
    }

    fun searchPublic() {
        val list = result ?: return
        if (request != null || list.source != "library" || expired) return
        start(DiscoveryRequest.Search("按刚才的要求查公开片目", "public", list.sessionId, true))
    }

    private fun start(next: DiscoveryRequest) {
        error = ""
        retry = null
        request = next
    }

    fun retryFailed() {
        if (request == null && !expired) retry?.let(::start)
    }

    fun complete(attempt: DiscoveryRequest, list: DiscoveryResult) {
        if (request !== attempt) return
        request = null
        result = list
        restoreSession = null
        if (attempt is DiscoveryRequest.Search) {
            if (!attempt.reuse) lastSearch = attempt
            editing = false
            draft = ""
            restoreFocus = null
            firstIndex = 0
            firstOffset = 0
        }
    }

    fun fail(attempt: DiscoveryRequest, message: String, code: String?) {
        if (request !== attempt) return
        request = null
        error = message
        expired = code in setOf("session_expired", "viewing_locked", "children_restricted")
        if (expired) {
            result = null
            restoreSession = null
        }
        retry = attempt.takeUnless { expired }
    }

    fun stopCurrent() {
        request?.let { fail(it, "已取消找片", null) }
    }

    fun cancel() {
        request = null
    }

    fun leaveForDetail(key: String, index: Int, offset: Int) {
        val list = result ?: return
        restoreFocus = key
        firstIndex = index
        firstOffset = offset
        restoreSession = list.sessionId
        result = null // Revalidate access before redisplaying titles on return.
    }

    fun restore() {
        val session = restoreSession ?: return
        if (request == null) start(DiscoveryRequest.Refresh(session))
    }

    fun restart() {
        cancel()
        draft = ""
        lastSearch = null
        result = null
        restoreSession = null
        restoreFocus = null
        firstIndex = 0
        firstOffset = 0
        error = ""
        editing = false
        expired = false
        retry = null
    }
}

/** A system recognizer may return after the TV has been asleep. */
internal class DiscoveryVoiceTurn(private val nowMillis: () -> Long = SystemClock::elapsedRealtime) {
    private var launchedAt = 0L
    var waiting by mutableStateOf(false)
        private set

    fun isFresh() = nowMillis() - launchedAt in 0L until 120_000L

    fun launch(): Boolean {
        if (waiting) return false
        launchedAt = nowMillis()
        waiting = true
        return true
    }

    fun finish(ok: Boolean, candidates: List<String>?): String? {
        if (!waiting) return null
        waiting = false
        return if (ok && isFresh()) candidates?.firstOrNull { it.isNotBlank() }?.trim() else null
    }
}
