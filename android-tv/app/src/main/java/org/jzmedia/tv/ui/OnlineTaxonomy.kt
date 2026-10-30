package org.jzmedia.tv.ui

import org.jzmedia.tv.data.OnlineCategory

private val catalogCategoryId = Regex("[A-Za-z0-9_-]{1,128}")
private val commonChannels = listOf(
    "电影" to setOf("电影", "电影片"),
    "剧集" to setOf("剧集", "电视剧", "连续剧"),
    "动漫" to setOf("动漫", "动漫片"),
    "综艺" to setOf("综艺", "综艺片"),
    "短剧" to setOf("短剧", "短剧大全"),
)

internal data class OnlineTaxonomyChannel(val id: String, val name: String, val label: String,
                                          val children: List<OnlineCategory>)
internal data class OnlineTaxonomy(val categories: List<OnlineCategory>, val channels: List<OnlineTaxonomyChannel>,
                                   val rootByCategory: Map<String, String>)

/** Build shortcuts only from the source's real taxonomy; never invent aggregate categories. */
internal fun onlineTaxonomy(rows: List<OnlineCategory>): OnlineTaxonomy {
    val byId = linkedMapOf<String, OnlineCategory>()
    rows.take(256).forEach { row ->
        val name = row.name.trim()
        if (catalogCategoryId.matches(row.id) && name.isNotEmpty() && row.id !in byId) {
            val parent = row.parentId.takeIf { catalogCategoryId.matches(it) && it != row.id }.orEmpty()
            byId[row.id] = OnlineCategory(row.id, name.take(128), parent)
        }
    }
    // Missing parents and cycles are detached so every valid item remains reachable.
    byId.keys.toList().forEach { id ->
        val row = byId.getValue(id)
        if (row.parentId !in byId) byId[id] = row.copy(parentId = "")
    }
    byId.keys.toList().forEach { id ->
        val path = mutableListOf<String>()
        var current = id
        while (current.isNotEmpty()) {
            val repeated = path.indexOf(current)
            if (repeated >= 0) {
                path.drop(repeated).forEach { member -> byId[member] = byId.getValue(member).copy(parentId = "") }
                break
            }
            path.add(current)
            current = byId[current]?.parentId.orEmpty()
        }
    }
    val roots = byId.values.associate { category ->
        var current = category
        while (current.parentId.isNotEmpty()) current = byId.getValue(current.parentId)
        category.id to current.id
    }
    val channels = byId.values.filter { it.parentId.isEmpty() }.mapIndexed { index, row ->
        val rank = commonChannels.indexOfFirst { row.name in it.second }
        val label = if (rank >= 0) commonChannels[rank].first else row.name
        val descendants = byId.values.filter { it.id != row.id && roots[it.id] == row.id }
        Triple(if (rank >= 0) rank else commonChannels.size + index, index,
            OnlineTaxonomyChannel(row.id, row.name, label, descendants))
    }.sortedWith(compareBy({ it.first }, { it.second })).map { it.third }
    return OnlineTaxonomy(byId.values.toList(), channels, roots)
}

/** Keep the chosen root reachable even when it lies beyond the compact row. */
internal fun onlineVisibleChannels(tree: OnlineTaxonomy, selectedRootId: String = "", limit: Int = 5): List<OnlineTaxonomyChannel> {
    if (limit <= 0) return emptyList()
    val visible = tree.channels.take(limit).toMutableList()
    val selected = tree.channels.firstOrNull { it.id == selectedRootId }
    if (selected != null && visible.none { it.id == selected.id } && visible.isNotEmpty()) visible[visible.lastIndex] = selected
    return visible
}

/** In the all view, show a balanced sample of real subcategories across channels. */
internal fun onlineVisibleCategories(tree: OnlineTaxonomy, rootId: String = "", selectedId: String = "", limit: Int = 8): List<OnlineCategory> {
    if (limit <= 0) return emptyList()
    val channel = tree.channels.firstOrNull { it.id == rootId }
    val choices = if (channel != null) {
        if (channel.children.isEmpty()) emptyList()
        else listOf(tree.categories.first { it.id == channel.id }) + channel.children
    } else if (rootId.isEmpty()) {
        val result = mutableListOf<OnlineCategory>()
        var offset = 0
        while (result.size < tree.categories.size) {
            val next = tree.channels.mapNotNull { it.children.getOrNull(offset) }
            if (next.isEmpty()) break
            result.addAll(next)
            offset++
        }
        result
    } else emptyList()
    val visible = choices.take(limit).toMutableList()
    val selected = choices.firstOrNull { it.id == selectedId }
    if (selected != null && visible.none { it.id == selected.id } && visible.isNotEmpty()) visible[visible.lastIndex] = selected
    return visible
}

/** Switching a channel selects an actual category and may restore a previous choice. */
internal fun onlineChannelCategory(channel: OnlineTaxonomyChannel, rememberedId: String = ""): String =
    if (rememberedId == channel.id || channel.children.any { it.id == rememberedId }) rememberedId
    else channel.children.firstOrNull()?.id ?: channel.id

internal fun onlineTaxonomyLabel(tree: OnlineTaxonomy, id: String): String {
    val category = tree.categories.firstOrNull { it.id == id } ?: return "全部分类"
    return if (tree.channels.any { it.id == id && it.children.isNotEmpty() }) "${category.name}综合条目" else category.name
}

/** The full chooser contains the real parent too; it is not a synthetic all-children query. */
internal fun onlineAllCategoriesForChannel(tree: OnlineTaxonomy, rootId: String = ""): List<OnlineCategory> {
    if (rootId.isEmpty()) return tree.categories
    val channel = tree.channels.firstOrNull { it.id == rootId } ?: return emptyList()
    return listOfNotNull(tree.categories.firstOrNull { it.id == channel.id }) + channel.children
}
