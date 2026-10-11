package org.jzmedia.tv.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class BrowseFiltersTest {
    private fun years(range: IntRange, count: Int = 5): List<Pair<Int, Int>> =
        range.map { it to count }

    private fun decades(vararg pairs: Pair<Int, Int>): List<Pair<Int, Int>> = pairs.toList()

    @Test fun fewYearsShowAllSinglesWithoutDecades() {
        val chips = buildPeriodChips(years(2015..2024), decades(2020 to 30, 2010 to 20))
        assertEquals(11, chips.size)
        assertEquals("all", chips.first().id)
        assertTrue(chips.drop(1).all { it.year.isNotBlank() && it.decade.isBlank() })
        assertEquals("2024", chips[1].label)
    }

    @Test fun twelveYearsIsTheBoundary() {
        val chips = buildPeriodChips(years(2013..2024), decades(2020 to 40, 2010 to 30))
        assertEquals(13, chips.size)
        assertTrue(chips.none { it.decade.isNotBlank() })
    }

    @Test fun manyYearsShowRecentSinglesPlusAllDecades() {
        val chips = buildPeriodChips(
            years(2000..2024, count = 5),
            decades(2020 to 30, 2010 to 50, 2000 to 40),
        )
        val ids = chips.map { it.id }
        assertEquals(listOf("all", "y2024", "y2023", "y2022", "y2021", "y2020", "d2020", "d2010", "d2000"), ids)
    }

    @Test fun sparseRecentYearFoldsIntoItsDecade() {
        val counts = years(2000..2024, count = 5).toMap().toMutableMap()
        counts[2024] = 2
        val chips = buildPeriodChips(
            counts.toList(), decades(2020 to 30, 2010 to 50, 2000 to 40),
        )
        val ids = chips.map { it.id }
        assertTrue("y2024" !in ids)
        assertTrue("y2023" in ids)
        assertTrue("d2020" in ids)
    }

    @Test fun sparseDecadeFoldsItsRecentYears() {
        val chips = buildPeriodChips(
            years(2000..2024, count = 5),
            // 2020 年代总量 8（＜15）：2020–2024 不再单列，只留年代 chip。
            decades(2020 to 8, 2010 to 50, 2000 to 40),
        )
        val ids = chips.map { it.id }
        assertEquals(listOf("all", "d2020", "d2010", "d2000"), ids)
    }

    @Test fun missingDecadesFallsBackToYearCountOnly() {
        val counts = years(2000..2024, count = 5).toMap().toMutableMap()
        counts[2024] = 1
        val chips = buildPeriodChips(counts.toList(), emptyList())
        val ids = chips.map { it.id }
        // 无年代总量可参考时只看单年 count：2024（1 部）折叠，其余近 5 年保留。
        assertEquals(listOf("all", "y2023", "y2022", "y2021", "y2020"), ids)
    }

    @Test fun emptyYearsShowOnlyAll() {
        assertEquals(listOf("all"), buildPeriodChips(emptyList(), emptyList()).map { it.id })
    }

    @Test fun periodChipIdPrefersYearOverDecade() {
        assertEquals("all", periodChipId("", ""))
        assertEquals("y2024", periodChipId("2024", ""))
        assertEquals("d2010", periodChipId("", "2010"))
        assertEquals("y2024", periodChipId("2024", "2020"))
    }

    @Test fun sortChipsMatchTheAgreedSets() {
        assertEquals(
            listOf("added" to "desc", "rating" to "desc", "title" to "asc"),
            sortChipsFor("movies").map { it.sort to it.order },
        )
        assertEquals(
            listOf("added" to "desc", "rating" to "desc", "title" to "asc"),
            sortChipsFor("shows").map { it.sort to it.order },
        )
        assertEquals(
            listOf("updated" to "desc", "title" to "asc"),
            sortChipsFor("collections").map { it.sort to it.order },
        )
        assertEquals("评分", sortLabelFor("movies", "rating"))
        assertEquals("最近更新", sortLabelFor("collections", "updated"))
    }
}
