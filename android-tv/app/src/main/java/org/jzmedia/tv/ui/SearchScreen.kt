package org.jzmedia.tv.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusProperties
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.tv.material3.Button
import androidx.tv.material3.ButtonDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import org.jzmedia.tv.data.ApiException
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.SEARCH_QUERY_LIMIT
import org.jzmedia.tv.data.TV_SEARCH_KEYS
import org.jzmedia.tv.data.appendSearchInput
import org.jzmedia.tv.data.actorSearchPath
import org.jzmedia.tv.data.deleteSearchInput
import org.jzmedia.tv.data.rows
import org.jzmedia.tv.data.searchKind
import org.jzmedia.tv.data.text
import org.jzmedia.tv.data.tvSearchPath
import org.json.JSONObject

/** The keyboard is part of the app, so ordinary TV search never requires a system IME. */
@Composable
fun SearchScreen(api: JzApi, library: Long, initialKind: String, memory: FocusMemory, navigate: (TvRoute) -> Unit, back: () -> Unit) {
    var mode by rememberSaveable { mutableStateOf("title") }
    var titleQuery by rememberSaveable { mutableStateOf("") }
    var actorQuery by rememberSaveable { mutableStateOf("") }
    val actorsMode = mode == "actor"
    val query = if (actorsMode) actorQuery else titleQuery
    var kind by rememberSaveable { mutableStateOf(searchKind(initialKind)) }
    var page by rememberSaveable { mutableStateOf(0) }
    var lastKey by rememberSaveable { mutableStateOf("A") }
    var editing by rememberSaveable { mutableStateOf(false) }
    var restoreInput by remember { mutableStateOf(false) }
    var data by remember { mutableStateOf<JSONObject?>(null) }
    var error by remember { mutableStateOf("") }
    var loading by remember { mutableStateOf(false) }
    var attempt by remember { mutableStateOf(0) }
    var focusResults by remember { mutableStateOf(false) }
    var resetResults by remember { mutableStateOf(false) }
    val keys = remember { TV_SEARCH_KEYS.associateWith { FocusRequester() } }
    val firstResult = remember { FocusRequester() }
    val inputButton = remember { FocusRequester() }
    val grid = rememberLazyGridState()
    val resultEntryIndex by remember { derivedStateOf { grid.firstVisibleItemIndex } }
    val softwareKeyboard = LocalSoftwareKeyboardController.current
    val rows = data?.rows().orEmpty()

    fun changeQuery(value: String) {
        val normalized = appendSearchInput("", value)
        if (normalized == query && page == 0) return
        if (memory.key.startsWith("result:")) keys.getValue(lastKey).requestFocus()
        if (actorsMode) actorQuery = normalized else titleQuery = normalized
        page = 0
        data = null
        error = ""
        focusResults = false
        resetResults = true
    }
    LaunchedEffect(editing, restoreInput) {
        if (!editing && restoreInput) {
            withFrameNanos { }
            inputButton.requestFocus()
            restoreInput = false
        }
    }

    LaunchedEffect(api, library, query, mode, kind, page, attempt) {
        data = null; error = ""; loading = actorsMode || query.isNotBlank()
        if (!actorsMode && query.isBlank()) return@LaunchedEffect
        try {
            delay(250)
            val response = api.get(if (actorsMode) actorSearchPath(query, library, page) else tvSearchPath(query, kind, library, page))
            coroutineContext.ensureActive()
            data = response
        } catch (e: CancellationException) { throw e }
        catch (e: Exception) {
            error = if (e is ApiException && e.status == 404)
                if (actorsMode) "服务器尚不支持演员搜索，请更新服务器后重试。" else "服务器尚不支持首字母搜索，请更新服务器后重试。"
            else e.message ?: "搜索失败，请重试"
        } finally { loading = false }
    }
    LaunchedEffect(data) {
        if (data != null && (focusResults || resetResults)) {
            if (rows.isNotEmpty()) {
                grid.scrollToItem(0)
                withFrameNanos { }
                if (focusResults) firstResult.requestFocus()
            } else if (focusResults) keys.getValue(lastKey).requestFocus()
            focusResults = false
            resetResults = false
        }
    }
    fun turnPage(next: Int) { page = next; focusResults = true }

    Column(Modifier.fillMaxSize().onPreviewKeyEvent { event ->
        if (editing || event.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
        val native = event.nativeKeyEvent
        if (native.keyCode == android.view.KeyEvent.KEYCODE_DEL) {
            changeQuery(deleteSearchInput(query)); true
        } else if (!native.isCtrlPressed && !native.isAltPressed && native.unicodeChar >= 32 && !Character.isISOControl(native.unicodeChar)) {
            changeQuery(appendSearchInput(query, String(Character.toChars(native.unicodeChar)))); true
        } else false
    }, verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(14.dp), verticalAlignment = Alignment.CenterVertically) {
            TvAction("‹ 返回", back, Modifier.focusMemory(memory, "back"))
            Text("搜索", style = MaterialTheme.typography.headlineMedium, modifier = Modifier.padding(end = 12.dp))
            listOf("title" to "片名", "actor" to "演员").forEach { (value, label) ->
                TvAction(label, {
                    if (mode != value) {
                        mode = value; page = 0; data = null; error = ""; focusResults = false; resetResults = true
                    }
                }, Modifier.focusMemory(memory, "mode:$value"), selected = mode == value)
            }
        }
        BoxWithConstraints(Modifier.weight(1f).fillMaxWidth()) {
            val keyboardWidth = (maxWidth * .3f).coerceIn(264.dp, 328.dp)
            val keyHeight = ((maxHeight - 174.dp) / 6f - 5.dp).coerceIn(28.dp, 44.dp)
            Row(Modifier.fillMaxSize(), horizontalArrangement = Arrangement.spacedBy(28.dp)) {
                Column(Modifier.width(keyboardWidth).fillMaxHeight().verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Box(Modifier.fillMaxWidth().height(48.dp).background(Panel, RoundedCornerShape(8.dp)).padding(horizontal = 14.dp), contentAlignment = Alignment.CenterStart) {
                        Text(query.ifEmpty { if (actorsMode) "输入姓名首字母" else "输入片名首字母" }, color = if (query.isEmpty()) Muted else Color.White,
                            style = MaterialTheme.typography.titleLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                    Text(if (actorsMode) "首字母 / 全拼 / 英文 · ZXC 找周星驰" else "首字母 / 全拼 / 英文 · 例如 SQ 找沙丘",
                        style = MaterialTheme.typography.labelMedium, color = Muted)
                    Column(verticalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.padding(4.dp)) {
                        TV_SEARCH_KEYS.chunked(6).forEachIndexed { rowIndex, group ->
                            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                                group.forEachIndexed { column, letter ->
                                    val index = rowIndex * 6 + column
                                    Button(onClick = { changeQuery(appendSearchInput(query, letter)) },
                                        modifier = Modifier.weight(1f).height(keyHeight)
                                            .focusProperties {
                                                if (column > 0) left = keys.getValue(TV_SEARCH_KEYS[index - 1]) else left = FocusRequester.Cancel
                                                if (column < 5) right = keys.getValue(TV_SEARCH_KEYS[index + 1])
                                                else if (rows.isNotEmpty() && !loading) right = firstResult
                                                if (rowIndex > 0) up = keys.getValue(TV_SEARCH_KEYS[index - 6])
                                                if (rowIndex < 5) down = keys.getValue(TV_SEARCH_KEYS[index + 6])
                                            }
                                            .focusMemory(memory, "key:$letter", keys.getValue(letter))
                                            .onFocusChanged { if (it.isFocused) lastKey = letter },
                                        contentPadding = PaddingValues(0.dp),
                                        shape = ButtonDefaults.shape(shape = RoundedCornerShape(6.dp)),
                                        colors = ButtonDefaults.colors(containerColor = Panel, contentColor = Color.White,
                                            focusedContainerColor = Color.White, focusedContentColor = Color.Black),
                                    ) { Text(letter, style = MaterialTheme.typography.titleMedium) }
                                }
                            }
                        }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        TvAction("删除", { changeQuery(deleteSearchInput(query)) }, Modifier.weight(1f).focusMemory(memory, "delete"))
                        TvAction("清空", { changeQuery("") }, Modifier.weight(1f).focusMemory(memory, "clear"))
                        TvAction("中文输入", { editing = true }, Modifier.weight(1.5f).focusMemory(memory, "input", inputButton))
                    }
                    Text(if (query.codePointCount(0, query.length) >= SEARCH_QUERY_LIMIT) "最多输入 $SEARCH_QUERY_LIMIT 个字符" else "方向键选字母 · 右移查看候选",
                        style = MaterialTheme.typography.labelMedium, color = Muted)
                }
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(if (data == null) { if (actorsMode) "本库演员" else "片名联想" }
                            else "${data!!.optInt("total")} ${if (actorsMode) "位演员" else "项"} · 第 ${page + 1} 页",
                            style = MaterialTheme.typography.labelLarge, modifier = Modifier.weight(1f), maxLines = 2, overflow = TextOverflow.Ellipsis)
                        if (!actorsMode) listOf("all" to "全部", "movie" to "电影", "show" to "电视剧", "collection" to "合集").forEach { (value, label) ->
                            TvAction(label, {
                                if (kind != value || page != 0) { kind = value; page = 0; data = null; focusResults = false; resetResults = true }
                            }, Modifier.focusMemory(memory, "scope:$value"), selected = kind == value)
                        }
                    }
                    when {
                        error.isNotBlank() -> Status(error, { attempt++ })
                        !actorsMode && query.isBlank() -> Status("输入几个首字母，就能查看当前媒体库中的候选片名。")
                        loading || data == null -> Status("正在搜索…")
                        rows.isEmpty() -> Status(if (actorsMode) {
                            if (query.isBlank()) "当前媒体库中还没有可浏览的演员。" else "没有找到相关演员，试试更短的首字母或其他姓名。"
                        } else "没有找到相关内容，试试更短的首字母或其他片名。")
                        else -> {
                            BoxWithConstraints(Modifier.weight(1f)) {
                                val columns = ((maxWidth + 2.dp) / (if (actorsMode) 244.dp else 168.dp)).toInt().coerceAtLeast(1)
                                LazyVerticalGrid(columns = GridCells.Fixed(columns), state = grid,
                                    contentPadding = PaddingValues(8.dp), horizontalArrangement = Arrangement.spacedBy(18.dp),
                                    verticalArrangement = Arrangement.spacedBy(18.dp)) {
                                    itemsIndexed(rows, key = { _, row -> if (actorsMode) "actor:${row.text("key")}" else "${row.text("kind")}:${row.optLong("id")}" }) { index, row ->
                                        val resultKey = if (actorsMode) "result:actor:${row.text("key")}" else "result:${row.text("kind")}:${row.optLong("id")}"
                                        val modifier = Modifier.focusProperties { if (index % columns == 0) left = keys.getValue(lastKey) }
                                            .focusMemory(memory, resultKey, if (index == resultEntryIndex) firstResult else null)
                                        if (actorsMode) ActorCard(api, row, {
                                            navigate(TvRoute("actor-works", title = row.text("name"), actorKey = row.text("key")))
                                        }, modifier)
                                        else {
                                            val resultKind = row.text("kind")
                                            MediaCard(api, row, { navigate(TvRoute(resultKind, row.optLong("id"))) }, modifier,
                                                listOf(when (resultKind) { "movie" -> "电影"; "show" -> "电视剧"; else -> "合集" }, row.text("year")).filter { it.isNotBlank() }.joinToString(" · "))
                                        }
                                    }
                                }
                            }
                            Row(horizontalArrangement = Arrangement.spacedBy(14.dp), verticalAlignment = Alignment.CenterVertically) {
                                if (page > 0) TvAction("上一页", { turnPage(page - 1) }, Modifier.focusMemory(memory, "previous-search"))
                                if (data!!.optBoolean("has_more")) TvAction("下一页", { turnPage(page + 1) }, Modifier.focusMemory(memory, "next-search"))
                            }
                        }
                    }
                }
            }
        }
    }
    if (editing) {
        var draft by remember { mutableStateOf(query) }
        val dialogInput = remember { FocusRequester() }
        fun close() { editing = false; softwareKeyboard?.hide(); restoreInput = true }
        Dialog(onDismissRequest = { close() }, properties = DialogProperties(usePlatformDefaultWidth = false)) {
            LaunchedEffect(Unit) { withFrameNanos { }; dialogInput.requestFocus() }
            Column(Modifier.width(640.dp).background(Panel, RoundedCornerShape(16.dp)).padding(28.dp), verticalArrangement = Arrangement.spacedBy(20.dp)) {
                Text(if (actorsMode) "输入演员姓名" else "输入片名", style = MaterialTheme.typography.headlineSmall)
                TvInput("支持中文、英文或拼音", draft, { draft = appendSearchInput("", it) }, Modifier.focusRequester(dialogInput))
                Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                    TvAction("搜索", { changeQuery(draft); close() })
                    TvAction("取消", { close() })
                }
            }
        }
    }
}
