package org.jzmedia.tv.ui

import org.jzmedia.tv.data.OnlineEntry
import org.jzmedia.tv.data.OnlineIdentity
import org.jzmedia.tv.data.OnlineCatalogItem
import org.jzmedia.tv.data.OnlineDetail
import org.jzmedia.tv.data.OnlineItemState
import org.jzmedia.tv.data.OnlineLine
import org.jzmedia.tv.data.OnlineProgress
import org.jzmedia.tv.data.OnlineSource
import org.junit.Assert.*
import org.junit.Test

class OnlineDetailScreenTest {
    private val source = "src_" + "1".repeat(32)
    private val item = "itm_" + "2".repeat(32)
    private val line = "ln_" + "3".repeat(32)
    private val otherLine = "ln_" + "4".repeat(32)

    private fun entry(index: Int, supported: Boolean = true): OnlineEntry {
        val identity = OnlineIdentity(source, item, "episode", "ep_" + index.toString(16).padStart(32, '0'), line)
        return OnlineEntry(if (index == 51) "番外 · 特别长的标题" else "第${index + 1}集", identity,
            if (supported) "hls" else null, supported, if (supported) "" else "host_not_allowed")
    }

    @Test fun compactHeroAndInitialFocusKeepTheEpisodeGridNearTheFirstViewport() {
        assertEquals(120f, onlineDetailPosterWidthDp(800f), 0f)
        assertEquals(96f, onlineDetailPosterWidthDp(600f), 0f)
        assertEquals("detail:play", onlineDetailInitialFocusKey(true, true, false))
        assertEquals("detail:more", onlineDetailInitialFocusKey(false, true, false))
        assertEquals("detail:more", onlineDetailInitialFocusKey(true, false, false))
        assertEquals("detail:more", onlineDetailInitialFocusKey(true, true, true))
    }

    @Test fun restoredEpisodeScrollReservesTheStickyHeaderButOtherActionsDoNot() {
        assertEquals(-76f, onlineDetailScrollDistance(0f, 56f, 500f, 60f, 16f, "detail:entry:line:episode"), 0f)
        assertEquals(-76f, onlineDetailScrollDistance(0f, 56f, 500f, 60f, 16f, "detail:unavailable:line:51"), 0f)
        assertEquals(-16f, onlineDetailScrollDistance(0f, 56f, 500f, 60f, 16f, "detail:group:1"), 0f)
        assertEquals(0f, onlineDetailScrollDistance(100f, 56f, 500f, 60f, 16f, "detail:entry:line:episode"), 0f)
        assertEquals(-44f, onlineDetailScrollDistance(0f, 56f, 100f, 80f, 16f, "detail:entry:line:episode"), 0f)
    }

    @Test fun originalRangesAreBoundedAndUnavailableEntriesKeepTheirPositions() {
        assertTrue(onlineEpisodeGroups(emptyList(), false).isEmpty())
        assertEquals(listOf("1–1"), onlineEpisodeGroups(listOf(entry(0)), false).map { it.label })
        assertEquals(listOf("1–50"), onlineEpisodeGroups((0 until 50).map { entry(it) }, false).map { it.label })
        val entries = (0 until 51).map { entry(it, supported = it != 49) }
        val groups = onlineEpisodeGroups(entries, false)
        assertEquals(listOf("1–50", "51–51"), groups.map { it.label })
        assertEquals(49, groups.first().indices.last())
        assertFalse(entries[groups.first().indices.last()].supported)
        assertEquals("媒体主机尚未加入此来源允许列表", onlineEntryUnavailableReason(entries[49]))
        assertEquals("当前线路不支持直接播放，请选择其他线路", onlineEntryUnavailableReason(entries[49].copy(reason = "other")))
    }

    @Test fun reversingChangesDisplayOrderWithoutRenumberingSourceGroupsOrLabels() {
        val entries = (0 until 783).map { entry(it) }
        val descending = onlineEpisodeGroups(entries, true)
        assertEquals(16, descending.size)
        assertEquals("751–783", descending.first().label)
        assertEquals(15, descending.first().index)
        assertEquals(782, descending.first().indices.first())
        assertEquals(750, descending.first().indices.last())
        assertEquals("1–50", descending.last().label)
        assertEquals(49, descending.last().indices.first())
        assertEquals(0, descending.last().indices.last())
        assertEquals("番外 · 特别长的标题", entries[51].label)
    }

