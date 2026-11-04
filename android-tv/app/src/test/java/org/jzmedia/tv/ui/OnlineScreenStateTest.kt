package org.jzmedia.tv.ui

import org.jzmedia.tv.data.OnlineCatalogItem
import org.jzmedia.tv.data.OnlineCategory
import org.jzmedia.tv.data.OnlineDetail
import org.jzmedia.tv.data.OnlineEntry
import org.jzmedia.tv.data.OnlineIdentity
import org.jzmedia.tv.data.OnlineItemState
import org.jzmedia.tv.data.OnlineLine
import org.jzmedia.tv.data.OnlineProgress
import org.jzmedia.tv.data.OnlineSearchItem
import org.jzmedia.tv.data.OnlineSearchPage
import org.jzmedia.tv.data.OnlineSearchSession
import org.jzmedia.tv.data.OnlineSource
import org.jzmedia.tv.data.OnlineSources
import org.jzmedia.tv.data.ApiException
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test

class OnlineScreenStateTest {
    private val source = OnlineSource("src_" + "1".repeat(32), "Source", "maccms")
    private val item = OnlineCatalogItem("itm_" + "2".repeat(32), source.id, "tv", "Show")
    private fun searchWindow(items: List<OnlineSearchItem> = emptyList(), version: Int = 1,
                             found: Int = items.size, complete: Boolean = false) = OnlineSearchSession(
        "srch_" + "b".repeat(32), version, source, "a".repeat(64), "已提交片名",
        if (complete) "complete" else "partial", items, emptyList(), 100, found,
        if (complete) found else null, !complete, !complete, "", 1000)

    @Test fun changingSearchFiltersUsesTheSubmittedWordAndPreservesUnsubmittedDrafts() {
        val page = OnlineScreenState().apply {
            sourceId = source.id; query = "还没有搜索的新词"; categoryId = "13"; year = "2026"
            loadedSelection = OnlineCatalogSelection(source.id, "已提交片名", "13", "2026")
        }
        val category = page.categorySelection("2", "descendants")
        assertEquals("已提交片名", category.query)
        assertEquals("2026", category.year)
        assertEquals("descendants", category.categoryScope)
        assertEquals("还没有搜索的新词", page.query)
        val year = page.yearSelection("")
        assertEquals("已提交片名", year.query)
        assertEquals("13", year.categoryId)
        assertEquals("", year.year)
        assertEquals("", page.categorySelection("").categoryId)
    }

    @Test fun submittingAWordPreservesFiltersAndSearchRootsIncludeAllTheirDescendants() {
        val page = OnlineScreenState().apply {
            sourceId = source.id; query = " 新片名 "; categoryId = "2"; year = "2026"
            categories = listOf(OnlineCategory("2", "连续剧"), OnlineCategory("13", "国产剧", "2"))
        }
        assertEquals(OnlineCatalogSelection(source.id, "新片名", "2", "2026", "descendants"), page.submittedSelection(true))
        assertEquals(OnlineCatalogSelection(source.id, "新片名"), page.submittedSelection(false))
        page.query = ""
        assertEquals(OnlineCatalogSelection(source.id, categoryId = "2", year = "2026"), page.submittedSelection(true))
    }

    @Test fun progressiveWindowsAppendWithoutDuplicatesAndKeepTheSamePositionKey() {
        val first = OnlineSearchItem("one", "One", "", "movie", "")
        val next = OnlineSearchItem("two", "Two", "", "tv", "")
        val selection = OnlineCatalogSelection(source.id, "已提交片名", "2", categoryScope = "descendants")
        val page = OnlineScreenState().apply { sourceId = source.id; loadedSelection = selection }
        page.acceptSearchWindow(searchWindow(listOf(first), found = 2), selection)
        val key = page.contentKey
        val old = page.results!!.items
        page.acceptSearchWindow(searchWindow(listOf(next), version = 2, found = 2), selection, old)
        page.acceptSearchWindow(searchWindow(listOf(next), version = 2, found = 2), selection, old)
        assertEquals(listOf("one", "two"), page.results!!.items.map { it.externalId })
        assertEquals(key, page.contentKey)
        page.acceptSearchWindow(searchWindow(listOf(first), version = 1, found = 2), selection)
        assertEquals(listOf("one", "two"), page.results!!.items.map { it.externalId })
        assertNull(page.searchSession!!.total)
        assertEquals(0, page.results!!.pageCount)
    }

