package org.jzmedia.tv.playback

import org.junit.Assert.*
import org.junit.Test

class WebVttTest {
    @Test fun parsesServerVttTextAndStylesWithoutHtmlExecution() {
        val cues = parseWebVtt("""
            WEBVTT

            NOTE generated subtitle
            ignore this block

            1
            00:00:01.200 --> 00:00:03.500 align:middle
            <b>中文</b> &amp; &lt;字幕&gt;<br>第二行

            00:05.000 --> 00:06.000
            short timestamp
        """.trimIndent())
        assertEquals(2, cues.size)
        assertEquals(1.2, cues[0].start, .001)
        assertEquals("中文 & <字幕>\n第二行", cues[0].text)
        assertEquals(5.0, cues[1].start, .001)
    }

    @Test fun overlappingLongCueRemainsVisibleAfterShortCueEnds() {
        val timeline = SubtitleTimeline(listOf(
            SubtitleCue(1.0, 10.0, "长字幕"), SubtitleCue(2.0, 3.0, "短字幕"),
            SubtitleCue(4.0, 5.0, "下一句"),
        ))
        assertEquals("长字幕\n短字幕", timeline.textAt(2.5))
        assertEquals("长字幕", timeline.textAt(3.5))
        assertEquals("长字幕\n下一句", timeline.textAt(4.0))
        assertEquals("", timeline.textAt(10.0))
        // Seeking backwards must restore old cues without a mutable forward-only cursor.
        assertEquals("长字幕\n短字幕", timeline.textAt(2.0))
    }

    @Test fun malformedAndReversedTimestampsAreSkipped() {
        val cues = parseWebVtt("WEBVTT\r\n\r\n00:99.000 --> 00:00:03.000\r\nbad\r\n\r\n00:02.000 --> 00:01.000\r\nreversed\r\n")
        assertTrue(cues.isEmpty())
    }

    @Test fun sourceOffsetAndUserAdjustmentShareOneTimeline() {
        val timeline = SubtitleTimeline(listOf(SubtitleCue(122.0, 125.0, "对齐")))
        val source = sourcePosition(7000, 116.0, 600.0)
        assertEquals("对齐", timeline.textAt(source))
        assertEquals("", timeline.textAt(source - 2.0))
    }
}
