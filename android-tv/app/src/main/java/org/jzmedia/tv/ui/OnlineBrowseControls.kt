package org.jzmedia.tv.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.produceState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.jzmedia.tv.data.OnlineHealth
import org.jzmedia.tv.data.OnlinePinyinDictionary
import org.jzmedia.tv.data.OnlineSource
import org.jzmedia.tv.data.TV_SEARCH_KEYS
import org.jzmedia.tv.data.deleteSearchInput
import org.jzmedia.tv.ui.generated.DesignTokens

@Composable
private fun OnlineQueryInput(value: String, onValue: (String) -> Unit, submit: () -> Unit, enabled: Boolean, modifier: Modifier) {
    var focused by remember { mutableStateOf(false) }
    val keyboard = LocalSoftwareKeyboardController.current
    val focus = LocalFocusManager.current
    BasicTextField(value = value, onValueChange = onValue, singleLine = true, enabled = enabled,
        textStyle = TextStyle(color = DesignTokens.TextStrong, fontSize = 16.sp),
        cursorBrush = SolidColor(DesignTokens.TextStrong),
        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
        keyboardActions = KeyboardActions(onSearch = { keyboard?.hide(); submit() }),
        modifier = modifier.heightIn(min = 40.dp).semantics { contentDescription = "搜索当前来源的片名" }
            .onFocusChanged { focused = it.isFocused }
            .onPreviewKeyEvent { event ->
                if (event.type == KeyEventType.KeyDown && event.key == Key.DirectionUp) focus.moveFocus(FocusDirection.Up)
                else if (event.type == KeyEventType.KeyDown && event.key == Key.DirectionDown) focus.moveFocus(FocusDirection.Down)
                else if (event.type == KeyEventType.KeyDown && event.key == Key.DirectionLeft) focus.moveFocus(FocusDirection.Left)
                else if (event.type == KeyEventType.KeyDown && event.key == Key.DirectionRight) focus.moveFocus(FocusDirection.Right)
                else if (event.type == KeyEventType.KeyUp && (event.key == Key.DirectionCenter || event.key == Key.Enter)) {
                    keyboard?.show(); true
                } else false
            }.border(if (focused) 3.dp else 1.dp, if (focused) DesignTokens.TextStrong else DesignTokens.Border,
                RoundedCornerShape(DesignTokens.CornerRadius)).background(Panel, RoundedCornerShape(DesignTokens.CornerRadius))
            .padding(horizontal = 12.dp, vertical = 8.dp),
        decorationBox = { field -> Box(contentAlignment = Alignment.CenterStart) {
            if (value.isBlank()) Text("搜索片名", style = MaterialTheme.typography.bodyMedium, color = Muted, maxLines = 1)
            field()
        } },
    )
}

@Composable
internal fun OnlineBrowseToolbar(section: String, query: String, source: OnlineSource?, sourceEnabled: Boolean,
                                 onSection: (String) -> Unit, onQuery: (String) -> Unit, onSearch: () -> Unit,
                                 onKeyboard: () -> Unit, onSource: () -> Unit, focus: (String) -> Modifier) {
    val fontScale = LocalDensity.current.fontScale
    val tabs: @Composable () -> Unit = {
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            listOf("search" to "浏览", "continue" to "继续", "favorites" to "收藏", "history" to "历史").forEach { (id, label) ->
                TvAction(label, { onSection(id) }, focus("online:tab:$id"), selected = section == id)
            }
        }
    }
    val tools: @Composable () -> Unit = {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            OnlineQueryInput(query, onQuery, onSearch, sourceEnabled, focus("online:query").weight(1f).widthIn(min = 100.dp))
            val openSample = source?.provider == "open_catalog" && !source.browseSupported && query.isBlank()
            TvAction(if (openSample) "目录" else "搜索", onSearch, focus("online:search"),
                enabled = sourceEnabled && (query.isNotBlank() || openSample), icon = "search")
            TvAction("键盘", onKeyboard, focus("online:keyboard"), enabled = sourceEnabled, icon = "keyboard")
            TvAction("来源：${source?.name ?: "选择"}", onSource, focus("online:source-button").widthIn(max = 170.dp),
                enabled = sourceEnabled, icon = "chevron-down")
        }
    }
    BoxWithConstraints(Modifier.fillMaxWidth()) {
        if (maxWidth < 800.dp || fontScale > 1.15f) {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) { tabs(); if (section == "search") tools() }
        } else {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                tabs()
                if (section == "search") Box(Modifier.weight(1f)) { tools() }
            }
        }
    }
}

