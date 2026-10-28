package org.jzmedia.tv.data

import kotlin.coroutines.coroutineContext
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject

/** All calls retain the JzApi server origin, write token and device identity. */
class OnlineApi(val api: JzApi) {
    private suspend fun <T> read(method: String, path: String, body: JSONObject? = null,
                                 scopeToken: String? = null, parser: (JSONObject) -> T): T {
        val value = api.requestOnline(method, path, body, scopeToken)
        return try { parser(value) }
        catch (error: ApiException) { throw error }
        catch (_: Exception) { throw ApiException(0, "服务器返回了无法识别的在线数据", "invalid_online_response") }
    }
    private fun sameScope(actual: String, expected: String) {
        if (actual != expected) throw ApiException(409, "观看身份已变化，请重新打开页面", "online_state_scope_changed")
    }
    suspend fun sources(): OnlineSources = read("GET", "/api/online/catalog/sources", parser = OnlineSources::fromJson)
    suspend fun search(sourceId: String, q: String, page: Int = 1): OnlineSearchPage {
        checkedOnlineSource(sourceId); require(q.length <= 100 && page in 1..1000)
        return read("GET", queryPath("/api/online/catalog/search", linkedMapOf("source_id" to sourceId, "q" to q, "page" to page))) {
            OnlineSearchPage.fromJson(it).also { result -> require(result.source.id == sourceId && result.page == page) }
        }
    }
    suspend fun detail(sourceId: String, externalId: String): OnlineDetail {
        checkedOnlineSource(sourceId)
        val selected = checkedOnlineCatalogExternal(externalId)
        return read("GET", queryPath("/api/online/catalog/detail", linkedMapOf("source_id" to sourceId, "external_id" to selected))) {
            OnlineDetail.fromJson(it).also { result -> require(result.source.id == sourceId) }
        }
    }
    suspend fun createSession(identity: OnlineIdentity): OnlineDescriptor {
        val value = api.requestOnline("POST", "/api/online/sessions", identity.toJson())
        val returnedId = (value.opt("session_id") as? String)?.takeIf { ONLINE_OPAQUE.matches(it) }
        try {
            val result = OnlineDescriptor.fromJson(value, identity)
            api.absoluteUrl(result.mediaUrl)
            coroutineContext.ensureActive()
            return result
        } catch (error: Exception) {
            // A valid returned ID belongs to this creation response. Never use
            // an upstream URL or arbitrary path from a rejected descriptor.
            if (returnedId != null) withContext(NonCancellable) {
                withTimeoutOrNull(2000) { try { deleteSession(returnedId) } catch (_: Exception) { } }
            }
            if (error is CancellationException) throw error
            throw ApiException(0, "服务器返回了无法识别的在线数据", "invalid_online_response")
        }
    }
    suspend fun deleteSession(sessionId: String) {
        checkedOnlineSession(sessionId)
        read("DELETE", "/api/online/sessions/$sessionId") { Unit }
    }
    suspend fun library(section: String = "continue", offset: Int = 0, limit: Int = 20): OnlineLibrary {
        require(section in setOf("continue", "favorites", "history") && offset in 0..1000 && limit in 1..100)
        return read("GET", queryPath("/api/online/state/library", linkedMapOf("section" to section, "limit" to limit, "offset" to offset)), parser = OnlineLibrary::fromJson)
    }
    suspend fun itemState(identity: OnlineIdentity): OnlineItemState = itemState(identity.itemId, identity.episodeId, identity.lineId).also {
        require(it.selectedProgress == null || it.selectedProgress.identity == identity)
    }
    /** Optional records cannot hold up media startup, but access denial still does. */
    suspend fun playbackState(identity: OnlineIdentity): OnlineItemState? = playbackState(identity, 4000)

    internal suspend fun playbackState(identity: OnlineIdentity, timeoutMillis: Long): OnlineItemState? {
        require(timeoutMillis in 1L..4000L)
        return withTimeoutOrNull(timeoutMillis) {
            try { itemState(identity) }
            catch (error: CancellationException) { throw error }
            catch (error: ApiException) {
                if (error.status == 401 || error.status == 403) throw error
                null
            }
            catch (_: Exception) { null }
        }
    }
    suspend fun itemState(itemId: String, episodeId: String? = null, lineId: String? = null): OnlineItemState {
        checkedOnlineItem(itemId)
        if (episodeId != null) require(Regex("ep_[0-9a-f]{32}").matches(episodeId))
        if (lineId != null) require(Regex("ln_[0-9a-f]{32}").matches(lineId))
        return read("GET", queryPath("/api/online/state/items/$itemId", linkedMapOf("episode_id" to episodeId, "line_id" to lineId))) {
            OnlineItemState.fromJson(it).also { result -> require(result.itemId == itemId) }
        }
    }
    suspend fun favorite(itemId: String, favorite: Boolean, scopeToken: String): OnlineFavoriteResult {
        checkedOnlineItem(itemId); checkedOnlineScope(scopeToken)
        return read("PUT", "/api/online/state/items/$itemId/favorite", JSONObject().put("favorite", favorite), scopeToken) {
            OnlineFavoriteResult.fromJson(it).also { result -> require(result.itemId == itemId); sameScope(result.scopeToken, scopeToken) }
        }
    }
    suspend fun deleteHistory(itemId: String, scopeToken: String): OnlineDeleteResult {
        checkedOnlineItem(itemId); checkedOnlineScope(scopeToken)
        return read("DELETE", "/api/online/state/items/$itemId/history", scopeToken = scopeToken, parser = OnlineDeleteResult::fromJson)
    }
    suspend fun claim(sessionId: String, expectedRevision: Long, scopeToken: String): OnlineClaim {
        checkedOnlineSession(sessionId); checkedOnlineScope(scopeToken); require(expectedRevision >= 0)
        return read("POST", "/api/online/state/progress/claim", JSONObject().put("session_id", sessionId)
            .put("expected_revision", expectedRevision), scopeToken) {
            OnlineClaim.fromJson(it).also { result -> sameScope(result.scopeToken, scopeToken) }
        }
    }
    suspend fun save(writerId: String, writerRevision: Long, seq: Long, position: Double, duration: Double?,
                     paused: Boolean, completed: Boolean, scopeToken: String): OnlineProgressResult {
        checkedOnlineSession(writerId); checkedOnlineScope(scopeToken)
        require(writerRevision > 0 && seq > 0 && position.isFinite() && position >= 0)
        require(duration == null || duration.isFinite() && duration > 0)
        val body = JSONObject().put("writer_id", writerId).put("writer_revision", writerRevision).put("seq", seq)
            .put("position", position).put("duration", duration ?: JSONObject.NULL).put("paused", paused).put("completed", completed)
        return read("POST", "/api/online/state/progress", body, scopeToken) {
            OnlineProgressResult.fromJson(it).also { result -> sameScope(result.scopeToken, scopeToken)
                require(result.progress.writerRevision == writerRevision) }
        }
    }
    suspend fun health(sourceId: String): OnlineHealth {
        checkedOnlineSource(sourceId)
        return read("GET", queryPath("/api/online/catalog/health", mapOf("source_id" to sourceId))) {
            OnlineHealth.fromJson(it).also { result -> require(result.sourceId == sourceId) }
        }
    }
}
