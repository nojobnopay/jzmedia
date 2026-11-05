package org.jzmedia.tv.ui

import org.jzmedia.tv.data.*
import org.junit.Assert.*
import org.junit.Test

class OnlineCacheScreenStateTest {
    private val source = OnlineSource("src_" + "1".repeat(32), "Source", "maccms")
    private val item = OnlineCatalogItem("itm_" + "2".repeat(32), source.id, "tv", "Show")
    private val detail = OnlineDetail(source, item, "", "2026", emptyList())
    private val scope = "a".repeat(64)
    private fun page() = OnlineScreenState().apply {
        acceptSources(OnlineSources(true, listOf(source), scope))
        this.detail = this@OnlineCacheScreenStateTest.detail
        detailSourceId = source.id; detailExternalId = "show_1"; cacheItemId = this@OnlineCacheScreenStateTest.item.id
        cacheDetail = OnlineCacheDetail(this@OnlineCacheScreenStateTest.detail, "show_1", emptyList())
    }

    @Test fun disablingASourceKeepsItsLocalSnapshotAndFocus() {
        val page = page()
        page.detailUi.focus("detail:more")
        assertTrue(page.acceptSources(OnlineSources(true, listOf(source.copy(enabled = false)), scope)))
        assertEquals(detail, page.detail)
        assertNotNull(page.cacheDetail)
        assertEquals("detail:more", page.detailUi.pendingFocus)
        assertTrue(page.showingDetail)
    }

    @Test fun globalDisableOrViewingScopeChangeDiscardsTheCachedDetail() {
        for (sources in listOf(OnlineSources(false, listOf(source), scope),
            OnlineSources(true, listOf(source), "b".repeat(64)))) {
            val page = page()
            page.acceptSources(sources)
            assertNull(page.detail)
            assertNull(page.cacheDetail)
            assertFalse(page.showingDetail)
        }
    }

    @Test fun cacheListUsesItsOwnFocusAndScrollPositions() {
        val page = OnlineScreenState().apply {
            section = "cache"
            cacheLibrary = OnlineCacheLibrary(true, listOf(OnlineCacheItem(this@OnlineCacheScreenStateTest.item.id, source.id, "Show", "Source", 1, 3)))
        }
        val key = page.contentKey
        page.savePosition(key, OnlinePagePosition(2, 20, "online:cache:${item.id}"))
        assertEquals(2, onlineFocusItemIndex(page, "online:cache:${item.id}"))
        assertEquals(1, onlineFocusItemIndex(page, "online:refresh-cache"))
        assertEquals(2, onlineFocusItemIndex(page, "online:retry-library"))
        page.cacheItemId = item.id
        assertTrue(page.showingDetail)
        page.cacheItemId = ""
        assertEquals(key, page.contentKey)
        assertEquals("online:cache:${item.id}", page.position(key).focus)
        assertEquals(20, page.position(key).offset)
    }
}