    @Test fun searchCapabilityDowngradeDiscardsAnOldProgressiveSession() {
        val current = source.copy(browseSupported = true, searchFiltersSupported = true, configurationToken = "a".repeat(64))
        val page = OnlineScreenState().apply {
            acceptSources(OnlineSources(true, listOf(current), "b".repeat(64)))
            searchSession = searchWindow()
        }
        assertTrue(page.acceptSources(OnlineSources(true, listOf(current.copy(searchFiltersSupported = false)), "b".repeat(64))))
        assertNull(page.searchSession)
        assertNull(page.loadedSelection)
    }

    @Test fun anUnreadOrPartialEmptyPrefixNeverClaimsThereAreNoSearchResults() {
        assertFalse(onlineSearchSessionEmptyLabel(searchWindow()).contains("没有找到"))
        assertTrue(onlineSearchSessionEmptyLabel(searchWindow().copy(scanned = 0)).contains("暂未读取"))
        assertTrue(onlineSearchSessionEmptyLabel(searchWindow().copy(canContinue = false, hasMore = false)).contains("缩小关键词"))
        assertTrue(onlineSearchSessionEmptyLabel(searchWindow(complete = true)).contains("没有找到"))
    }

    @Test fun anExpiredContinuationRebuildsOnlyOnceAndDiscardsThePreviousPrefix() = runBlocking {
        val expiry = OnlineSearchExpiryRecovery()
        var offset = 40
        var prefix = listOf("old")
        var rebuilt = 0
        // A cached GET can succeed after the fresh period, while POST /more expires.
        assertEquals("cached", expiry.read({ "cached" }, { error("unexpected rebuild") }))
        val result = expiry.read({ throw ApiException(410, "expired", "online_search_session_expired") }) {
            offset = 0; prefix = emptyList(); rebuilt++
            "new first window"
        }
        assertEquals("new first window", result)
        assertEquals(0, offset); assertTrue(prefix.isEmpty()); assertEquals(1, rebuilt)
        try {
            expiry.read({ throw ApiException(410, "expired again", "online_search_session_expired") }) { rebuilt++; "unexpected" }
            fail("a second expiry must remain an error")
        } catch (error: ApiException) { assertEquals(410, error.status) }
        assertEquals(1, rebuilt)
    }

    @Test fun progressiveLoadMoreTargetsTheFooterAndUnsupportedFiltersHaveNoFocusTarget() {
        val selection = OnlineCatalogSelection(source.id, "已提交片名")
        val page = OnlineScreenState().apply {
            sourceId = source.id; loadedSelection = selection
            sources = OnlineSources(true, listOf(source.copy(browseSupported = true, searchFiltersSupported = true)))
            acceptSearchWindow(searchWindow(listOf(OnlineSearchItem("one", "One", "", "tv", ""))), selection)
        }
        assertEquals(4, onlineFocusItemIndex(page, "online:more-search"))
        assertEquals(4, onlineFocusItemIndex(page, "online:refresh-search"))
        page.sources = OnlineSources(true, listOf(source.copy(browseSupported = true)))
        assertNull(onlineFocusItemIndex(page, "online:root:all"))
        assertNull(onlineFocusItemIndex(page, "online:year:all"))
    }
    private val line = "ln_" + "3".repeat(32)
    private val otherLine = "ln_" + "4".repeat(32)
    private val identity = OnlineIdentity(source.id, item.id, "episode", "ep_" + "5".repeat(32), line)
    private val entry = OnlineEntry("Episode", identity, "mp4", true, "")
    private val scope = "a".repeat(64)
    private fun progress(selected: OnlineIdentity = identity, completed: Boolean = false) =
        OnlineProgress(selected, 18.5, 24.0, true, completed, 100.0, 7)
    private fun state(selected: OnlineProgress? = progress(), available: Boolean = true) =
        OnlineItemState(item.id, true, selected, selected, available, "", 0, scope)
    private fun detail() = OnlineDetail(source, item, "", "2026", listOf(OnlineLine(line, "Main", listOf(entry))))