    @Test fun locatingSavedEntryRequiresTheExactSupportedLineIdentity() {
        val entries = (0 until 52).map { entry(it, supported = it != 50) }
        assertEquals(51, onlineEpisodeIndex(entries, entries[51].identity))
        assertEquals(-1, onlineEpisodeIndex(entries, entries[50].identity))
        assertEquals(-1, onlineEpisodeIndex(entries, entries[51].identity!!.copy(lineId = otherLine)))
        assertEquals(-1, onlineEpisodeIndex(entries, null))
        assertEquals(1, onlineEpisodeIndex(entries, entries[51].identity) / ONLINE_EPISODE_GROUP_SIZE)
    }

    @Test fun focusKeysUseStableIdentityAndUnavailableRowsRemainDistinct() {
        val selected = entry(3)
        assertEquals(onlineEpisodeKey(line, selected, 3), onlineEpisodeKey(line, selected, 7))
        assertNotEquals(onlineEpisodeKey(line, selected, 3), onlineEpisodeKey(otherLine, selected, 3))
        val unsupported = OnlineEntry("番外", null, null, false, "invalid")
        assertNotEquals(onlineEpisodeKey(line, unsupported, 1), onlineEpisodeKey(line, unsupported, 2))
    }

    @Test fun groupAndFocusPersistAcrossPlaybackButResetForNewContentAndLines() {
        val ui = OnlineDetailUiState()
        ui.bind(item, line)
        ui.selectGroup(4); ui.updateReversed(true); ui.focused("detail:entry:$line:ep_id")
        ui.scrollIndex = 9; ui.scrollOffset = 22
        ui.bind(item, line)
        assertEquals(4, ui.groupIndex); assertTrue(ui.reversed)
        assertEquals("detail:entry:$line:ep_id", ui.lastFocusKey)
        assertEquals(9, ui.scrollIndex); assertEquals(22, ui.scrollOffset)
        ui.bind(item, otherLine)
        assertEquals(0, ui.groupIndex); assertTrue(ui.reversed)
        val target = "detail:entry:$otherLine:ep_id"
        ui.selectGroup(1); ui.focus(target)
        // The recomposition after the parent applies the selected line must
        // not reset the group or discard the explicit saved-entry focus.
        ui.bind(item, otherLine)
        assertEquals(1, ui.groupIndex); assertEquals(target, ui.pendingFocus)
        ui.bind("itm_" + "5".repeat(32), line)
        assertEquals(0, ui.groupIndex); assertFalse(ui.reversed)
        assertEquals("", ui.lastFocusKey); assertEquals(0, ui.scrollIndex)
    }

    private fun detailWithLastProgress(): Pair<OnlineDetail, OnlineItemState> {
        val saved = entry(51).copy(identity = entry(51).identity!!.copy(lineId = otherLine))
        val detail = OnlineDetail(OnlineSource(source, "Source", "maccms"),
            OnlineCatalogItem(item, source, "tv", "Show"), "", "2026",
            listOf(OnlineLine(line, "First", listOf(entry(0))), OnlineLine(otherLine, "Second", listOf(saved))))
        val progress = OnlineProgress(saved.identity!!, 30.0, 300.0, true, false, 100.0, 1)
        return detail to OnlineItemState(item, false, progress, null, true, "", 0, "a".repeat(64))
    }

    @Test fun lastProgressSuggestsItsRealPlayableLineOnlyBeforeUserActionOrPlayback() {
        val (detail, itemState) = detailWithLastProgress()
        assertEquals(otherLine, onlineResumeLineCandidate(detail, itemState, line, starting = false, userInteracted = false))
        assertNull(onlineResumeLineCandidate(detail, itemState, otherLine, starting = false, userInteracted = false))
        assertNull(onlineResumeLineCandidate(detail, itemState, line, starting = true, userInteracted = false))
        assertNull(onlineResumeLineCandidate(detail, itemState, line, starting = false, userInteracted = true))
        assertNull(onlineResumeLineCandidate(detail, itemState.copy(available = false), line, starting = false, userInteracted = false))
    }

