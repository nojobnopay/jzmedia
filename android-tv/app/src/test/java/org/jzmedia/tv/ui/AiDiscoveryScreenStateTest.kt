package org.jzmedia.tv.ui

import org.junit.Assert.*
import org.junit.Test
import org.jzmedia.tv.data.DISCOVERY_QUERY_LIMIT
import org.jzmedia.tv.data.DiscoveryResult

class AiDiscoveryScreenStateTest {
    private fun result(id: String, source: String = "library") = DiscoveryResult(id, source, "ok", "recommend",
        "找到作品", "", emptyList(), emptyList(), emptyList())

    @Test fun voiceRequiresItsOwnPendingLaunchAndOneNonemptyFinalResult() {
        var elapsed = 100L
        val voice = DiscoveryVoiceTurn { elapsed }
        assertNull(voice.finish(true, listOf("旧回答")))
        assertTrue(voice.launch())
        assertFalse(voice.launch())
        assertNull(voice.finish(false, listOf("已取消")))
        assertNull(voice.finish(true, listOf("晚到")))
        assertTrue(voice.launch())
        assertNull(voice.finish(true, listOf(" ")))
        assertTrue(voice.launch())
        assertEquals("找一个雨天的电影", voice.finish(true, listOf("", " 找一个雨天的电影 ", "其他")))
        assertNull(voice.finish(true, listOf("重复")))
        assertTrue(voice.launch())
        elapsed += 120_000
        assertNull(voice.finish(true, listOf("待机期间的旧回答")))
        assertFalse(voice.isFresh())
        assertTrue(voice.launch())
        assertEquals("重新说", voice.finish(true, listOf("重新说")))
    }

    @Test fun cancelledSearchCannotReplaceItsSuccessorAndKeystrokesNeverSubmit() {
        val state = AiDiscoveryScreenState()
        state.change("找好看的")
        assertNull(state.request)
        state.submit()
        val old = state.request!!
        state.submit("重复发送")
        assertSame(old, state.request)
        state.stopCurrent()
        state.submit("新要求")
        val next = state.request!!
        state.complete(old, result("old"))
        assertSame(next, state.request)
        assertNull(state.result)
        state.complete(next, result("new"))
        assertEquals("新要求", state.lastSearch?.query)
        assertEquals("", state.draft)
    }

    @Test fun correctionUsesTheParentTurnAndPublicHandoffIsExplicit() {
        val state = AiDiscoveryScreenState()
        state.submit("找悬疑片")
        state.complete(state.request!!, result("original"))
        state.submit("只要一部")
        assertEquals(DiscoveryRequest.Search("只要一部", "library", "original"), state.request)
        state.complete(state.request!!, result("follow"))
        state.editLast()
        assertEquals("只要一部", state.draft)
        state.submit("只要两部")
        assertEquals(DiscoveryRequest.Search("只要两部", "library", "original"), state.request)
        state.complete(state.request!!, result("corrected"))
        state.searchPublic()
        assertEquals(DiscoveryRequest.Search("按刚才的要求查公开片目", "public", "corrected", true), state.request)
        state.complete(state.request!!, result("public", "public"))
        state.submit("第二部是哪年的")
        assertEquals(DiscoveryRequest.Search("第二部是哪年的", "public", "public"), state.request)
    }

    @Test fun returningFromDetailsRevalidatesBeforeShowingTitlesAndPreservesPosition() {
        val state = AiDiscoveryScreenState()
        state.submit("我没看过的")
        state.complete(state.request!!, result("list"))
        state.leaveForDetail("library-movie-7", 4, 33)
        assertNull(state.result)
        state.submit("离开后迟到的文字")
        assertNull(state.request)
        state.restore()
        val refresh = state.request!!
        assertEquals(DiscoveryRequest.Refresh("list"), refresh)
        state.fail(refresh, "网络超时", "timeout")
        state.retryFailed()
        assertEquals(refresh, state.request)
        state.complete(state.request!!, result("fresh"))
        assertEquals("我没看过的", state.lastSearch?.query)
        assertEquals("library-movie-7", state.restoreFocus)
        assertEquals(4, state.firstIndex)
        assertEquals(33, state.firstOffset)
    }

    @Test fun expiredSessionClearsResultsAndRestartUsesAFreshLibrarySearch() {
        val state = AiDiscoveryScreenState()
        state.submit("电影")
        state.complete(state.request!!, result("old"))
        state.submit("换一些")
        state.fail(state.request!!, "观看范围改变", "session_expired")
        state.submit("迟到")
        state.retryFailed()
        assertNull(state.result)
        assertNull(state.request)
        assertTrue(state.expired)
        state.restart()
        state.submit("新要求")
        assertEquals(DiscoveryRequest.Search("新要求", "library", null), state.request)
    }

    @Test fun unicodeLimitDoesNotSplitCharactersOrSilentlySubmitLongVoiceTranscript() {
        val state = AiDiscoveryScreenState()
        val limit = "🎬".repeat(DISCOVERY_QUERY_LIMIT)
        state.change(limit + "再")
        assertEquals(limit, state.draft)
        state.submit(limit + "再")
        assertNull(state.request)
        assertTrue(state.error.isNotBlank())
        state.submit()
        assertEquals(limit, (state.request as DiscoveryRequest.Search).query)
    }
}
