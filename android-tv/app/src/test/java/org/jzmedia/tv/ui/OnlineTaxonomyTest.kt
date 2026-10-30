package org.jzmedia.tv.ui

import org.jzmedia.tv.data.OnlineCategory
import org.junit.Assert.*
import org.junit.Test

class OnlineTaxonomyTest {
    private val rows = listOf(
        OnlineCategory("sport", "体育赛事"), OnlineCategory("short", "短剧大全"),
        OnlineCategory("movie", "电影片"), OnlineCategory("tv", "连续剧"), OnlineCategory("anime", "动漫片"),
        OnlineCategory("variety", "综艺片"), OnlineCategory("action", "动作片", "movie"),
        OnlineCategory("comedy", "喜剧片", "movie"), OnlineCategory("domestic", "国产剧", "tv"),
        OnlineCategory("special", "特别篇", "domestic"), OnlineCategory("urban", "都市脑洞", "short"),
    )

    @Test fun emptyFlatAndInvalidRowsNeverInventCategories() {
        assertTrue(onlineTaxonomy(emptyList()).channels.isEmpty())
        val tree = onlineTaxonomy(listOf(OnlineCategory("one", "电影"), OnlineCategory("two", "剧集", "missing"),
            OnlineCategory("one", "重复"), OnlineCategory("../bad", "不安全"), OnlineCategory("bad\n", "换行"),
            OnlineCategory("blank", "  ")))
        assertEquals(listOf("one", "two"), tree.categories.map { it.id })
        assertEquals(listOf("one", "two"), tree.channels.map { it.id })
        assertEquals("two", onlineChannelCategory(tree.channels[1]))
        assertEquals("电影", onlineTaxonomyLabel(tree, "one"))
        assertTrue(onlineVisibleCategories(tree).isEmpty())
    }

    @Test fun sourceAliasesOrderChannelsAndKeepUnknownLateSelectionReachable() {
        val tree = onlineTaxonomy(rows)
        assertEquals(listOf("电影", "剧集", "动漫", "综艺", "短剧"), onlineVisibleChannels(tree).map { it.label })
        assertEquals(listOf("movie", "tv", "anime", "variety", "sport"), onlineVisibleChannels(tree, "sport").map { it.id })
        assertEquals("电影片", tree.categories.first { it.id == "movie" }.name)
    }

    @Test fun descendantsAndRealParentEntriesHaveAccurateLabelsAndStableRequests() {
        val tree = onlineTaxonomy(rows)
        val tv = tree.channels.first { it.id == "tv" }
        assertEquals(listOf("domestic", "special"), tv.children.map { it.id })
        assertEquals("tv", tree.rootByCategory["special"])
        assertEquals("连续剧综合条目", onlineTaxonomyLabel(tree, "tv"))
        assertEquals("特别篇", onlineTaxonomyLabel(tree, "special"))
        assertEquals("domestic", onlineChannelCategory(tv))
        assertEquals("special", onlineChannelCategory(tv, "special"))
        assertEquals("tv", onlineChannelCategory(tv, "tv"))
        assertEquals("domestic", onlineChannelCategory(tv, "action"))
        assertEquals(listOf("tv", "domestic", "special"), onlineAllCategoriesForChannel(tree, "tv").map { it.id })
    }

    @Test fun allViewBalancesActualChildrenAcrossChannels() {
        val tree = onlineTaxonomy(rows)
        assertEquals(listOf("action", "domestic", "urban", "comedy", "special"),
            onlineVisibleCategories(tree).map { it.id })
        assertTrue(onlineAllCategoriesForChannel(tree).all { original -> rows.any { it.id == original.id } })
        assertEquals("全部分类", onlineTaxonomyLabel(tree, "does-not-exist"))
    }

    @Test fun selectedLateChildStaysVisibleAndParentIsAnExplicitEntry() {
        val tree = onlineTaxonomy(listOf(OnlineCategory("root", "电影")) +
            (1..12).map { OnlineCategory("c$it", "分类$it", "root") })
        assertEquals(listOf("root", "c1", "c2", "c3", "c4", "c5", "c6", "c12"),
            onlineVisibleCategories(tree, "root", "c12").map { it.id })
        assertEquals("c12", onlineVisibleCategories(tree, selectedId = "c12").last().id)
        assertEquals("电影综合条目", onlineTaxonomyLabel(tree, "root"))
        assertEquals(13, onlineAllCategoriesForChannel(tree, "root").size)
        assertTrue(onlineVisibleChannels(tree, limit = 0).isEmpty())
        assertTrue(onlineVisibleCategories(tree, limit = 0).isEmpty())
    }

    @Test fun missingSelfAndCyclicParentsCannotLoseRowsOrLoop() {
        val tree = onlineTaxonomy(listOf(OnlineCategory("a", "A", "b"), OnlineCategory("b", "B", "a"),
            OnlineCategory("child", "Child", "a"), OnlineCategory("self", "Self", "self"),
            OnlineCategory("orphan", "Orphan", "missing")))
        assertEquals(setOf("a", "b", "child", "self", "orphan"), tree.rootByCategory.keys)
        assertEquals("a", tree.rootByCategory["a"])
        assertEquals("b", tree.rootByCategory["b"])
        assertEquals("a", tree.rootByCategory["child"])
        assertEquals("self", tree.rootByCategory["self"])
        assertEquals("orphan", tree.rootByCategory["orphan"])
    }

    @Test fun categoryBudgetAndOpaqueIdsAreRespected() {
        val tree = onlineTaxonomy((1..300).map { OnlineCategory("id-$it", "分类_$it") })
        assertEquals(256, tree.categories.size)
        assertTrue(tree.categories.any { it.id == "id-256" })
        assertFalse(tree.categories.any { it.id == "id-257" })
        assertEquals(emptyList<OnlineCategory>(), onlineAllCategoriesForChannel(tree, "../../x"))
    }
}
