package org.jzmedia.tv.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.tv.material3.Button
import androidx.tv.material3.ButtonDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text

private data class NavigationItem(val kind: String, val label: String)
private val ChannelItems = listOf(
    NavigationItem("search", "搜索"), NavigationItem("home", "首页"), NavigationItem("movies", "电影"),
    NavigationItem("shows", "电视剧"), NavigationItem("collections", "合集"),
)

@Composable
internal fun TvNavigation(section: String, libraryName: String, memory: FocusMemory, open: (String) -> Unit) {
    val tools = listOf(NavigationItem("libraries", libraryName), NavigationItem("settings", "设置"))
    val allItems = ChannelItems + tools
    val labelStyle = MaterialTheme.typography.labelLarge.copy(fontSize = 16.sp, lineHeight = 20.sp, fontWeight = FontWeight.Medium)
    val brandStyle = MaterialTheme.typography.labelMedium.copy(fontSize = 14.sp)
    val measurer = rememberTextMeasurer()
    val density = LocalDensity.current
    val widths = remember(libraryName, labelStyle, density, measurer) {
        allItems.associate { item ->
            val textWidth = with(density) { measurer.measure(AnnotatedString(item.label), labelStyle, maxLines = 1, softWrap = false).size.width.toDp() }
            val width = (textWidth + 56.dp).coerceAtLeast(80.dp)
            item.kind to if (item.kind == "libraries") width.coerceAtMost(176.dp) else width
        }
    }
    val brandWidth = with(density) { measurer.measure(AnnotatedString("jzmedia"), brandStyle, maxLines = 1).size.width.toDp() }
    val state = rememberLazyListState()
    LaunchedEffect(Unit) {
        if (!memory.restored && memory.restoreKey.startsWith("nav:")) {
            val index = allItems.indexOfFirst { "nav:${it.kind}" == memory.restoreKey }
            // A non-focusable spacer separates the five channels from the two tools.
            val rowIndex = if (index < ChannelItems.size) index else index + 1
            withFrameNanos { }
            if (index >= 0 && !memory.restored && state.layoutInfo.visibleItemsInfo.none { it.index == rowIndex }) {
                state.scrollToItem(rowIndex)
            }
        }
    }
    BoxWithConstraints(Modifier.fillMaxWidth().height(52.dp)) {
        val itemWidth = widths.values.fold(0.dp) { total, value -> total + value }
        val minimumWidth = itemWidth + 16.dp + 56.dp + 16.dp
        val showBrand = maxWidth >= minimumWidth + brandWidth + 32.dp
        val brandSpace = if (showBrand) brandWidth + 8.dp else 0.dp
        val spacerWidth = (maxWidth - itemWidth - 16.dp - 56.dp - brandSpace).coerceAtLeast(16.dp)
        LazyRow(state = state, modifier = Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically,
            contentPadding = PaddingValues(horizontal = 8.dp, vertical = 6.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            items(ChannelItems, key = { it.kind }) { item ->
                NavigationButton(item, widths.getValue(item.kind), labelStyle, section == item.kind, memory) { open(item.kind) }
            }
            item(key = "tools-space") { Spacer(Modifier.width(spacerWidth)) }
            items(tools, key = { it.kind }) { item ->
                NavigationButton(item, widths.getValue(item.kind), labelStyle, section == item.kind, memory) { open(item.kind) }
            }
            if (showBrand) item(key = "brand") {
                Text("jzmedia", style = brandStyle, color = Muted, maxLines = 1)
            }
        }
    }
}

@Composable
private fun NavigationButton(item: NavigationItem, width: Dp, labelStyle: TextStyle, isSelected: Boolean,
                             memory: FocusMemory, click: () -> Unit) {
    Button(click, Modifier.width(width).height(40.dp)
        .drawWithContent {
            drawContent()
            if (isSelected) {
                val lineWidth = 20.dp.toPx()
                drawRoundRect(Accent, Offset((size.width - lineWidth) / 2f, size.height - 5.dp.toPx()),
                    Size(lineWidth, 3.dp.toPx()), CornerRadius(1.5.dp.toPx()))
            }
        }
        .semantics { selected = isSelected }
        .focusMemory(memory, "nav:${item.kind}"),
        contentPadding = PaddingValues(horizontal = 12.dp, vertical = 4.dp),
        scale = ButtonDefaults.scale(focusedScale = 1.06f),
        shape = ButtonDefaults.shape(shape = RoundedCornerShape(10.dp)),
        colors = ButtonDefaults.colors(containerColor = if (isSelected) Panel else Color.Transparent,
            contentColor = Color(0xFFD0D0D0), focusedContainerColor = Color.White, focusedContentColor = Color(0xFF141414))) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            TvNavIcon(item.kind, Modifier.size(24.dp))
            Text(item.label, style = labelStyle, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}
