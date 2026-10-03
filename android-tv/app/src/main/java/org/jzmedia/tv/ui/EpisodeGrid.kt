package org.jzmedia.tv.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.key
import androidx.compose.runtime.remember
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.tv.material3.Button
import androidx.tv.material3.ButtonDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text

/** Each server page contains at most 50 buttons; one eager grid keeps every saved
 * episode focus target available while the surrounding detail page owns scrolling. */
@Composable
internal fun EpisodeGrid(
    episodes: List<EpisodeButtonModel>,
    memory: FocusMemory,
    focusedId: Long,
    onFocused: (Long) -> Unit,
    openEpisode: (Long) -> Unit,
    requestFirstFocus: Boolean,
    onFocusRequested: () -> Unit,
) {
    val firstRequester = remember { FocusRequester() }
    LaunchedEffect(episodes, requestFirstFocus) {
        if (requestFirstFocus && episodes.isNotEmpty()) {
            withFrameNanos { }
            firstRequester.requestFocus()
            onFocusRequested()
        }
    }
    val focused = episodes.firstOrNull { it.id == focusedId } ?: episodes.firstOrNull()
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(focused?.description ?: "暂时没有已入库的分集", color = Muted,
            style = MaterialTheme.typography.bodyMedium, maxLines = 2, overflow = TextOverflow.Ellipsis,
            modifier = Modifier.fillMaxWidth().height(44.dp).padding(horizontal = 6.dp))
        BoxWithConstraints(Modifier.fillMaxWidth().padding(6.dp)) {
            val columns = episodeGridColumns(maxWidth.value.toInt())
            val buttonWidth = ((maxWidth.value - (columns - 1) * EpisodeButtonGap) / columns)
                .coerceAtMost(EpisodeButtonMaximumWidth.toFloat()).dp
            Column(verticalArrangement = Arrangement.spacedBy(EpisodeButtonGap.dp)) {
                episodes.chunked(columns).forEach { group ->
                    Row(horizontalArrangement = Arrangement.spacedBy(EpisodeButtonGap.dp)) {
                        group.forEach { episode ->
                            key(episode.id) {
                                Button(
                                    onClick = { openEpisode(episode.id) },
                                    modifier = Modifier.width(buttonWidth).heightIn(min = 48.dp)
                                        .focusMemory(memory, "episode:${episode.id}", if (episode.id == episodes.first().id) firstRequester else null)
                                        .onFocusChanged { if (it.isFocused) onFocused(episode.id) }
                                        .semantics { contentDescription = episode.description },
                                    contentPadding = PaddingValues(horizontal = 6.dp, vertical = 3.dp),
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
                                        modifier = Modifier.fillMaxWidth().heightIn(min = 42.dp)) {
                                        Text(episode.number, fontSize = if (episode.number.length > 7) 16.sp else 19.sp, lineHeight = 22.sp, maxLines = 1)
                                        if (episode.badge.isNotBlank()) Text(episode.badge, fontSize = 11.sp, lineHeight = 14.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
