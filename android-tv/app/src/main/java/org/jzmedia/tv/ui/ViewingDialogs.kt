package org.jzmedia.tv.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import org.jzmedia.tv.data.ApiException
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.queryPath
import org.jzmedia.tv.data.text
import org.jzmedia.tv.ui.generated.DesignTokens

internal data class ViewingDialogRequest(val kind: String = "modes", val mediaKind: String = "", val id: Long = 0)

internal data class ContentVisibilitySummary(val normal: String, val children: String, val privateMode: String)

internal fun contentVisibilitySummary(isPrivate: Boolean, children: String, autoStatus: String): ContentVisibilitySummary {
    val childResult = when {
        isPrivate || children == "block" -> "不显示"
        children == "allow" || autoStatus == "allow" -> "显示"
        autoStatus == "block" -> "不显示"
        else -> "不显示（资料不足）"
    }
    return ContentVisibilitySummary(if (isPrivate) "不显示" else "显示", childResult, if (isPrivate) "显示" else "不显示")
}

/** Private content retains its earlier parental decision for a later move back to normal browsing. */
internal fun contentVisibilityPatch(kind: String, id: Long, isPrivate: Boolean, children: String): JSONObject =
    JSONObject().put("kind", kind).put("ids", JSONArray(listOf(id))).put("is_private", isPrivate).apply {
        if (!isPrivate) put("children_override", children)
    }

@Composable
private fun VisibilityChoice(label: String, description: String, selected: Boolean, enabled: Boolean,
                             modifier: Modifier = Modifier, onClick: () -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        TvAction(label, onClick, modifier.fillMaxWidth(), selected = selected, enabled = enabled,
            fillContent = true, focusScale = 1f)
        Text(description, color = Muted, modifier = Modifier.padding(horizontal = 14.dp))
    }
}

