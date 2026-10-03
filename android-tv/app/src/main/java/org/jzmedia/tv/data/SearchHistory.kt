package org.jzmedia.tv.data

import android.content.Context
import android.util.Log
import java.security.MessageDigest
import java.util.Locale
import org.json.JSONArray
import org.json.JSONObject

const val SEARCH_HISTORY_LIMIT = 12
data class RecentActor(val key: String, val name: String)

/** Small local-only histories. Scope keys never contain tokens or raw server URLs. */
class SearchHistoryStore(
    private val read: (String) -> String?,
    private val write: (String, String?) -> Unit,
    private val onInvalid: (Exception) -> Unit = {},
) {
    constructor(context: Context) : this(
        { key -> context.getSharedPreferences("tv_search_history", Context.MODE_PRIVATE).getString(key, null) },
        { key, value -> context.getSharedPreferences("tv_search_history", Context.MODE_PRIVATE).edit().let { edit ->
            if (value == null) edit.remove(key) else edit.putString(key, value)
            edit.apply()
        } },
        { error -> Log.w("JzSearchHistory", "Discarding invalid local history (${error.javaClass.simpleName})") },
    )

    private fun scope(server: String, library: Long): String {
        val canonical = JzApi.normalizeServerUrl(server)
        val bytes = MessageDigest.getInstance("SHA-256").digest("$canonical\u0000${library.coerceAtLeast(0)}".toByteArray(Charsets.UTF_8))
        return "v1:" + bytes.joinToString("") { "%02x".format(it.toInt() and 255) }
    }

    private fun queryKey(server: String, library: Long, mode: String): String =
        scope(server, library) + if (mode == "actor") ":actor-query" else ":title-query"

    private fun entries(key: String): JSONArray {
        val value = read(key) ?: return JSONArray()
        return try { JSONArray(value) }
        catch (error: Exception) { onInvalid(error); write(key, null); JSONArray() }
    }

    fun queries(server: String, library: Long, mode: String): List<String> {
        val rows = entries(queryKey(server, library, mode))
        return (0 until rows.length()).mapNotNull { rows.opt(it) as? String }
            .map { appendSearchInput("", it).trim() }.filter { it.isNotEmpty() }
            .distinctBy { it.lowercase(Locale.ROOT) }.take(SEARCH_HISTORY_LIMIT)
    }

    fun recordQuery(server: String, library: Long, mode: String, query: String) {
        val normalized = appendSearchInput("", query).trim()
        if (normalized.isEmpty()) return
        val rows = (listOf(normalized) + queries(server, library, mode))
            .distinctBy { it.lowercase(Locale.ROOT) }.take(SEARCH_HISTORY_LIMIT)
        write(queryKey(server, library, mode), JSONArray(rows).toString())
    }

    fun clearQueries(server: String, library: Long, mode: String) { write(queryKey(server, library, mode), null) }

    fun actors(server: String, library: Long): List<RecentActor> {
        val rows = entries(scope(server, library) + ":actors")
        return (0 until rows.length()).mapNotNull { index ->
            val row = rows.optJSONObject(index) ?: return@mapNotNull null
            val actorKey = row.text("key")
            val name = appendSearchInput("", row.text("name")).trim()
            if (actorKey.isBlank() || actorKey.length > 4096 || name.isEmpty()) null else RecentActor(actorKey, name)
        }.distinctBy { it.key }.take(SEARCH_HISTORY_LIMIT)
    }

    fun recordActor(server: String, library: Long, actor: RecentActor) {
        if (actor.key.isBlank() || actor.key.length > 4096 || actor.name.isBlank()) return
        val latest = actor.copy(name = appendSearchInput("", actor.name).trim())
        if (latest.name.isEmpty()) return
        val rows = (listOf(latest) + actors(server, library)).distinctBy { it.key }.take(SEARCH_HISTORY_LIMIT)
        write(scope(server, library) + ":actors", JSONArray(rows.map { JSONObject().put("key", it.key).put("name", it.name) }).toString())
    }

    fun clearActors(server: String, library: Long) { write(scope(server, library) + ":actors", null) }
}
