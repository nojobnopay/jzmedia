package org.jzmedia.tv.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.key
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.text
import org.json.JSONObject

private val GenericSeasonName = Regex("^(第[0-9零〇一二两三四五六七八九十百千万]+季|season[0-9]+)$", RegexOption.IGNORE_CASE)

/** The detail page owns vertical scrolling; all season focus targets stay composed. */
@Composable
internal fun SeasonGrid(api: JzApi, seasons: List<JSONObject>, showPosterPath: String,
                        memory: FocusMemory, openSeason: (Int) -> Unit) {
    BoxWithConstraints(Modifier.fillMaxWidth().padding(16.dp)) {
        val gap = 18.dp
        val columns = ((maxWidth + gap) / (150.dp + gap)).toInt().coerceAtLeast(1)
        Column(verticalArrangement = Arrangement.spacedBy(28.dp)) {
            seasons.chunked(columns).forEach { group ->
                Row(horizontalArrangement = Arrangement.spacedBy(gap)) {
                    group.forEach { season ->
                        val number = season.optInt("season")
                        key(number) {
                            val heading = if (number == 0) "特别篇" else "第 $number 季"
                            val name = season.text("name").trim()
                            val compactName = name.filterNot(Char::isWhitespace)
                            val subtitle = name.takeUnless {
                                GenericSeasonName.matches(compactName) ||
                                    (number == 0 && compactName.lowercase() in setOf("特别篇", "特别集", "番外篇", "special", "specials"))
                            }.orEmpty()
                            val count = "共 ${season.optInt("total")} 集 · 已看 ${season.optInt("watched_count")} 集"
                            val description = listOf(heading, subtitle, count).filter { it.isNotBlank() }.joinToString(" · ")
                            val card = JSONObject()
                                .put("title", if (subtitle.isBlank()) heading else "$heading\n$subtitle")
                                .put("poster_path", season.text("poster_path"))
                            MediaCard(api, card, { openSeason(number) },
                                modifier = Modifier.focusMemory(memory, "season:$number")
                                    .semantics { contentDescription = description },
                                subtitle = count, fallbackPosterPath = showPosterPath,
                                posterPlaceholder = heading.replace(" ", ""))
                        }
                    }
                }
            }
        }
    }
}
