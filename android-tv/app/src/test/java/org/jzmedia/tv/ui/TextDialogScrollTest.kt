package org.jzmedia.tv.ui

import org.junit.Assert.*
import org.junit.Test

class TextDialogScrollTest {
    @Test fun pageIsThirdOfViewport() {
        assertEquals(333, textDialogPagePx(1000, 600))
        assertEquals(240, textDialogPagePx(720, 600))
        assertEquals(1, textDialogPagePx(1, 600))
    }

    @Test fun pageFallsBackBeforeFirstLayout() {
        assertEquals(600, textDialogPagePx(0, 600))
        assertEquals(1, textDialogPagePx(-10, 0))
    }

    @Test fun downScrollsUntilBottomThenYieldsFocus() {
        assertEquals(240, textDialogScrollTarget(0, 2000, 240, down = true))
        assertEquals(2000, textDialogScrollTarget(1900, 2000, 240, down = true))
        // 到底后返回 null：不消费按键，焦点让给关闭按钮。
        assertNull(textDialogScrollTarget(2000, 2000, 240, down = true))
    }

    @Test fun upScrollsUntilTopThenYieldsFocus() {
        assertEquals(1660, textDialogScrollTarget(1900, 2000, 240, down = false))
        assertEquals(0, textDialogScrollTarget(100, 2000, 240, down = false))
        assertNull(textDialogScrollTarget(0, 2000, 240, down = false))
    }

    @Test fun shortTextNeverScrolls() {
        assertNull(textDialogScrollTarget(0, 0, 240, down = true))
        assertNull(textDialogScrollTarget(0, 0, 240, down = false))
    }

    @Test fun firstPressStepsThirdOfVisibleLines() {
        // 720px 视口、48px 行高：可见 15 行，首按走 5 行 = 240px。
        assertEquals(240, textDialogStepPx(false, 720, 800, 48))
        // 视口不足三行时至少走一行。
        assertEquals(48, textDialogStepPx(false, 100, 800, 48))
    }

    @Test fun firstPressFallsBackWithoutLayout() {
        assertEquals(240, textDialogStepPx(false, 0, 720, 48))
        // 行高未知时退回按视口三分之一像素步进。
        assertEquals(240, textDialogStepPx(false, 720, 800, 0))
    }

    @Test fun repeatScrollsOneLinePerEvent() {
        assertEquals(48, textDialogStepPx(true, 720, 800, 48))
        assertEquals(48, textDialogStepPx(true, 720, 800, 0))
    }

    @Test fun avgLineUsesMeasuredText() {
        assertEquals(50, textDialogAvgLine(500, 10, 48))
        assertEquals(48, textDialogAvgLine(0, 0, 48))
        assertEquals(1, textDialogAvgLine(0, 0, 0))
    }

    @Test fun burstKeyEventsCountAsRepeat() {
        // 系统连发、一次按压内的连击都走逐行；正常点按间隔远大于窗口。
        assertTrue(textDialogIsRepeat(1, 1000L, 0L))
        assertTrue(textDialogIsRepeat(0, 1100L, 1000L))
        assertFalse(textDialogIsRepeat(0, 1300L, 1000L))
        assertFalse(textDialogIsRepeat(0, 1000L, 0L))
    }

    @Test fun thumbIsNullWhenNotScrollable() {
        assertNull(textDialogThumb(0, 0, 800, 64))
        assertNull(textDialogThumb(0, 100, 0, 64))
    }

    @Test fun thumbTracksReadingProgress() {
        // 800px 轨道、最大滚动 800：滑块高 400，起点/终点分别贴轨道两端。
        assertEquals(0 to 400, textDialogThumb(0, 800, 800, 64))
        assertEquals(400 to 400, textDialogThumb(800, 800, 800, 64))
        assertEquals(200 to 400, textDialogThumb(400, 800, 800, 64))
    }

    @Test fun thumbKeepsMinimumHeight() {
        // 全文远长于可视时滑块按最小高度显示，仍能随进度移动。
        val (top, height) = textDialogThumb(5000, 10000, 800, 64)!!
        assertEquals(64, height)
        assertTrue(top in 1 until 800 - 64)
    }

    @Test fun snapAlignsLandingToWholeLines() {
        // 240 行高 48：落点 250 对齐到 240，顶行完整显示。
        assertEquals(240, textDialogSnapTarget(250, 0, 2000, 48, down = true))
        assertEquals(480, textDialogSnapTarget(490, 240, 2000, 48, down = true))
        assertEquals(192, textDialogSnapTarget(200, 440, 2000, 48, down = false))
    }

    @Test fun snapNeverBlocksReachingTheBottom() {
        // 到底不做对齐，原样返回 max，下一次下键让焦点给关闭按钮。
        assertEquals(2000, textDialogSnapTarget(2000, 1800, 2000, 48, down = true))
        assertEquals(2000, textDialogSnapTarget(2100, 1800, 2000, 48, down = true))
    }

    @Test fun snapAlwaysMakesProgress() {
        // 步长不足一行时下滚至少前进一步，不吞按键；行高未知时原样通过。
        assertEquals(48, textDialogSnapTarget(30, 0, 2000, 48, down = true))
        assertEquals(250, textDialogSnapTarget(250, 0, 2000, 0, down = true))
    }
}
