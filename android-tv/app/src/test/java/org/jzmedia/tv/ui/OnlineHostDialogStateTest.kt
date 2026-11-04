package org.jzmedia.tv.ui

import org.jzmedia.tv.data.OnlineHostReport
import org.junit.Assert.*
import org.junit.Test

class OnlineHostDialogStateTest {
    private val source = "src_" + "1".repeat(32)
    private val item = "itm_" + "2".repeat(32)
    private val scope = "3".repeat(64)
    private fun state() = OnlineHostDialogState(source, "show-1", item, "Example", "Source", scope,
        "normal", 7, permissionRequired = false)
    private fun report() = OnlineHostReport(source, "show-1", listOf("media.example", "image.example"),
        listOf("api.example"), 0, "4".repeat(64))

    @Test fun defaultSelectionsCanBeNarrowedButNeverContainAnUnreportedHost() {
        val state = state()
        state.accept(report())
        assertEquals(listOf("media.example", "image.example"), state.selectedHosts())
        state.toggle("media.example")
        state.toggle("unreported.example")
        assertEquals(listOf("image.example"), state.selectedHosts())
        state.toggle("media.example")
        assertEquals(listOf("media.example", "image.example"), state.selectedHosts())
        state.toggle("media.example"); state.toggle("image.example")
        assertTrue(state.selectedHosts().isEmpty())
    }

    @Test fun busyOrSavedReportsCannotBeEditedAndRecheckingDiscardsPreviousSelections() {
        val state = state()
        state.accept(report())
        state.saving = true; state.toggle("media.example")
        assertEquals(2, state.selectedHosts().size)
        state.saving = false; state.checking = true; state.toggle("media.example")
        assertEquals(2, state.selectedHosts().size)
        state.checking = false; state.saved = true; state.toggle("media.example")
        assertEquals(2, state.selectedHosts().size)
        state.discardReport()
        assertNull(state.report)
        assertTrue(state.selectedHosts().isEmpty())
        assertTrue(state.saved)
    }

    @Test fun newReportMustBelongToTheExactSourceAndContent() {
        val state = state()
        assertThrows(IllegalArgumentException::class.java) {
            state.accept(report().copy(sourceId = "src_" + "5".repeat(32)))
        }
        assertThrows(IllegalArgumentException::class.java) { state.accept(report().copy(externalId = "show-2")) }
        assertNull(state.report)
        state.accept(report())
        state.toggle("image.example")
        state.discardReport(); state.accept(report())
        assertEquals(2, state.selectedHosts().size)
    }

    @Test fun asyncRepliesAndManagementCallbacksRequireTheOriginalViewingAndDetail() {
        val state = state()
        assertTrue(state.matches(source, "show-1", item, scope, "normal", 7))
        assertFalse(state.matches("src_" + "5".repeat(32), "show-1", item, scope, "normal", 7))
        assertFalse(state.matches(source, "show-2", item, scope, "normal", 7))
        assertFalse(state.matches(source, "show-1", null, scope, "normal", 7))
        assertFalse(state.matches(source, "show-1", item, "6".repeat(64), "normal", 7))
        assertFalse(state.matches(source, "show-1", item, scope, "private", 7))
        assertFalse(state.matches(source, "show-1", item, scope, "normal", 8))
    }
}