@Composable
internal fun OnlineFilterRows(tree: OnlineTaxonomy, rootId: String, categoryId: String, year: String,
                              yearSupported: Boolean, onRoot: (String) -> Unit, onCategory: (String) -> Unit,
                              onMore: (String) -> Unit, onYear: (String) -> Unit, onOtherYear: () -> Unit,
                              focus: (String) -> Modifier) {
    val roots = onlineVisibleChannels(tree, rootId)
    val children = onlineVisibleCategories(tree, rootId, categoryId)
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("分类", color = Muted, style = MaterialTheme.typography.labelLarge)
            Row(Modifier.weight(1f).horizontalScroll(rememberScrollState()).padding(4.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TvAction("全部", { onRoot("") }, focus("online:root:all"), selected = rootId.isBlank())
                roots.forEach { channel ->
                    TvAction(channel.label, { onRoot(channel.id) }, focus("online:root:${channel.id}"), selected = rootId == channel.id)
                }
                if (tree.channels.size > roots.size) TvAction("更多", { onMore("") }, focus("online:more-roots"), icon = "filter")
            }
        }
        if (children.isNotEmpty()) key(rootId) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("细类", color = Muted, style = MaterialTheme.typography.labelLarge)
                Row(Modifier.weight(1f).horizontalScroll(rememberScrollState()).padding(4.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    children.forEach { category ->
                        TvAction(onlineTaxonomyLabel(tree, category.id), { onCategory(category.id) },
                            focus("online:category:${category.id}").widthIn(max = 190.dp), selected = categoryId == category.id)
                    }
                    if (onlineAllCategoriesForChannel(tree, rootId).size > children.size)
                        TvAction("更多", { onMore(rootId) }, focus("online:more-categories"), icon = "filter")
                }
            }
        }
        if (yearSupported) {
            val current = java.util.Calendar.getInstance().get(java.util.Calendar.YEAR).coerceIn(1004, 9999)
            val years = (0..4).map { (current - it).toString() }
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("年份", color = Muted, style = MaterialTheme.typography.labelLarge)
                Row(Modifier.weight(1f).horizontalScroll(rememberScrollState()).padding(4.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    TvAction("全部", { onYear("") }, focus("online:year:all"), selected = year.isBlank())
                    years.forEach { candidate -> TvAction(candidate, { onYear(candidate) }, focus("online:year:$candidate"), selected = year == candidate) }
                    val other = year.takeIf { it.isNotBlank() && it !in years }
                    TvAction(other ?: "其他", onOtherYear, focus("online:year:other"), selected = other != null)
                }
            }
        }
    }
}

@Composable
internal fun OnlineSourceDialog(sources: List<OnlineSource>, selected: String, health: OnlineHealth?, loading: Boolean,
                                error: String, onSelect: (String) -> Unit, onRefresh: () -> Unit, onClose: () -> Unit) {
    val enabled = sources.filter { it.enabled }
    val initial = enabled.indexOfFirst { it.id == selected }.coerceAtLeast(0)
    val list = rememberLazyListState(initial)
    val requester = remember { FocusRequester() }
    val height = tvDialogMaxHeight(.86f)
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Column(Modifier.fillMaxWidth(.7f).heightIn(max = height).background(Panel, RoundedCornerShape(DesignTokens.DialogRadius)).padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("选择视频源", style = MaterialTheme.typography.headlineSmall)
            LazyColumn(state = list, modifier = Modifier.weight(1f, fill = false), contentPadding = PaddingValues(8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(enabled, key = { it.id }) { source ->
                    TvAction(source.name, { onSelect(source.id) },
                        (if (source.id == enabled.getOrNull(initial)?.id) Modifier.focusRequester(requester) else Modifier).fillMaxWidth(),
                        selected = source.id == selected, fillContent = true)
                }
            }
            Text(if (loading) "正在读取最近状态…" else health?.let { "API：${it.api.label}　媒体：${it.media.label}" }
                ?: error.ifBlank { "API／媒体：未观察" }, color = Muted)
            Text("仅显示最近实际请求的状态，不主动探测。", color = Muted, style = MaterialTheme.typography.bodySmall)
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                TvAction("刷新状态", onRefresh, enabled = !loading, icon = "refresh")
                TvAction("关闭", onClose)
            }
        }
        LaunchedEffect(Unit) { if (enabled.isNotEmpty()) { withFrameNanos { }; requester.requestFocus() } }
    }
}

