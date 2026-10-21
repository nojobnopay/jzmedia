package org.jzmedia.tv.ui

import org.junit.Assert.*
import org.junit.Test

class FocusMemoryTest {
    @Test fun returningFromDetailsRestoresTheOriginalListItemDespiteNavigationFocusOnExit() {
        val list = FocusMemory("nav:shows")
        list.focused("show:42")
        list.leaving = true
        list.focused("nav:home")
        val restored = FocusMemory.restore(list.save())
        assertEquals("show:42", restored.restoreKey)
        assertFalse(restored.leaving)
        var requests = 0
        assertFalse(restored.restoreFocus("nav:home") { requests++; true })
        assertTrue(restored.restoreFocus("show:42") { requests++; true })
        assertFalse(restored.restoreFocus("show:42") { requests++; true })
        assertEquals(1, requests)
    }

    @Test fun returningFromPlaybackKeepsTheDetailsPlayActionAndLaterFocusCanMoveNormally() {
        val details = FocusMemory("detail:primary")
        details.focused("play")
        details.leaving = true
        details.focused("more")
        val restored = FocusMemory.restore(details.save())
        assertTrue(restored.restoreFocus("play") { true })
        restored.focused("version")
        assertEquals("version", restored.save())
    }

    @Test fun unavailableFocusTargetDoesNotConsumeTheOnlyRestoreAttempt() {
        val restored = FocusMemory.restore("episode:251")
        assertFalse(restored.restoreFocus("episode:251") { false })
        assertFalse(restored.restored)
        assertTrue(restored.restoreFocus("episode:251") { true })
        assertTrue(restored.restored)
    }

    @Test fun pageThatIsLeavingCannotRequestFocusBackFromItsDestination() {
        val leaving = FocusMemory.restore("movie:42")
        leaving.leaving = true
        assertFalse(leaving.restoreFocus("movie:42") { fail("Leaving page must not steal focus"); true })
        assertFalse(leaving.restored)
    }
}
