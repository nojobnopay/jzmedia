<template>
  <div class="obsolete-cleanup">
    <JzButton ref="checkButton" type="button" size="compact" icon="eye" :loading="busy" :disabled="disabled" @click="open">{{ label }}</JzButton>
    <template v-if="!dialog">
      <ObsoletePreviewCleanupResult v-if="candidate || lastResult" :candidate="candidate || lastResult" :summary="!!candidate"
        :actions="!!candidate" :busy="busy" :disabled="disabled" @confirm="clean" @cancel="cancel" />
      <p v-if="message" :class="{ 'warn-text': failed }" :role="failed ? 'alert' : 'status'">{{ message }}</p>
    </template>
    <JzDialog v-else-if="dialogOpen" title="清理废弃缩略图" :busy="busy" close-label="关闭废弃缩略图清理" @close="closeDialog">
      <p class="cleanup-scope">清理范围：全服务的全部媒体库。</p>
      <p v-if="contextName" class="hint">从“{{ contextName }}”的扫描结果进入；检查和清理也会包含其他媒体库。</p>
      <p v-if="replacementMissing" class="hint">以下保留原因是全部媒体库的合计；生成入口只进入本库，其他库的缺失预览需切换到对应库处理。</p>
      <p v-if="busy && checking" role="status">正在检查废弃缩略图…</p>
      <ObsoletePreviewCleanupResult v-if="candidate || lastResult" :candidate="candidate || lastResult" :summary="!!candidate" :actions="false" />
      <p v-if="message" :class="{ 'warn-text': failed }" :role="failed ? 'alert' : 'status'">{{ message }}</p>
      <template #footer>
        <JzButton v-if="replacementMissing" type="button" :disabled="busy || disabled" @click="generatePreviews">生成本库新版缩略图</JzButton>
        <JzButton v-if="failed || lastResult" type="button" icon="refresh" :disabled="busy || disabled" @click="preview">重新检查</JzButton>
        <JzButton type="button" :disabled="busy" @click="closeDialog">{{ candidate?.candidates || replacementMissing ? '稍后处理' : '关闭' }}</JzButton>
        <JzButton v-if="candidate?.candidates" type="button" variant="danger" :loading="busy && !checking" :disabled="busy || disabled" @click="clean">确认清理废弃缩略图</JzButton>
      </template>
    </JzDialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import { api } from '../api.js'
import { fmtBytes } from '../format.js'
import { activeFocusTraps } from '../useFocusTrap.js'
import JzButton from './JzButton.vue'
import JzDialog from './JzDialog.vue'
import ObsoletePreviewCleanupResult from './ObsoletePreviewCleanupResult.vue'

const props = defineProps({
  disabled: Boolean, dialog: Boolean, label: { type: String, default: '清理废弃缩略图' },
  contextName: { type: String, default: '' },
})
const emit = defineEmits(['checked', 'cleaned', 'busy-change', 'generate-previews'])
const endpoint = '/api/stream/previews/cache/clean-obsolete'
const candidate = ref(null), busy = ref(false), message = ref(''), failed = ref(false)
const dialogOpen = ref(false), checking = ref(false), lastResult = ref(null)
const replacementMissing = computed(() => Number((candidate.value || lastResult.value)?.skipped_details?.replacement_missing) > 0)
const checkButton = ref(null)
const controller = new AbortController()
function setBusy(value) {
  if (busy.value === value) return
  busy.value = value
  emit('busy-change', value)
}
async function restoreFocus() {
  await nextTick()
  if (!controller.signal.aborted && !props.disabled) checkButton.value?.focus()
}
async function open() {
  if (busy.value || props.disabled || props.dialog && activeFocusTraps.value > 0) return
  if (props.dialog) dialogOpen.value = true
  await preview()
}
async function preview() {
  if (busy.value || props.disabled) return
  setBusy(true)
  checking.value = true
  candidate.value = null
  lastResult.value = null
  message.value = ''
  failed.value = false
  try {
    const result = await api(endpoint, { method: 'POST', body: JSON.stringify({ dry_run: true }), signal: controller.signal })
    if (controller.signal.aborted) return
    candidate.value = result
    emit('checked', result)
  } catch (e) {
    if (!controller.signal.aborted) { failed.value = true; message.value = '检查失败：' + e.message }
  } finally { checking.value = false; setBusy(false) }
}
async function clean() {
  if (!candidate.value?.candidates || busy.value || props.disabled) return
  setBusy(true)
  message.value = ''
  failed.value = false
  let completed = false
  try {
    const result = await api(endpoint, { method: 'POST', body: JSON.stringify({ dry_run: false }), signal: controller.signal })
    if (controller.signal.aborted) return
    candidate.value = null
    lastResult.value = result
    message.value = Number(result.removed) > 0
      ? `已清理 ${result.removed} 个目录，释放 ${fmtBytes(result.freed)}，失败 ${result.failed} 个`
      : `本次未清理目录，释放 ${fmtBytes(result.freed)}，失败 ${result.failed} 个；可重新检查当前状态。`
    failed.value = Number(result.failed) > 0
    window.dispatchEvent(new Event('jzmedia:data-usage-changed'))
    emit('cleaned', result)
    completed = true
  } catch (e) {
    if (!controller.signal.aborted) { failed.value = true; message.value = '清理失败：' + e.message }
  } finally { setBusy(false) }
  if (completed && !props.dialog) await restoreFocus()
}
async function cancel() {
  if (busy.value || props.disabled) return
  candidate.value = null
  await restoreFocus()
}
async function closeDialog() {
  if (busy.value) return false
  dialogOpen.value = false
  candidate.value = null
  lastResult.value = null
  message.value = ''
  failed.value = false
  await restoreFocus()
  return true
}
async function generatePreviews() {
  if (!replacementMissing.value || busy.value || props.disabled) return
  if (await closeDialog() && !controller.signal.aborted) emit('generate-previews')
}
onBeforeUnmount(() => { controller.abort(); setBusy(false) })
</script>

<style scoped>
.obsolete-cleanup { margin-top: 12px; font-size: var(--jz-font-s); }
.obsolete-cleanup p { margin: 8px 0 0; line-height: 1.6; overflow-wrap: anywhere; }
.cleanup-scope { font-weight: 500; }
</style>