    @Test fun selectedLineRestoresOnlyItsOwnPositionAndWriterWithTheFrozenScope() {
        val saved = onlinePlaybackSelection(entry, state())!!
        assertEquals(18.5, saved.position, 0.0)
        assertEquals(7, saved.writerRevision)
        assertEquals(scope, saved.scopeToken)
        val other = onlinePlaybackSelection(entry, state(progress(identity.copy(lineId = otherLine))))!!
        assertEquals(0.0, other.position, 0.0)
        assertEquals(0, other.writerRevision)
        assertEquals(scope, other.scopeToken)
    }

    @Test fun replayStartsAtZeroButKeepsTheWriterRevisionRequiredForTheNewClaim() {
        val replay = onlinePlaybackSelection(entry, state(progress(completed = true)))!!
        assertEquals(0.0, replay.position, 0.0)
        assertEquals(7, replay.writerRevision)
    }

    @Test fun missingOptionalRecordsDoNotBlockMediaButUnavailableOrWrongItemsDo() {
        assertEquals(OnlinePlaybackSelection(0.0, "", 0), onlinePlaybackSelection(entry, null))
        assertNull(onlinePlaybackSelection(entry, state(available = false)))
        assertNull(onlinePlaybackSelection(entry.copy(supported = false), state()))
        assertNull(onlinePlaybackSelection(entry, state().copy(itemId = "itm_" + "9".repeat(32))))
    }

    @Test fun resumeNeverQuietlySwitchesLinesOrRestoresARemovedEpisode() {
        assertEquals(entry, onlineResumeEntry(detail(), line, state()))
        assertNull(onlineResumeEntry(detail(), otherLine, state()))
        assertNull(onlineResumeEntry(detail(), line, state(progress(identity.copy(episodeId = "ep_" + "6".repeat(32))))))
    }

    @Test fun recordResumeRestoresTheExactLineAndTheGroupContainingEpisode105() {
        val identities = (1..110).map { number -> identity.copy(episodeId = "ep_" + "%032x".format(number), lineId = otherLine) }
        val entries = identities.mapIndexed { index, value -> OnlineEntry("第${index + 1}集", value, "hls", true, "") }
        val selected = identities[104]
        val program = detail().copy(lines = listOf(OnlineLine(line, "First", listOf(entry)), OnlineLine(otherLine, "Second", entries)))
        val ui = OnlineDetailUiState()
        assertEquals(entries[104], onlinePrepareRecordResume(ui, program, selected))
        assertEquals(2, ui.groupIndex)
        assertEquals(onlineEpisodeKey(otherLine, entries[104], 104), ui.pendingFocus)
        ui.bind(program.item.id, otherLine)
        assertEquals(2, ui.groupIndex)
        assertNull(onlinePrepareRecordResume(ui, program, selected.copy(episodeId = "ep_" + "f".repeat(32))))
    }

    @Test fun lazyFocusTargetsFollowResultsAndNoticeWhileDialogKeyboardAddsNoRow() {
        val page = OnlineScreenState()
        page.results = OnlineSearchPage(source, listOf(
            OnlineSearchItem("one", "One", "", "movie", ""), OnlineSearchItem("two", "Two", "", "tv", "")), 1, 2, 4)
        assertEquals(2, onlineFocusItemIndex(page, "online:result:two"))
        page.keyboard = true; page.notice = "A saving notice"
        assertEquals(3, onlineFocusItemIndex(page, "online:result:two"))
        assertNull(onlineFocusItemIndex(page, "online:result:removed"))
        page.detailSourceId = source.id; page.detailExternalId = "one"; page.detail = detail(); page.lineId = line
        assertEquals(2, onlineFocusItemIndex(page, "online:retry-detail"))
        assertNull(onlineFocusItemIndex(page, "online:entry:${identity.episodeId}"))
    }

    @Test fun gridRestorationFindsTheCorrectLazyRowAtDifferentFontSizes() {
        val page = OnlineScreenState()
        page.sourceId = source.id
        page.sources = OnlineSources(true, listOf(source.copy(browseSupported = true, yearFilterSupported = true)))
        page.results = OnlineSearchPage(source, (0..10).map { OnlineSearchItem("id$it", "Item $it", "", "movie", "") }, 2, 3, 24)
        page.resultColumns = 5
        assertEquals(5, onlineFocusItemIndex(page, "online:result:id10"))
        assertEquals(6, onlineFocusItemIndex(page, "online:next-search"))
        page.resultColumns = 3
        assertEquals(6, onlineFocusItemIndex(page, "online:result:id10"))
        assertEquals(7, onlineFocusItemIndex(page, "online:next-search"))
        assertEquals(1, onlineFocusItemIndex(page, "online:category:41"))
        assertEquals(1, onlineFocusItemIndex(page, "online:year:2026"))
        assertEquals(0, onlineFocusItemIndex(page, "online:source-button"))
    }

