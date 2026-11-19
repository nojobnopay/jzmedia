package org.jzmedia.tv.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.tv.material3.Button
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import org.jzmedia.tv.data.DiscoveryItem
import org.jzmedia.tv.data.DiscoveryResult
import org.jzmedia.tv.data.DiscoveryTarget
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.ui.generated.DesignTokens

@Composable
internal fun DiscoveryResults(api: JzApi, list: DiscoveryResult, state: AiDiscoveryScreenState,
                             memory: FocusMemory, onOpen: (DiscoveryItem, Int, Int) -> Unit) {
    if (list.items.isEmpty()) return
    key(list) {
        val targetIndex = remember {
            val focusKey = state.restoreFocus ?: list.comparison.firstOrNull()?.key
            list.items.indexOfFirst { it.key == focusKey }.coerceAtLeast(0)
        }
        val scroll = rememberLazyListState(state.firstIndex.coerceIn(list.items.indices), state.firstOffset)
        val focus = remember { FocusRequester() }
        var focusPending by remember { mutableStateOf(true) }
        var info by remember { mutableStateOf<Pair<String, List<String>>?>(null) }
        LaunchedEffect(Unit) {
            withFrameNanos { }
            if (scroll.layoutInfo.visibleItemsInfo.none { it.index == targetIndex }) scroll.scrollToItem(targetIndex)
        }
        LazyColumn(state = scroll, contentPadding = PaddingValues(6.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)) {
            itemsIndexed(list.items, key = { _, item -> item.key }) { index, item ->
                val target = item.target
                val online = target is DiscoveryTarget.Online
                val actionable = target != null && (!online || api.supportsOnlineVod)
                val comparison = list.comparison.firstOrNull { it.key == item.key }?.text.orEmpty()
                val description = comparison.ifBlank { item.reason }
                val label = when {
                    online && actionable -> "在线搜索"
                    online -> "尚未入库"
                    target is DiscoveryTarget.Detail && list.source == "public" -> "已入库"
                    else -> ""
                }
                val mediaKind = when (item.kind) {
                    "movie" -> "电影"
                    "tv", "show" -> "剧集"
                    "season" -> "季"
                    "episode" -> "分集"
                    else -> ""
                }
                val details = listOf(if (comparison.isNotBlank()) "对比" else "", mediaKind, item.year, item.ratingLabel, item.watchLabel, label)
                    .filter(String::isNotBlank).joinToString(" · ")
                Button(onClick = {
                    if (actionable) onOpen(item, scroll.firstVisibleItemIndex, scroll.firstVisibleItemScrollOffset)
                    else info = item.title to listOf(details, description).filter(String::isNotBlank)
                }, modifier = Modifier.fillMaxWidth().focusMemory(memory, "discovery:item:${item.key}",
                    focus.takeIf { index == targetIndex }), shape = tvButtonShape(card = true), scale = tvButtonScale(),
                    colors = tvButtonColors(surface = DesignTokens.Surface),
                    contentPadding = PaddingValues(horizontal = 14.dp, vertical = 10.dp)) {
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        if (item.poster.isNotBlank()) Poster(api, item.poster, item.title, Modifier.size(48.dp, 70.dp),
                            showPlaceholderLabel = false)
                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                            Text("${index + 1}. ${item.title}", style = MaterialTheme.typography.titleMedium)
                            if (details.isNotBlank()) Text(details, style = MaterialTheme.typography.labelLarge)
                            if (description.isNotBlank()) Text(description, style = MaterialTheme.typography.bodyMedium,
                                maxLines = if (list.comparison.isEmpty()) 3 else 8, overflow = TextOverflow.Ellipsis)
                        }
                        if (actionable) TvIcon("chevron-right")
                    }
                }
                if (index == targetIndex && focusPending) LaunchedEffect(Unit) {
                    withFrameNanos { }
                    if (!memory.leaving) focus.requestFocus()
                    focusPending = false
                }
            }
        }
        info?.let { (title, lines) -> TextDialog(title, lines) { info = null } }
    }
}