    @Test fun suggestedLineRejectsWrongScopeRemovedOrUnplayableEntries() {
        val (detail, itemState) = detailWithLastProgress()
        val progress = itemState.lastProgress!!
        val badSource = "src_" + "9".repeat(32)
        val badItem = "itm_" + "9".repeat(32)
        assertNull(onlineResumeLineCandidate(detail, itemState.copy(itemId = badItem), line, false, false))
        assertNull(onlineResumeLineCandidate(detail, itemState.copy(lastProgress = progress.copy(identity = progress.identity.copy(sourceId = badSource))), line, false, false))
        assertNull(onlineResumeLineCandidate(detail, itemState.copy(lastProgress = progress.copy(identity = progress.identity.copy(itemId = badItem))), line, false, false))
        assertNull(onlineResumeLineCandidate(detail, itemState.copy(lastProgress = progress.copy(identity = progress.identity.copy(episodeId = "ep_" + "9".repeat(32)))), line, false, false))
        val movieIdentity = OnlineIdentity(source, item, "movie", null, otherLine)
        assertNull(onlineResumeLineCandidate(detail, itemState.copy(lastProgress = progress.copy(identity = movieIdentity)), line, false, false))
        val noLongerPlayable = detail.copy(lines = detail.lines.map { selected ->
            if (selected.id == otherLine) selected.copy(entries = selected.entries.map { it.copy(supported = false) }) else selected
        })
        assertNull(onlineResumeLineCandidate(noLongerPlayable, itemState, line, false, false))
    }

    @Test fun automaticFocusIsNotUserActionButMovementAndExplicitSelectionAre() {
        val ui = OnlineDetailUiState()
        ui.bind(item, line)
        ui.expectAutomaticFocus("detail:play"); ui.focused("detail:play")
        assertFalse(ui.userInteracted)
        ui.expectAutomaticFocus("detail:play"); ui.focused("detail:play")
        assertFalse(ui.userInteracted)
        ui.focused("detail:line:$otherLine")
        assertTrue(ui.userInteracted)
        ui.bind(item, otherLine)
        assertTrue(ui.userInteracted)
        ui.reset(); assertFalse(ui.userInteracted)
        ui.expectAutomaticFocus("detail:play"); ui.focused("detail:play")
        assertFalse(ui.userInteracted)
        ui.markUserInteraction() // Includes clicking the already-selected line or the current group.
        assertTrue(ui.userInteracted)
    }

    @Test fun sharedNavigationChangesInvalidateInitialAndPendingDetailFocusWithoutBreakingPlaybackReturn() {
        assertFalse(onlineDetailFocusAllowed("", null, false))
        assertTrue(onlineDetailFocusAllowed("", null, true))
        assertTrue(onlineDetailFocusAllowed("detail:play", null, false))
        assertTrue(onlineDetailFocusAllowed("", "detail:entry:one", false))
        assertTrue(onlineDetailFocusCurrent(2, 2, 4, 4))
        assertFalse(onlineDetailFocusCurrent(2, 3, 4, 4))
        assertFalse(onlineDetailFocusCurrent(2, 2, 4, 5))

        val ui = OnlineDetailUiState()
        val memory = FocusMemory("nav:online")
        ui.bind(item, line)
        ui.expectAutomaticFocus("detail:play"); ui.focused("detail:play", memory)
        assertEquals(0L, memory.revision) // Programmatic focus must not invalidate a late resume lookup.
        assertFalse(ui.userInteracted)
        assertTrue(ui.focusContextCurrent(memory))
        memory.focused("nav:search")
        assertFalse(ui.focusContextCurrent(memory))
        // The page gets a fresh focus owner on return from playback.
        assertTrue(ui.focusContextCurrent(FocusMemory("nav:online")))

        ui.focus("detail:entry:line:episode", memory)
        assertTrue(ui.focusContextCurrent(memory))
        memory.focused("nav:home")
        assertFalse(ui.focusContextCurrent(memory))
        ui.clearPendingFocus()

        ui.focused("detail:line:$otherLine", memory)
        assertTrue(ui.userInteracted)
        assertEquals("detail:line:$otherLine", memory.key)
        assertTrue(ui.focusContextCurrent(memory))
    }
}