/** Password fields deliberately use remember, never saved-instance state. */
@Composable
internal fun ViewingDialog(api: JzApi, request: ViewingDialogRequest, onClose: () -> Unit,
                           onSession: (JzApi, Boolean) -> Unit, onPolicySaved: () -> Unit,
                           onUserActivity: () -> Unit) {
    var current by remember { mutableStateOf(api) }
    var form by remember { mutableStateOf(if (request.kind == "password") "password" else "") }
    var password by remember { mutableStateOf("") }
    var newPassword by remember { mutableStateOf("") }
    var confirmation by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf("") }
    var policy by remember { mutableStateOf<JSONObject?>(null) }
    var isPrivate by remember { mutableStateOf(false) }
    var children by remember { mutableStateOf("auto") }
    val scope = rememberCoroutineScope()
    val memory = key(form, policy != null) {
        rememberFocusMemory(if (request.kind == "policy" && policy != null && isPrivate) "viewing:private" else "viewing:first")
    }
    val height = tvDialogMaxHeight(.9f)
    val setup = current.viewing?.enabled != true
    val policyLoaded = form.isBlank() && request.kind == "policy" && policy != null
    val dialogScroll = rememberScrollState()
    val optionsScroll = rememberScrollState()

    fun run(action: suspend () -> Unit) {
        if (busy) return
        busy = true; error = ""
        scope.launch {
            try { action() }
            catch (e: CancellationException) { throw e }
            catch (e: Exception) { error = e.message ?: "操作失败，请重试" }
            finally { busy = false }
        }
    }
    suspend fun loadPolicy() {
        policy = current.get(queryPath("/api/viewing/policy", mapOf("kind" to request.mediaKind, "id" to request.id)))
        isPrivate = policy!!.optBoolean("is_private")
        children = policy!!.text("children_override", "auto")
    }
    fun changeMode(mode: String) {
        if (mode == current.viewing?.mode) { onClose(); return }
        if ((mode != "normal" && setup) || mode == "private" || current.viewing?.mode == "children") {
            form = "mode:$mode"; error = ""; return
        }
        run {
            val updated = current.withViewing(current.post("/api/viewing/mode", JSONObject().put("mode", mode)))
            onSession(updated, true); onClose()
        }
    }
    fun submitPassword() {
        if ((setup || form == "password") && (newPassword.length !in 6..128 || newPassword != confirmation)) {
            error = if (newPassword.length !in 6..128) "保护密码应为 6–128 个字符" else "两次输入的新密码不一致"
            return
        }
        run {
            val chosen = if (setup || form == "password") newPassword else password
            if (setup || form == "password") {
                current = current.withViewing(current.post("/api/viewing/password", JSONObject()
                    .put("current_password", password).put("new_password", newPassword)))
                if (form == "password") onSession(current, false)
            }
            when {
                form == "password" -> onClose()
                form.startsWith("mode:") -> {
                    current = current.withViewing(current.post("/api/viewing/mode", JSONObject()
                        .put("mode", form.substringAfter(':')).put("password", chosen)))
                    onSession(current, true); onClose()
                }
                else -> {
                    current = current.withViewing(current.post("/api/viewing/unlock", JSONObject().put("password", chosen)))
                    onSession(current, false)
                    form = ""; loadPolicy()
                }
            }
            password = ""; newPassword = ""; confirmation = ""
        }
    }
    LaunchedEffect(request) {
        if (request.kind == "policy") {
            if (current.viewing?.managementUnlocked != true) form = "unlock"
            else run { loadPolicy() }
        }
    }
    BackHandler { if (!busy) onClose() }
    Dialog(onDismissRequest = { if (!busy) onClose() }, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Column(Modifier.fillMaxWidth(.72f).heightIn(max = height)
            .onPreviewKeyEvent { onUserActivity(); false }
            .background(Panel, RoundedCornerShape(DesignTokens.DialogRadius)).padding(24.dp)
            .then(if (policyLoaded) Modifier else Modifier.verticalScroll(dialogScroll)),
            verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Text(when {
                form.isNotBlank() && setup -> "设置保护密码"
                form == "password" -> "修改保护密码"
                form.isNotBlank() -> "输入保护密码"
                request.kind == "policy" -> "内容可见性"
                else -> "观看模式"
            }, style = MaterialTheme.typography.headlineSmall)
            if (form.isNotBlank()) {
                Text("保护密码用于进入隐私模式、退出儿童模式和修改保护设置。密码全家共用，观看模式只影响当前设备。", color = Muted)
                if (!setup) TvInput(if (form == "password") "当前保护密码" else "保护密码", password,
                    { password = it; onUserActivity() }, Modifier.focusMemory(memory, "viewing:first"), password = true)
                if (setup || form == "password") {
                    TvInput("新保护密码（6–128 个字符）", newPassword, { newPassword = it; onUserActivity() },
                        if (setup) Modifier.focusMemory(memory, "viewing:first") else Modifier, password = true)
                    TvInput("再次输入新密码", confirmation, { confirmation = it; onUserActivity() }, password = true)
                }
                TvAction(if (busy) "正在验证…" else "确认", ::submitPassword,
                    enabled = !busy)
            } else if (request.kind == "policy") {
                val value = policy
                if (value == null) {
                    Status(if (busy) "正在读取可见性设置…" else "可见性设置尚未读取", icon = if (busy) "loading" else "info")
                    if (!busy) TvAction("重试", { run { loadPolicy() } }, Modifier.focusMemory(memory, "viewing:first"))
                } else {
                    Column(Modifier.weight(1f, fill = false).verticalScroll(optionsScroll),
                        verticalArrangement = Arrangement.spacedBy(16.dp)) {
                        Text("展示范围", style = MaterialTheme.typography.titleMedium)
                        VisibilityChoice("普通模式可见", "普通模式显示，隐私模式隐藏；儿童模式在下方设置。",
                            selected = !isPrivate, enabled = !busy, modifier = Modifier.focusMemory(memory, "viewing:first")) {
                            isPrivate = false
                        }
                        VisibilityChoice("仅隐私模式可见", "需要密码进入；普通和儿童模式均隐藏。",
                            selected = isPrivate, enabled = !busy, modifier = Modifier.focusMemory(memory, "viewing:private")) {
                            isPrivate = true
                        }
                        if (isPrivate) {
                            Text("隐私内容不在普通或儿童模式显示。儿童设置会保留，移回普通模式时可再调整。", color = Muted)
                        } else {
                            Text("儿童模式是否显示", style = MaterialTheme.typography.titleMedium)
                            val autoResult = when (value.text("children_auto_status")) {
                                "allow" -> "当前自动结果：显示。"
                                "block" -> "当前自动结果：不显示。"
                                else -> "当前自动结果：不显示（资料不足）。"
                            }
                            VisibilityChoice("自动筛选", autoResult + value.text("children_reason"),
                                selected = children == "auto", enabled = !busy) { children = "auto" }
                            VisibilityChoice("普通和儿童模式都显示", "我已确认适合儿童观看。",
                                selected = children == "allow", enabled = !busy) { children = "allow" }
                            VisibilityChoice("只在普通模式显示", "儿童模式隐藏。",
                                selected = children == "block", enabled = !busy) { children = "block" }
                        }
                        Text(if (request.mediaKind == "show") "此设置适用于整部剧的所有季和集。" else "此设置适用于同一视频库内这部电影的所有版本。", color = Muted)
                    }
                    val result = contentVisibilitySummary(isPrivate, children, value.text("children_auto_status"))
                    Column(Modifier.fillMaxWidth().background(DesignTokens.Background, RoundedCornerShape(DesignTokens.CornerRadius)).padding(14.dp),
                        verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text("保存后", style = MaterialTheme.typography.titleMedium)
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            listOf("普通模式" to result.normal, "儿童模式" to result.children, "隐私模式" to result.privateMode).forEach { (mode, visibility) ->
                                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                    Text(mode, color = Muted)
                                    Text(visibility)
                                }
                            }
                        }
                    }
                    if (error.isNotBlank()) Text(error, color = DesignTokens.Error)
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        TvAction(if (busy) "正在保存…" else "保存", {
                            run {
                                try {
                                    current.patch("/api/viewing/policy", contentVisibilityPatch(request.mediaKind, request.id, isPrivate, children))
                                    try { current = current.withViewing(current.get("/api/viewing/session")); onSession(current, true) }
                                    catch (e: CancellationException) { throw e }
                                    catch (_: Exception) { /* The save succeeded; clear the old view even if refreshing the session fails. */ }
                                    onPolicySaved(); onClose()
                                } catch (e: ApiException) {
                                    if (e.code == "management_required") { form = "unlock"; policy = null }
                                    throw e
                                }
                            }
                        }, enabled = !busy)
                        TvAction("取消", onClose, enabled = !busy)
                    }
                }
            } else {
                Text("普通模式隐藏手工加入隐私的内容；儿童模式只展示适合儿童的内容；隐私模式只展示手工加入的内容。", color = Muted)
                listOf("normal" to "普通模式", "children" to "儿童模式", "private" to "隐私模式").forEachIndexed { index, (mode, label) ->
                    TvAction(label, { changeMode(mode) }, if (index == 0) Modifier.focusMemory(memory, "viewing:first") else Modifier,
                        selected = current.viewing?.mode == mode, enabled = !busy)
                }
                if (current.viewing?.mode == "private") Text("要添加内容，先退出隐私模式，再从影片或剧集的“更多操作 → 内容可见性”加入。", color = Muted)
            }
            if (!policyLoaded) {
                if (error.isNotBlank()) Text(error, color = DesignTokens.Error)
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    TvAction("取消", onClose, enabled = !busy)
                }
            }
        }
    }
}
