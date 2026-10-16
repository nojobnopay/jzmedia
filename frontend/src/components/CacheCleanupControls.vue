<template>
  <div class="cache-cleanup">
    <JzButton ref="checkButton" type="button" size="compact" icon="eye" :loading="busy" :disabled="disabled" @click="preview">检查可清理空间</JzButton>
    <div v-if="candidate" class="cleanup-confirmation" :aria-label="label + '清理确认'" role="region">
      <p>可清理 {{ candidate.candidates }} 个目录，释放约 {{ fmtBytes(candidate.candidate_bytes) }}。{{ kind === 'previews' ? '清理后需要重新生成缩略图。' : '清理后再次播放可能需要重新缓存。' }}</p>
      <div class="bar">
        <JzButton type="button" variant="danger" :disabled="disabled" :loading="busy" @click="clean">{{ kind === 'previews' ? '确认清理缩略图' : '确认清理缓存' }}</JzButton>
        <JzButton type="button" :disabled="busy || disabled" @click="cancel">取消</JzButton>
      </div>
    </div>
    <p v-if="message" :class="{ 'warn-text': failed }" :role="failed ? 'alert' : 'status'">{{ message }}</p>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import { api } from '../api.js'
import { fmtBytes } from '../format.js'
import JzButton from './JzButton.vue'

const props = defineProps({ kind: { type: String, required: true }, disabled: Boolean })
const label = computed(() => props.kind === 'previews' ? '进度缩略图' : '播放缓存')
const endpoint = computed(() => props.kind === 'previews' ? '/api/stream/previews/cache/clean' : '/api/stream/cache/clean')
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
    const result = await api(endpoint.value, { method: 'POST', body: JSON.stringify({ dry_run: true }), signal: controller.signal })
    if (result.candidates) candidate.value = result
    else message.value = '暂无可清理的' + label.value
  } catch (e) {
    if (!controller.signal.aborted) { failed.value = true; message.value = '检查失败：' + e.message }
  } finally { busy.value = false }
}
async function clean() {
  if (!candidate.value || busy.value || props.disabled) return
  busy.value = true
  failed.value = false
  message.value = ''
  let completed = false
  try {
    const result = await api(endpoint.value, { method: 'POST', body: JSON.stringify({ dry_run: false }), signal: controller.signal })
    candidate.value = null
    message.value = `已清理 ${result.removed} 个目录，释放 ${fmtBytes(result.freed)}`
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
.cache-cleanup { margin-top: 12px; font-size: var(--jz-font-s); }
.cache-cleanup p { margin: 8px 0 0; line-height: 1.6; overflow-wrap: anywhere; }
.cleanup-confirmation { margin-top: 12px; padding: 12px; border: 1px solid var(--jz-warn-border); border-radius: var(--jz-radius-s); }
.cleanup-confirmation p { margin: 0; }
.cleanup-confirmation .bar { padding: 12px 0 0; flex-wrap: wrap; }
</style>
