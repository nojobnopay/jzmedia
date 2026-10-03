package org.jzmedia.tv.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.key
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.tv.material3.Button
import androidx.tv.material3.ButtonDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import org.jzmedia.tv.data.text
import org.json.JSONObject

private val GenericSeasonName = Regex("^(第[0-9零〇一二两三四五六七八九十百千万]+季|season[0-9]+)$", RegexOption.IGNORE_CASE)

/** The detail page owns vertical scrolling; all season focus targets stay composed. */
@Composable
internal fun SeasonGrid(seasons: List<JSONObject>, memory: FocusMemory, openSeason: (Int) -> Unit) {
    BoxWithConstraints(Modifier.fillMaxWidth().padding(12.dp)) {
        val gap = 16.dp
        val columns = ((maxWidth + gap) / (180.dp + gap)).toInt().coerceAtLeast(1)
        val cardWidth = ((maxWidth - gap * (columns - 1)) / columns).coerceAtMost(210.dp)
        Column(verticalArrangement = Arrangement.spacedBy(gap)) {
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
                            Button(onClick = { openSeason(number) },
                                modifier = Modifier.width(cardWidth).height(112.dp).focusMemory(memory, "season:$number")
                                    .semantics { contentDescription = description },
                                contentPadding = PaddingValues(14.dp),
                                shape = ButtonDefaults.shape(shape = RoundedCornerShape(10.dp)),
                                colors = ButtonDefaults.colors(containerColor = Panel, contentColor = Color.White,
                                    focusedContainerColor = Color.White, focusedContentColor = Color(0xFF141414))) {
                                Column(Modifier.fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally,
                                    verticalArrangement = Arrangement.SpaceBetween) {
                                    Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(2.dp)) {
                                        Text(heading, style = MaterialTheme.typography.titleLarge, textAlign = TextAlign.Center,
                                            maxLines = 1, overflow = TextOverflow.Ellipsis)
                                        if (subtitle.isNotBlank()) Text(subtitle, style = MaterialTheme.typography.bodySmall,
                                            textAlign = TextAlign.Center, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                    }
                                    Text(count, style = MaterialTheme.typography.labelMedium, textAlign = TextAlign.Center,
                                        maxLines = 1, overflow = TextOverflow.Ellipsis)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
