package org.jzmedia.tv.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import org.jzmedia.tv.data.OnlineHostReport
import org.jzmedia.tv.ui.generated.DesignTokens

/** A report and its selections belong only to the detail that opened this dialog. */
internal class OnlineHostDialogState(
    val sourceId: String,
    val externalId: String,
    val itemId: String,
    val title: String,
    val sourceName: String,
    val scopeToken: String,
    val viewingMode: String?,
    val viewingGeneration: Long?,
    permissionRequired: Boolean,
) {
    var report by mutableStateOf<OnlineHostReport?>(null)
        private set
    var selected by mutableStateOf(emptySet<String>())
        private set
    var checking by mutableStateOf(false)
    var saving by mutableStateOf(false)
    var saved by mutableStateOf(false)
    var authorizing by mutableStateOf(false)
    var permissionRequired by mutableStateOf(permissionRequired)
    var error by mutableStateOf("")

    fun matches(sourceId: String, externalId: String, itemId: String?, scopeToken: String?,
                viewingMode: String?, viewingGeneration: Long?): Boolean =
        this.sourceId == sourceId && this.externalId == externalId && this.itemId == itemId &&
            this.scopeToken == scopeToken && this.viewingMode == viewingMode && this.viewingGeneration == viewingGeneration

    fun discardReport() { report = null; selected = emptySet() }
    fun accept(value: OnlineHostReport) {
        require(value.sourceId == sourceId && value.externalId == externalId)
        report = value
        selected = value.missingHosts.toSet()
    }
    fun toggle(host: String) {
        if (checking || saving || saved || report?.missingHosts?.contains(host) != true) return
        selected = if (host in selected) selected - host else selected + host
    }
    fun selectedHosts(): List<String> = report?.missingHosts.orEmpty().filter { it in selected }
}

/** A bounded native TV list keeps confirmation and cancellation reachable with a remote. */
@Composable
internal fun OnlineHostDialog(
    state: OnlineHostDialogState,
    onCheck: () -> Unit,
    onAuthorize: () -> Unit,
    onApply: () -> Unit,
    onReload: () -> Unit,
    onClose: () -> Unit,
) {
    val firstHost = remember { FocusRequester() }
    val action = remember { FocusRequester() }
    val close = remember { FocusRequester() }
    val list = rememberLazyListState()
    val report = state.report
    val hasCandidates = report?.missingHosts?.isNotEmpty() == true && !state.saved
    val reload = state.saved || report?.missingHosts?.isEmpty() == true
    val actionEnabled = !state.checking && !state.saving &&
        (state.permissionRequired || reload || report == null || state.selectedHosts().isNotEmpty())
    val firstCandidateIndex = 1 + (if (state.checking) 1 else 0) + (if (state.saving) 1 else 0) +
        (if (state.permissionRequired) 1 else 0) + (if (state.saved) 1 else 0) +
        (if (state.error.isNotBlank()) 1 else 0) + 1
    Dialog(onDismissRequest = { if (!state.saving) onClose() },
        properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Column(Modifier.fillMaxWidth(.75f).heightIn(max = tvDialogMaxHeight(.86f))
            .background(Panel, RoundedCornerShape(DesignTokens.DialogRadius)).padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("允许此来源的媒体主机", style = MaterialTheme.typography.headlineSmall)
            Text("视频源：${state.sourceName}", maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text("对本服务的此来源生效，所有设备共用；确认会关闭此来源正在播放的会话。", color = Muted)
            LazyColumn(state = list, modifier = Modifier.weight(1f, fill = false), contentPadding = PaddingValues(8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)) {
                item {
                    Text("仅读取当前内容资料，检查媒体及图片域名；不访问或自动添加新主机。只添加勾选域名，代理设置不变。", color = Muted,
                        style = MaterialTheme.typography.bodySmall)
                }
                if (state.checking) item { Status("正在检查媒体主机…", icon = "loading") }
                if (state.saving) item { Status(if (state.saved) "正在重新加载详情…" else "正在添加并刷新详情…", icon = "loading") }
                if (state.permissionRequired) item { Text("需要管理权限才能检查并添加媒体主机。", color = Muted) }
                if (state.saved) item { Text("媒体主机已加入允许列表。", color = Muted) }
                if (state.error.isNotBlank()) item { Text(state.error, color = DesignTokens.Error) }
                if (report != null && !state.saved) {
                    if (hasCandidates) {
                        item { Text("确认允许的域名（确定键勾选或取消）", style = MaterialTheme.typography.titleMedium) }
                        itemsIndexed(report.missingHosts, key = { _, host -> host }) { index, host ->
                            TvAction(host, { state.toggle(host) },
                                (if (index == 0) Modifier.focusRequester(firstHost) else Modifier).fillMaxWidth(),
                                selected = host in state.selected, enabled = !state.saving,
                                fillContent = true, focusScale = 1f, maxLines = Int.MAX_VALUE)
                        }
                    } else item { Text("没有发现需要新增的域名，可重新加载详情。", color = Muted) }
                    if (report.rejectedCount > 0) item { Text("部分地址格式不受支持，未列入候选。", color = Muted) }
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                TvAction(when {
                    state.checking -> "正在检查…"
                    state.saving -> "正在处理…"
                    state.permissionRequired -> "验证并检查"
                    reload -> "重新加载详情"
                    report != null -> "确认添加"
                    else -> "重新检查"
                }, {
                    when {
                        state.permissionRequired -> onAuthorize()
                        reload -> onReload()
                        report != null -> onApply()
                        else -> onCheck()
                    }
                }, Modifier.focusRequester(action), enabled = actionEnabled)
                TvAction(if (state.saved) "关闭" else "暂不添加", onClose,
                    Modifier.focusRequester(close), enabled = !state.saving)
            }
        }
        LaunchedEffect(report, state.permissionRequired, state.checking, state.saving, state.saved) {
            if (!state.saving) {
                if (hasCandidates && !state.checking && list.layoutInfo.visibleItemsInfo.none { it.index == firstCandidateIndex })
                    list.scrollToItem(firstCandidateIndex)
                withFrameNanos { }
                runCatching {
                    when {
                        hasCandidates && !state.checking -> firstHost.requestFocus()
                        actionEnabled -> action.requestFocus()
                        else -> close.requestFocus()
                    }
                }
            }
        }
    }
}
