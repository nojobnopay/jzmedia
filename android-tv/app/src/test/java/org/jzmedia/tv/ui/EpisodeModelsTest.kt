package org.jzmedia.tv.ui

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class EpisodeModelsTest {
    private fun episode() = JSONObject("""{"id":123,"season":1,"episode":5,"title":"完整的剧集名称","version":1,"exists":true}""")

    @Test fun mergedEpisodeNumbersStayCompactAndKeepTheFullTitleForFocus() {
        val model = episodeButtonModel(episode().put("episode_end", 6), false)
        assertEquals("05–06", model.number)
        assertEquals("", model.badge)
        assertTrue(model.description.contains("完整的剧集名称"))
        assertTrue(model.description.contains("未看"))
        assertEquals("05", episodeButtonModel(episode().put("episode_end", 0), false).number)
    }

    @Test fun duplicatedEpisodeVersionsRemainDistinct() {
        val first = episodeButtonModel(episode(), true)
        val second = episodeButtonModel(episode().put("id", 456).put("version", 2), true)
        assertEquals(first.number, second.number)
        assertNotEquals(first.id, second.id)
        assertEquals("V1", first.badge)
        assertEquals("V2", second.badge)
        assertTrue(second.description.contains("版本 2"))
        assertEquals("V2", episodeButtonModel(episode().put("version", 2), false).badge)
    }

    @Test fun progressAndWatchedStatusDoNotConflict() {
        val row = episode().put("progress", JSONObject().put("position", 1231))
        val continuing = episodeButtonModel(row, false)
        assertTrue(continuing.continuing)
        assertEquals("续播", continuing.badge)
        assertTrue(continuing.description.contains("20:31"))
        val watched = episodeButtonModel(row.put("watched", 1), false)
        assertFalse(watched.continuing)
        assertEquals("已看", watched.badge)
        assertFalse(watched.description.contains("已观看"))
    }

    @Test fun offlineEpisodesKeepTheirProgressButNeverLookPlayable() {
        val row = episode().put("progress", JSONObject().put("position", 45)).put("exists", false)
        val model = episodeButtonModel(row, true)
        assertFalse(model.continuing)
        assertEquals("V1 · 离线", model.badge)
        assertTrue(model.description.contains("文件离线"))
        assertTrue(model.description.contains("0:45"))
        assertEquals("离线", episodeButtonModel(episode().put("missing", 1), false).badge)
    }

    @Test fun columnsAdaptToTheAvailableDetailWidth() {
        assertEquals(8, episodeGridColumns(868))
        assertEquals(12, episodeGridColumns(1188))
        assertEquals(2, episodeGridColumns(248))
        assertEquals(1, episodeGridColumns(80))
        for (width in 88..2000) {
            val columns = episodeGridColumns(width)
            assertTrue(columns * EpisodeButtonMinimumWidth + (columns - 1) * EpisodeButtonGap <= width)
        }
    }
}
