package org.jzmedia.tv.ui

/**
 * 浏览页筛选的纯逻辑：年份合并、排序映射。
 *
 * 年份合并规则（与服务端 facets 对齐，无需改接口）：
 * - 去重年份 ≤12：全列单年，不合并。
 * - ＞12：只考虑近 5 年做单年 chip，且需同时满足单年 count≥3
 *   与所在年代总量≥15，否则并入年代 chip；年代 chip 全保留。
 * - 服务端 facets 缺 decades 字段时（旧版本），只按单年 count 判定，
 *   不展示年代 chip。
 */
internal data class PeriodChip(val id: String, val label: String, val year: String, val decade: String)

internal fun buildPeriodChips(years: List<Pair<Int, Int>>, decades: List<Pair<Int, Int>>): List<PeriodChip> {
    val sortedYears = years.sortedByDescending { it.first }
    val all = PeriodChip("all", "全部", "", "")
    if (sortedYears.size <= 12) {
        return listOf(all) + sortedYears.map { (y, _) ->
            PeriodChip("y$y", y.toString(), y.toString(), "")
        }
    }
    val decadeTotals = decades.toMap()
    val out = mutableListOf(all)
    for ((y, count) in sortedYears.take(5)) {
        val start = (y / 10) * 10
        // 旧服务端无 decades 时按大数放行，只看单年 count。
        val decadeTotal = decadeTotals[start] ?: Int.MAX_VALUE
        if (count >= 3 && decadeTotal >= 15) {
            out.add(PeriodChip("y$y", y.toString(), y.toString(), ""))
        }
    }
    for ((d, _) in decades.sortedByDescending { it.first }) {
        out.add(PeriodChip("d$d", "${d}年代", "", d.toString()))
    }
    return out
}

internal fun periodChipId(year: String, decade: String): String =
    when {
        year.isNotBlank() -> "y$year"
        decade.isNotBlank() -> "d$decade"
        else -> "all"
    }

internal data class SortChip(val key: String, val label: String, val sort: String, val order: String)

internal fun sortChipsFor(kind: String): List<SortChip> =
    if (kind == "collections") {
        listOf(
            SortChip("updated", "最近更新", "updated", "desc"),
            SortChip("title", "标题", "title", "asc"),
        )
    } else {
        listOf(
            SortChip("added", "添加时间", "added", "desc"),
            SortChip("rating", "评分", "rating", "desc"),
            SortChip("title", "标题", "title", "asc"),
        )
    }

internal fun sortLabelFor(kind: String, sort: String): String =
    sortChipsFor(kind).firstOrNull { it.sort == sort }?.label ?: sort