@Composable
internal fun OnlineSearchKeyboardDialog(query: String, onQuery: (String) -> Unit, onSubmit: () -> Unit, onClose: () -> Unit) {
    val requester = remember { FocusRequester() }
    val keyboardRequester = remember { FocusRequester() }
    val editRequester = remember { FocusRequester() }
    val firstCandidateRequester = remember { FocusRequester() }
    val height = tvDialogMaxHeight(.86f)
    val context = LocalContext.current
    val dictionaryResult by produceState<Result<OnlinePinyinDictionary>?>(null, context) {
        value = withContext(Dispatchers.IO) { runCatching { OnlinePinyinDictionary.load(context) } }
    }
    val dictionary = dictionaryResult?.getOrNull()
    var pinyinMode by remember { mutableStateOf(true) }
    var composition by remember { mutableStateOf("") }
    var page by remember { mutableStateOf(0) }
    var hint by remember { mutableStateOf("") }
    val candidates = remember(dictionary, composition) { dictionary?.candidates(composition).orEmpty() }
    val pageSize = 5
    val pageCount = ((candidates.size + pageSize - 1) / pageSize).coerceAtLeast(1)
    val visible = candidates.drop(page.coerceIn(0, pageCount - 1) * pageSize).take(pageSize)
    fun cannotLeave(): Boolean {
        if (composition.isEmpty()) return false
        hint = "请先选择候选、保留字母，或清空拼音。"
        return true
    }
    fun appendLiteral(value: String) {
        val next = query + value
        if (onlineSearchInput(next) == next) { onQuery(next); hint = "" } else hint = "片名最多输入 100 个字符。"
    }
    fun focusEditing() {
        if (!runCatching { keyboardRequester.requestFocus() }.getOrDefault(false))
            runCatching { editRequester.requestFocus() }
    }
    Dialog(onDismissRequest = { if (!cannotLeave()) onClose() }, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Column(Modifier.fillMaxWidth(.7f).heightIn(max = height).background(Panel, RoundedCornerShape(DesignTokens.DialogRadius))
            .verticalScroll(rememberScrollState()).padding(32.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            key("title") { Text("搜索当前来源", style = MaterialTheme.typography.headlineSmall) }
            key("input") { TvInput("片名（确定可使用系统输入法）", query, onQuery, Modifier.fillMaxWidth()) }
            key("mode-actions") {
                Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(4.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    TvAction("拼音", { if (!pinyinMode && !cannotLeave()) { pinyinMode = true; hint = "" } }, Modifier.focusRequester(requester), selected = pinyinMode)
                    TvAction("英文/数字", { if (!cannotLeave()) { pinyinMode = false; hint = "" } }, selected = !pinyinMode)
                    TvAction("搜索", { if (!cannotLeave()) onSubmit() }, enabled = query.isNotBlank() || composition.isNotBlank(), icon = "search")
                    TvAction("关闭", { if (!cannotLeave()) onClose() })
                }
            }
            key("edit-actions") {
                Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(4.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    TvAction("退格", {
                        if (composition.isNotEmpty()) { composition = composition.dropLast(1); page = 0 }
                        else onQuery(deleteSearchInput(query))
                        hint = ""
                    }, Modifier.focusRequester(editRequester), icon = "backspace")
                    TvAction("全部清空", { onQuery(""); composition = ""; page = 0; hint = "" }, icon = "trash")
                    if (composition.isNotEmpty()) {
                        TvAction("保留字母", {
                            val next = query + composition
                            if (onlineSearchInput(next) == next) { focusEditing(); onQuery(next); composition = ""; page = 0; hint = "" }
                            else hint = "片名最多输入 100 个字符。"
                        })
                        TvAction("清空拼音", { focusEditing(); composition = ""; page = 0; hint = "" })
                    }
                }
            }
            if (pinyinMode) {
                key("composition") {
                    Text(if (composition.isEmpty()) "输入拼音后选择候选词（v 表示 ü）" else "拼音：$composition",
                        color = if (composition.isEmpty()) Muted else DesignTokens.TextStrong)
                }
                if (composition.isNotEmpty()) {
                    key("candidates") {
                        if (visible.isEmpty()) Text(when {
                            dictionaryResult == null -> "正在准备拼音词库…"
                            dictionary == null -> "拼音词库不可用；可保留字母或使用系统输入法。"
                            else -> "没有候选，可退格、保留字母或使用系统输入法。"
                        }, color = Muted)
                        else Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(4.dp),
                            horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                            visible.forEachIndexed { index, candidate -> key("candidate:$index") {
                                TvAction(candidate.text, {
                                    val next = query + candidate.text
                                    if (onlineSearchInput(next) == next) {
                                        val remaining = composition.drop(candidate.consumed)
                                        if (dictionary?.candidates(remaining).isNullOrEmpty()) focusEditing()
                                        else if (!runCatching { firstCandidateRequester.requestFocus() }.getOrDefault(false)) focusEditing()
                                        onQuery(next); composition = remaining; page = 0; hint = ""
                                    } else hint = "片名最多输入 100 个字符。"
                                }, if (index == 0) Modifier.focusRequester(firstCandidateRequester) else Modifier)
                            } }
                            if (pageCount > 1) {
                                key("candidate:previous") { TvAction("上一页", { page = (page + pageCount - 1) % pageCount }) }
                                Text("${page + 1}/$pageCount", color = Muted)
                                key("candidate:next") { TvAction("下一页", { page = (page + 1) % pageCount }) }
                            }
                        }
                    }
                }
            }
            if (hint.isNotEmpty()) key("hint") { Text(hint, color = Muted) }
            TV_SEARCH_KEYS.chunked(6).forEachIndexed { rowIndex, keys ->
                key("keyboard:$rowIndex") {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        keys.forEachIndexed { columnIndex, value ->
                            TvAction(value, {
                                if (pinyinMode && value.singleOrNull()?.isLetter() == true) {
                                    if (composition.length >= OnlinePinyinDictionary.MAX_COMPOSITION) hint = "请先选择候选，再继续输入。"
                                    else if (query.length + composition.length < 100) { composition += value.lowercase(); page = 0; hint = "" }
                                    else hint = "片名最多输入 100 个字符。"
                                } else if (!cannotLeave()) appendLiteral(value)
                            }, (if (rowIndex == 0 && columnIndex == 0) Modifier.focusRequester(keyboardRequester) else Modifier).weight(1f), centerLabel = true)
                        }
                    }
                }
            }
        }
        LaunchedEffect(Unit) { withFrameNanos { }; requester.requestFocus() }
    }
}

