package org.jzmedia.tv.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.background
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.OnlineCategory
import org.jzmedia.tv.data.OnlineLibraryEntry
import org.jzmedia.tv.data.OnlineSearchItem
import org.jzmedia.tv.playback.playbackTime
import org.jzmedia.tv.ui.generated.DesignTokens

@Composable
internal fun OnlineCatalogCard(api: JzApi, item: OnlineSearchItem, onClick: () -> Unit, modifier: Modifier = Modifier) {
    val caption = listOf(item.year, item.category).filter(String::isNotBlank).joinToString(" · ")
    PosterWallCard(item.title, caption, onClick, modifier) {
        Poster(api, item.posterPath.orEmpty(), item.title, Modifier.fillMaxSize(), showPlaceholderLabel = false)
        PosterBottomLabel(item.remarks)
    }
}

@Composable
internal fun OnlineRecordCard(api: JzApi, row: OnlineLibraryEntry, actions: @Composable () -> Unit) {
    Row(Modifier.fillMaxWidth().background(DesignTokens.Surface, RoundedCornerShape(DesignTokens.CardRadius)).padding(12.dp),
        horizontalArrangement = Arrangement.spacedBy(16.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.width(78.dp).aspectRatio(2f / 3f).clip(RoundedCornerShape(DesignTokens.CornerRadius))) {
            Poster(api, if (row.available) row.posterPath.orEmpty() else "", row.item.title,
                Modifier.fillMaxSize(), showPlaceholderLabel = false)
            row.progress?.let { progress ->
                if (progress.completed) PosterProgress(1f)
                else progress.duration?.let { PosterProgress((progress.position / it).toFloat()) }
            }
        }
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(row.item.title, style = MaterialTheme.typography.titleMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
            val metadata = listOf(row.source.name, row.episodeLabel).filter(String::isNotBlank).joinToString(" · ")
            if (metadata.isNotBlank()) Text(metadata, color = Muted, style = MaterialTheme.typography.labelMedium,
                maxLines = 2, overflow = TextOverflow.Ellipsis)
            row.progress?.let { progress ->
                val time = if (progress.completed) "已播放完毕" else "上次播放到 ${playbackTime(progress.position)}" +
                    (progress.duration?.let { " / ${playbackTime(it)}" } ?: "")
                Text(time, color = Muted, style = MaterialTheme.typography.labelMedium)
            }
            if (!row.available) Text(onlineAvailabilityReason(row.reason), color = Muted)
            actions()
        }
    }
}

@Composable
internal fun OnlineCategoryDialog(categories: List<OnlineCategory>, selected: String,
                                  onSelect: (String) -> Unit, onClose: () -> Unit, searchMode: Boolean = false) {
    val initial = (categories.indexOfFirst { it.id == selected } + 1).coerceAtLeast(0)
    val list = rememberLazyListState(initial)
    val focus = remember { FocusRequester() }
    val height = tvDialogMaxHeight(.86f)
    val tree = remember(categories) { onlineTaxonomy(categories) }
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Column(Modifier.fillMaxWidth(.7f).heightIn(max = height)
            .background(Panel, RoundedCornerShape(DesignTokens.DialogRadius)).padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("选择分类", style = MaterialTheme.typography.headlineSmall)
            LazyColumn(state = list, contentPadding = PaddingValues(8.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                item {
                    TvAction("全部分类", { onSelect("") },
                        if (initial == 0) Modifier.fillMaxWidth().focusRequester(focus) else Modifier.fillMaxWidth(), selected.isBlank(), fillContent = true)
                }
                items(categories, key = { it.id }) { category ->
                    val label = onlineCategoryLabel(categories, category.id) +
                        if (tree.channels.any { it.id == category.id && it.children.isNotEmpty() })
                            if (searchMode) "（全部子分类）" else "综合条目" else ""
                    TvAction(label, { onSelect(category.id) },
                        if (selected == category.id) Modifier.fillMaxWidth().focusRequester(focus) else Modifier.fillMaxWidth(),
                        selected == category.id, fillContent = true)
                }
            }
        }
        LaunchedEffect(Unit) { withFrameNanos { }; focus.requestFocus() }
    }
}
