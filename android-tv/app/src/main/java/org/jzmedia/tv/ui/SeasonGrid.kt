package org.jzmedia.tv.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.runtime.key
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.text
import org.json.JSONObject

private val GenericSeasonName = Regex("^(第[0-9零〇一二两三四五六七八九十百千万]+季|season[0-9]+)$", RegexOption.IGNORE_CASE)

internal data class SeasonCardModel(val number: Int, val heading: String, val title: String,
                                    val subtitle: String, val description: String, val progress: Float?)

internal fun seasonGridColumns(width: Int): Int = ((width - 32 + 18) / (150 + 18)).coerceAtLeast(1)

internal fun seasonCardModel(season: JSONObject): SeasonCardModel {
    val number = season.optInt("season")
    val heading = if (number == 0) "特别篇" else "第 $number 季"
    val name = season.text("name").trim()
    val compactName = name.filterNot(Char::isWhitespace)
    val distinctName = name.takeUnless {
        GenericSeasonName.matches(compactName) ||
            (number == 0 && compactName.lowercase() in setOf("特别篇", "特别集", "番外篇", "special", "specials"))
    }.orEmpty()
    val total = season.optInt("total").coerceAtLeast(0)
    val watched = season.optInt("watched_count").coerceAtLeast(0)
    val complete = total > 0 && (season.optBoolean("done") || watched >= total)
    val state = when {
        complete -> "已看完"
        season.optBoolean("has_partial") || watched > 0 -> "正在看"
        else -> ""
    }
    val subtitle = when {
        complete -> "已看完 · $total 集"
        state.isNotBlank() -> "$state · $watched/$total 集"
        else -> "共 $total 集"
    }
    val description = listOf(heading, distinctName, state, "共 $total 集，已看 $watched 集")
        .filter { it.isNotBlank() }.joinToString(" · ")
    return SeasonCardModel(number, heading, if (distinctName.isBlank()) heading else "$heading\n$distinctName",
        subtitle, description, if (total > 0 && watched > 0) (watched.toFloat() / total).coerceIn(0f, 1f) else null)
}

/** Rows belong to the detail LazyColumn, so offscreen seasons do not retain images. */
internal fun LazyListScope.seasonGrid(api: JzApi, seasons: List<JSONObject>, showPosterPath: String,
                                     columns: Int, focusModifier: (String) -> Modifier, openSeason: (Int) -> Unit) {
    items(seasons.chunked(columns), key = { "season-row:${it.first().optInt("season")}" }) { group ->
        Row(horizontalArrangement = Arrangement.spacedBy(18.dp), modifier = Modifier.padding(horizontal = 16.dp, vertical = 12.dp)) {
            group.forEach { season ->
                val model = seasonCardModel(season)
                key(model.number) {
                    val card = JSONObject().put("title", model.title).put("poster_path", season.text("poster_path"))
                    MediaCard(api, card, { openSeason(model.number) },
                        modifier = focusModifier("season:${model.number}").semantics { contentDescription = model.description },
                        subtitle = model.subtitle, fallbackPosterPath = showPosterPath,
                        posterPlaceholder = model.heading.replace(" ", ""), progressFraction = model.progress)
                }
            }
        }
    }
}
