package org.jzmedia.tv.data

import org.junit.Assert.*
import org.junit.Test

class SearchHistoryTest {
    private fun store(values: MutableMap<String, String> = mutableMapOf()) = SearchHistoryStore(values::get,
        { key, value -> if (value == null) values.remove(key) else values[key] = value })

    @Test fun historiesAreIsolatedByCanonicalServerLibraryAndSearchMode() {
        val values = mutableMapOf<String, String>()
        val history = store(values)
        history.recordQuery("http://server/jzmedia", 1, "title", "SQ")
        history.recordQuery("http://server/jzmedia/", 1, "actor", "ZXC")
        assertEquals(listOf("SQ"), history.queries("http://server/jzmedia/", 1, "title"))
        assertEquals(listOf("ZXC"), history.queries("http://server/jzmedia/", 1, "actor"))
        assertTrue(history.queries("http://server/jzmedia/", 2, "title").isEmpty())
        assertTrue(history.queries("http://other/jzmedia/", 1, "title").isEmpty())
        assertTrue(history.queries("http://server/other/", 1, "title").isEmpty())
        assertTrue(values.keys.none { it.contains("server") })
    }

    @Test fun selectingAnOldQueryMovesItToTheFrontAndBoundsTheList() {
        val history = store()
        for (index in 0..20) history.recordQuery("http://server", 0, "title", "Film $index")
        history.recordQuery("http://server", 0, "title", " film 10 \n")
        val rows = history.queries("http://server", 0, "title")
        assertEquals(SEARCH_HISTORY_LIMIT, rows.size)
        assertEquals("film 10", rows.first())
        assertEquals(1, rows.count { it.equals("Film 10", ignoreCase = true) })
        history.recordQuery("http://server", 0, "title", " \n")
        assertEquals(rows, history.queries("http://server", 0, "title"))
    }

    @Test fun recentActorsPreserveOpaqueKeysAndDoNotMergeSameNamedPeople() {
        val history = store()
        val first = RecentActor("tmdb:123", "同名演员")
        val second = RecentActor("name:同 名&演员", "同名演员")
        history.recordActor("http://server", 3, first)
        history.recordActor("http://server", 3, second)
        history.recordActor("http://server", 3, first.copy(name = "已更新姓名"))
        assertEquals(listOf(first.copy(name = "已更新姓名"), second), history.actors("http://server", 3))
        assertTrue(history.actors("http://server", 4).isEmpty())
        history.clearActors("http://server", 3)
        assertTrue(history.actors("http://server", 3).isEmpty())
    }

    @Test fun clearingQueriesDoesNotClearOtherModesOrRecentActors() {
        val history = store()
        history.recordQuery("http://server", 1, "title", "SQ")
        history.recordQuery("http://server", 1, "actor", "ZXC")
        history.recordActor("http://server", 1, RecentActor("tmdb:1", "演员"))
        history.clearQueries("http://server", 1, "title")
        assertTrue(history.queries("http://server", 1, "title").isEmpty())
        assertEquals(listOf("ZXC"), history.queries("http://server", 1, "actor"))
        assertEquals(1, history.actors("http://server", 1).size)
    }

    @Test fun actorHistoryIsBoundedAndIgnoresInvalidEntries() {
        val history = store()
        for (index in 1..20) history.recordActor("http://server", 1, RecentActor("tmdb:$index", "演员 $index"))
        history.recordActor("http://server", 1, RecentActor("", "无身份"))
        history.recordActor("http://server", 1, RecentActor("tmdb:22", "\u0000"))
        val actors = history.actors("http://server", 1)
        assertEquals(SEARCH_HISTORY_LIMIT, actors.size)
        assertEquals("tmdb:20", actors.first().key)
        assertEquals("tmdb:9", actors.last().key)
    }

    @Test fun damagedLocalDataIsReportedAndDiscarded() {
        val values = mutableMapOf<String, String>()
        val errors = mutableListOf<Exception>()
        val history = SearchHistoryStore(values::get, { key, value -> if (value == null) values.remove(key) else values[key] = value }, { errors += it })
        history.recordQuery("http://server", 0, "title", "SQ")
        values[values.keys.single()] = "invalid-json"
        assertTrue(history.queries("http://server", 0, "title").isEmpty())
        assertEquals(1, errors.size)
        assertTrue(values.isEmpty())
    }
}