    @Test fun yearStatusRowsKeepResultFocusPositionsWithoutInventingUnsupportedFilterTargets() {
        val page = OnlineScreenState()
        page.sourceId = source.id
        page.results = OnlineSearchPage(source, listOf(OnlineSearchItem("one", "One", "", "movie", "")), 1, 2, 2)
        page.sources = OnlineSources(true, listOf(source))

        assertEquals(3, onlineFocusItemIndex(page, "online:result:one"))
        assertEquals(4, onlineFocusItemIndex(page, "online:next-search"))
        assertEquals(2, onlineFocusItemIndex(page, "online:retry-search"))
        assertNull(onlineFocusItemIndex(page, "online:root:all"))
        assertNull(onlineFocusItemIndex(page, "online:year:all"))

        page.sources = OnlineSources(true, listOf(source.copy(yearFilterSupported = true)))
        assertNull(onlineFocusItemIndex(page, "online:year:2026"))
        page.sources = OnlineSources(true, listOf(source.copy(browseSupported = true)))
        assertEquals(1, onlineFocusItemIndex(page, "online:root:all"))
        assertNull(onlineFocusItemIndex(page, "online:year:2026"))
        page.sources = OnlineSources(true, listOf(source.copy(browseSupported = true, yearFilterSupported = true)))
        assertEquals(1, onlineFocusItemIndex(page, "online:year:2026"))
        assertEquals(3, onlineFocusItemIndex(page, "online:result:one"))
    }

    @Test fun catalogueReturnPositionIsIsolatedBySourceFilterAndPage() {
        val page = OnlineScreenState()
        page.sourceId = source.id
        page.loadedSelection = OnlineCatalogSelection(source.id, categoryId = "41", year = "2026")
        page.searchPage = 2
        val savedKey = page.contentKey
        val saved = OnlinePagePosition(8, 12, "online:result:show")
        page.savePosition(savedKey, saved)
        page.query = "Unsubmitted input"
        assertEquals(savedKey, page.contentKey)
        page.searchPage = 3
        assertNotEquals(savedKey, page.contentKey)
        assertEquals(OnlinePagePosition(), page.position(page.contentKey))
        page.searchPage = 2
        page.detailSourceId = source.id; page.detailExternalId = "show"
        assertNotEquals(savedKey, page.contentKey)
        page.detailExternalId = ""
        assertEquals(saved, page.position(page.contentKey))
        page.loadedSelection = OnlineCatalogSelection(source.id, categoryId = "42")
        assertNotEquals(savedKey, page.contentKey)
    }

    @Test fun pendingFocusCannotOverrideANewerRemoteOrNavigationSelection() {
        val ticket = OnlineFocusTicket(4, "online:root:tv", 9)
        assertTrue(ticket.isCurrent(4, "online:root:tv", 9))
        assertFalse(ticket.isCurrent(5, "online:root:tv", 9))
        assertFalse(ticket.isCurrent(4, "nav:online", 10))
        // Returning to the same key does not revive the old ticket.
        assertFalse(ticket.isCurrent(4, "online:root:tv", 11))
    }

    @Test fun categoryLabelsKeepTheSourceHierarchyAndBoundBrokenParentChains() {
        val categories = listOf(OnlineCategory("1", "电视剧"), OnlineCategory("2", "国产剧", "1"),
            OnlineCategory("3", "年代剧", "2"), OnlineCategory("4", "孤立项", "missing"))
        assertEquals("电视剧 / 国产剧 / 年代剧", onlineCategoryLabel(categories, "3"))
        assertEquals("孤立项", onlineCategoryLabel(categories, "4"))
        assertEquals("全部分类", onlineCategoryLabel(categories, "unknown"))
        assertEquals("B / A", onlineCategoryLabel(listOf(OnlineCategory("a", "A", "b"), OnlineCategory("b", "B", "a")), "a"))
    }

