package org.jzmedia.tv.data

import org.json.JSONObject

/** The server owns files and task management; TV only reads snapshots and plays ready entries. */
data class OnlineCacheEntry(val identity: OnlineIdentity, val state: String) {
    companion object {
        fun fromJson(value: JSONObject) = OnlineCacheEntry(
            OnlineIdentity.fromJson(value.getJSONObject("identity")),
            value.getString("state").also { require(it in setOf("queued", "downloading", "paused", "failed", "ready", "deleting")) })
    }
}

data class OnlineCacheItem(val itemId: String, val sourceId: String, val title: String,
                           val sourceName: String, val readyCount: Int, val totalCount: Int) {
    companion object {
        fun fromJson(value: JSONObject): OnlineCacheItem {
            val ready = value.getInt("ready_count")
            val total = value.getInt("total_count")
            require(ready >= 0 && total >= ready)
            return OnlineCacheItem(checkedOnlineItem(value.getString("item_id")),
                checkedOnlineSource(value.getString("source_id")), value.getString("title").also { require(it.length <= 512) },
                value.getString("source_name").also { require(it.length <= 128) }, ready, total)
        }
    }
}

data class OnlineCacheLibrary(val enabled: Boolean, val items: List<OnlineCacheItem>) {
    companion object {
        fun fromJson(value: JSONObject): OnlineCacheLibrary {
            val rows = value.getJSONArray("items")
            require(rows.length() <= 8000)
            val items = (0 until rows.length()).map { OnlineCacheItem.fromJson(rows.getJSONObject(it)) }
            require(items.map { it.itemId }.distinct().size == items.size)
            return OnlineCacheLibrary(value.getBoolean("enabled"), items)
        }
    }
}

data class OnlineCacheDetail(val detail: OnlineDetail, val externalId: String, val entries: List<OnlineCacheEntry>) {
    fun isReady(identity: OnlineIdentity): Boolean = entries.any { it.identity == identity && it.state == "ready" }

    /** Keep missing episodes in their original positions; they cannot start an upstream request. */
    fun playableDetail(): OnlineDetail = detail.copy(lines = detail.lines.map { line ->
        line.copy(entries = line.entries.map { entry ->
            val ready = entry.identity?.let(::isReady) == true && entry.transport != null
            entry.copy(supported = ready, reason = if (ready) "" else "cache_not_ready")
        })
    })

    companion object {
        fun fromJson(value: JSONObject): OnlineCacheDetail {
            val detail = OnlineDetail.fromJson(value.getJSONObject("detail"))
            val rows = value.getJSONArray("entries")
            require(rows.length() <= 8000)
            val entries = (0 until rows.length()).map { OnlineCacheEntry.fromJson(rows.getJSONObject(it)) }
            require(entries.all { it.identity.itemId == detail.item.id && it.identity.sourceId == detail.source.id })
            require(entries.map { it.identity }.distinct().size == entries.size)
            return OnlineCacheDetail(detail, checkedOnlineCatalogExternal(value.getString("external_id")), entries)
        }
    }
}

data class OnlineOpenedDetail(val detail: OnlineDetail, val cache: OnlineCacheDetail? = null)
