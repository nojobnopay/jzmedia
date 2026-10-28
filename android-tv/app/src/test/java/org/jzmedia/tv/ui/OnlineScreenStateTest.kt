package org.jzmedia.tv.ui

import org.jzmedia.tv.data.OnlineCatalogItem
import org.jzmedia.tv.data.OnlineDetail
import org.jzmedia.tv.data.OnlineEntry
import org.jzmedia.tv.data.OnlineIdentity
import org.jzmedia.tv.data.OnlineItemState
import org.jzmedia.tv.data.OnlineLine
import org.jzmedia.tv.data.OnlineProgress
import org.jzmedia.tv.data.OnlineSearchItem
import org.jzmedia.tv.data.OnlineSearchPage
import org.jzmedia.tv.data.OnlineSource
import org.junit.Assert.*
import org.junit.Test

class OnlineScreenStateTest {
    private val source = OnlineSource("src_" + "1".repeat(32), "Source", "maccms")
    private val item = OnlineCatalogItem("itm_" + "2".repeat(32), source.id, "tv", "Show")
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

    @Test fun lazyFocusTargetsFollowResultsAndDetailWithoutDroppingNoticeOrKeyboardRows() {
        val page = OnlineScreenState()
        page.results = OnlineSearchPage(source, listOf(
            OnlineSearchItem("one", "One", "", "movie", ""), OnlineSearchItem("two", "Two", "", "tv", "")), 1, 2, 4)
        assertEquals(7, onlineFocusItemIndex(page, "online:result:two"))
        page.keyboard = true; page.notice = "A saving notice"
        assertEquals(9, onlineFocusItemIndex(page, "online:result:two"))
        assertNull(onlineFocusItemIndex(page, "online:result:removed"))
        page.detailSourceId = source.id; page.detailExternalId = "one"; page.detail = detail(); page.lineId = line
        assertEquals(6, onlineFocusItemIndex(page, "online:entry:${identity.episodeId}"))
        assertNull(onlineFocusItemIndex(page, "online:entry:removed"))
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
