package org.jzmedia.tv.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.key
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.tv.material3.Button
import androidx.tv.material3.ButtonDefaults
import androidx.tv.material3.Text

/** The parent supplies a sticky focused-episode description above these lazy rows. */
internal fun LazyListScope.episodeGrid(
    episodes: List<EpisodeButtonModel>,
    columns: Int,
    focusModifier: (String) -> Modifier,
    onFocused: (Long) -> Unit,
    openEpisode: (Long) -> Unit,
) {
    items(episodes.chunked(columns), key = { "episode-row:${it.first().id}" }) { group ->
        BoxWithConstraints(Modifier.fillMaxWidth().padding(8.dp)) {
            val buttonWidth = ((maxWidth.value - (columns - 1) * EpisodeButtonGap) / columns)
                .coerceAtMost(EpisodeButtonMaximumWidth.toFloat()).dp
            Row(horizontalArrangement = Arrangement.spacedBy(EpisodeButtonGap.dp)) {
                group.forEach { episode ->
                    key(episode.id) {
                        Button(
                            onClick = { openEpisode(episode.id) },
                            modifier = focusModifier("episode:${episode.id}").width(buttonWidth).heightIn(min = 56.dp)
                                .onFocusChanged { if (it.isFocused) onFocused(episode.id) }
                                .semantics { contentDescription = episode.description },
                            contentPadding = PaddingValues(horizontal = 6.dp, vertical = 5.dp),
                            shape = ButtonDefaults.shape(shape = RoundedCornerShape(8.dp)),
                            colors = ButtonDefaults.colors(
                                containerColor = if (episode.continuing) Accent else Panel,
                                contentColor = Color.White,
                                focusedContainerColor = Color.White,
                                focusedContentColor = Color(0xFF141414),
                            ),
                        ) {
                            Column(horizontalAlignment = Alignment.CenterHorizontally,
                                verticalArrangement = Arrangement.Center,
                                modifier = Modifier.fillMaxWidth().heightIn(min = 46.dp)) {
                                Text(episode.number, fontSize = if (episode.number.length > 7) 16.sp else 19.sp, lineHeight = 22.sp, maxLines = 1)
                                if (episode.badge.isNotBlank()) Text(episode.badge, fontSize = 14.sp, lineHeight = 18.sp,
                                    maxLines = 2, overflow = TextOverflow.Ellipsis)
                            }
                        }
                    }
                }
            }
        }
    }
}