@Composable
internal fun OnlineYearDialog(value: String, onValue: (String) -> Unit, onApply: () -> Unit, onClose: () -> Unit, error: String) {
    val requester = remember { FocusRequester() }
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Column(Modifier.fillMaxWidth(.55f).heightIn(max = tvDialogMaxHeight(.86f))
            .background(Panel, RoundedCornerShape(DesignTokens.DialogRadius)).padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("其他年份", style = MaterialTheme.typography.headlineSmall)
            TvInput("四位年份，留空为全部", value, onValue, Modifier.fillMaxWidth())
            if (error.isNotBlank()) Text(error, color = DesignTokens.Error)
            listOf("12345", "67890").forEachIndexed { rowIndex, digits ->
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    digits.forEachIndexed { index, digit ->
                        TvAction(digit.toString(), { onValue((value + digit).take(4)) },
                            (if (rowIndex == 0 && index == 0) Modifier.focusRequester(requester) else Modifier).weight(1f), centerLabel = true)
                    }
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TvAction("应用", onApply, icon = "filter")
                TvAction("退格", { onValue(value.dropLast(1)) }, icon = "backspace")
                TvAction("清空", { onValue("") }, icon = "trash")
                TvAction("取消", onClose)
            }
        }
        LaunchedEffect(Unit) { withFrameNanos { }; requester.requestFocus() }
    }
}
