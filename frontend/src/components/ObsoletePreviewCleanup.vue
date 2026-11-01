<template>
  <div class="obsolete-cleanup">
    <JzButton ref="checkButton" type="button" size="compact" icon="eye" :loading="busy" :disabled="disabled" @click="preview">清理废弃缩略图</JzButton>
    <div v-if="candidate" class="cleanup-confirmation" aria-label="废弃缩略图清理检查结果" role="region">
      <p>可清理 {{ candidate.candidates }} 个目录，释放约 {{ fmtBytes(candidate.candidate_bytes) }}。</p>
      <p>废弃原因：<span v-for="(item, index) in reasons" :key="item.key">{{ index ? ' · ' : '' }}{{ item.label }} {{ candidate.reasons?.[item.key] ?? 0 }}</span></p>
      <p>跳过：<span v-for="(item, index) in skips" :key="item.key">{{ index ? ' · ' : '' }}{{ item.label }} {{ candidate.skipped?.[item.key] ?? 0 }}</span></p>
      <p class="hint">确认后会重新检查状态，只删除仍确认废弃的目录。</p>
      <div class="bar">
        <JzButton v-if="candidate.candidates" type="button" variant="danger" :disabled="disabled" :loading="busy" @click="clean">确认清理废弃缩略图</JzButton>
        <JzButton type="button" :disabled="busy || disabled" @click="cancel">取消</JzButton>
      </div>
    </div>
    <p v-if="message" :class="{ 'warn-text': failed }" :role="failed ? 'alert' : 'status'">{{ message }}</p>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, ref } from 'vue'
import { api } from '../api.js'
import { fmtBytes } from '../format.js'
import JzButton from './JzButton.vue'

const props = defineProps({ disabled: Boolean })
const reasons = [
  { key: 'deleted', label: '媒体已删除' }, { key: 'replaced', label: '旧片源' },
  { key: 'superseded', label: '旧画质或抽帧规则' }, { key: 'invalid', label: '无法归属或损坏' },
]
const skips = [
  { key: 'in_use', label: '使用或排队' }, { key: 'offline', label: '离线或扫描未完成' },
  { key: 'protected', label: '最近访问或写入' }, { key: 'unknown', label: '状态无法确认' },
]
const endpoint = '/api/stream/previews/cache/clean-obsolete'
const candidate = ref(null), busy = ref(false), message = ref(''), failed = ref(false)
const checkButton = ref(null)
const controller = new AbortController()
async function preview() {
  if (busy.value || props.disabled) return
  busy.value = true
  candidate.value = null
  message.value = ''
  failed.value = false
  try {
    candidate.value = await api(endpoint, { method: 'POST', body: JSON.stringify({ dry_run: true }), signal: controller.signal })
  } catch (e) {
    if (!controller.signal.aborted) { failed.value = true; message.value = '检查失败：' + e.message }
  } finally { busy.value = false }
}
async function clean() {
  if (!candidate.value?.candidates || busy.value || props.disabled) return
  busy.value = true
  message.value = ''
  failed.value = false
  let completed = false
  try {
    const result = await api(endpoint, { method: 'POST', body: JSON.stringify({ dry_run: false }), signal: controller.signal })
    candidate.value = null
    message.value = `已清理 ${result.removed} 个目录，释放 ${fmtBytes(result.freed)}，失败 ${result.failed} 个`
    window.dispatchEvent(new Event('jzmedia:data-usage-changed'))
    completed = true
  } catch (e) {
    if (!controller.signal.aborted) { failed.value = true; message.value = '清理失败：' + e.message }
  } finally { busy.value = false }
  if (completed && !controller.signal.aborted && !props.disabled) {
    await nextTick()
    checkButton.value?.focus()
  }
}
async function cancel() { candidate.value = null; await nextTick(); checkButton.value?.focus() }
onBeforeUnmount(() => controller.abort())
</script>

<style scoped>
.obsolete-cleanup { margin-top: 12px; font-size: var(--jz-font-s); }
.obsolete-cleanup p { margin: 8px 0 0; line-height: 1.6; overflow-wrap: anywhere; }
.cleanup-confirmation { margin-top: 12px; padding: 12px; border: 1px solid var(--jz-warn-border); border-radius: var(--jz-radius-s); }
.cleanup-confirmation p:first-child { margin-top: 0; }
.cleanup-confirmation .bar { padding: 12px 0 0; flex-wrap: wrap; }
</style>