    private fun loadedCatalogue(): OnlineScreenState = OnlineScreenState().apply {
        val current = source.copy(browseSupported = true, yearFilterSupported = true, configurationToken = "1".repeat(64))
        acceptSources(OnlineSources(true, listOf(current), "a".repeat(64)))
        searchPage = 7; searching = true; searchError = "Old failure"
        categoryId = "41"; year = "2026"; yearInput = "2026"
        loadedSelection = OnlineCatalogSelection(source.id, categoryId = categoryId, year = year)
        results = OnlineSearchPage(current, listOf(OnlineSearchItem("one", "One", "", "tv", "")), 7, 8, 80)
        cataloguePending = false
    }

    @Test fun unavailableSourceFallsBackToTheFirstPageWithoutOldFiltersOrBusyState() {
        val page = loadedCatalogue()
        val replacement = source.copy(id = "src_" + "9".repeat(32), browseSupported = true)
        assertTrue(page.acceptSources(OnlineSources(true, listOf(replacement), "a".repeat(64))))
        assertEquals(replacement.id, page.sourceId)
        assertEquals(1, page.searchPage)
        assertFalse(page.searching)
        assertEquals("", page.searchError)
        assertEquals("", page.year)
        assertEquals("", page.categoryId)
        assertNull(page.loadedSelection)
        assertTrue(page.cataloguePending)
        assertEquals("", page.categoryRoot)
        assertTrue(page.channelSelections.isEmpty())
    }

    @Test fun sourceCapabilityDowngradesDiscardUnusableYearAndDirectorySelection() {
        val page = loadedCatalogue()
        val current = page.sources!!
        assertTrue(page.acceptSources(current.copy(items = listOf(current.items.single().copy(yearFilterSupported = false)))))
        assertEquals("", page.year); assertEquals("", page.yearInput)
        assertEquals(1, page.searchPage); assertNull(page.results)
        assertNull(page.loadedSelection)
    }

    @Test fun authoritativeReadsKeepUnchangedPagesButRetireChangedProcessOrConfigurationBindings() {
        val page = loadedCatalogue()
        val current = page.sources!!
        assertFalse(page.acceptSources(current))
        assertNotNull(page.results); assertEquals(7, page.searchPage)
        assertFalse(page.cataloguePending)
        assertTrue(page.acceptSources(current.copy(scopeToken = "b".repeat(64))))
        assertNull(page.results); assertTrue(page.cataloguePending)
        val configured = loadedCatalogue()
        assertTrue(configured.acceptSources(current.copy(items = listOf(current.items.single().copy(configurationToken = "2".repeat(64))))))
        assertNull(configured.results); assertEquals(1, configured.searchPage)
    }

    @Test fun leavingDuringALoadClearsBusyAndSchedulesOnlyTheInterruptedSelectionForReturn() {
        val page = loadedCatalogue()
        val selected = page.loadedSelection
        page.interruptCatalogue()
        assertFalse(page.searching)
        assertTrue(page.cataloguePending)
        assertEquals(selected, page.loadedSelection)
        page.cataloguePending = false
        page.interruptCatalogue()
        assertFalse(page.cataloguePending)
    }

    @Test fun oldSearchOnlySourcesKeepSearchWithoutInventingDirectoryFilters() {
        val page = OnlineScreenState()
        page.acceptSources(OnlineSources(true, listOf(source)))
        assertEquals(0, onlineFocusItemIndex(page, "online:search"))
        assertNull(onlineFocusItemIndex(page, "online:category:41"))
        page.acceptSources(OnlineSources(true, listOf(source.copy(browseSupported = true))))
        assertEquals(1, onlineFocusItemIndex(page, "online:category:41"))
    }

    @Test fun leavingTheOnlinePageDuringALoadRestartsItOnTheNextMount() {
        val page = loadedCatalogue()
        val ticket = page.attach()
        page.searching = true; page.cataloguePending = false
        page.detach(ticket)
        assertFalse(page.searching)
        assertTrue(page.cataloguePending)
        val next = page.attach()
        assertFalse(page.current(ticket))
        assertTrue(page.current(next))
        assertTrue(page.cataloguePending)
    }

    @Test fun remoteAndImeInputKeepsUnicodeCharactersWholeAndDropsControlKeys() {
        val character = String(Character.toChars(0x1F3AC))
        assertEquals("电影AB", onlineSearchInput("电影\nA\tB"))
        val value = onlineSearchInput("a".repeat(99) + character)
        assertEquals("a".repeat(99), value)
        assertFalse(value.any(Character::isSurrogate))
        assertEquals(character.repeat(50), onlineSearchInput(character.repeat(51)))
    }
}
