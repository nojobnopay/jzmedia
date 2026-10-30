package org.jzmedia.tv.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.background
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.tv.material3.Button
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.OnlineCategory
import org.jzmedia.tv.data.OnlineSearchItem
import org.jzmedia.tv.ui.generated.DesignTokens

@Composable
internal fun OnlineCatalogCard(api: JzApi, item: OnlineSearchItem, onClick: () -> Unit, modifier: Modifier = Modifier) {
    Button(onClick, modifier, contentPadding = PaddingValues(MediaCardPadding),
        colors = tvButtonColors(surface = DesignTokens.Surface), shape = tvButtonShape(card = true), scale = tvButtonScale()) {
        Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(MediaCardArtworkGap)) {
            Poster(api, item.posterPath.orEmpty(), item.title, Modifier.fillMaxWidth().height(180.dp))
            Column(verticalArrangement = Arrangement.spacedBy(MediaCardTextGap)) {
                Text(item.title, style = MaterialTheme.typography.titleSmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                val caption = listOf(item.year, item.category, item.remarks).filter(String::isNotBlank).joinToString(" · ")
                if (caption.isNotBlank()) Text(caption, style = MaterialTheme.typography.labelSmall,
                    maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}

@Composable
internal fun OnlineCategoryDialog(categories: List<OnlineCategory>, selected: String,
                                  onSelect: (String) -> Unit, onClose: () -> Unit) {
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
                        if (tree.channels.any { it.id == category.id && it.children.isNotEmpty() }) "综合条目" else ""
                    TvAction(label, { onSelect(category.id) },
                        if (selected == category.id) Modifier.fillMaxWidth().focusRequester(focus) else Modifier.fillMaxWidth(),
                        selected == category.id, fillContent = true)
                }
            }
        }
        LaunchedEffect(Unit) { withFrameNanos { }; focus.requestFocus() }
    }
}
