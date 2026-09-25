<template>
  <div class="preview-maintenance">
    <div class="bar">
      <button @click="start" :disabled="busy">{{ busy ? '生成进度预览中…' : '生成本库进度预览' }}</button>
      <button v-if="busy && job?.job_id" @click="cancel">取消</button>
      <span>{{ message }}</span>
    </div>
    <p class="hint">为电影、剧集和花絮生成进度条缩略图，已有成品自动跳过。只读片源，图片缓存在服务器；远程库建议空闲时执行。</p>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api.js'
const props = defineProps({ libraryId: { type: Number, required: true }, active: Boolean })
const job = ref(null)
const error = ref('')
const starting = ref(false)
const busy = computed(() => starting.value || job.value?.state === 'running')
const message = computed(() => {
  if (error.value) return error.value
  const j = job.value
  if (!j || j.state === 'idle') return ''
  const progress = `${j.done || 0}/${j.total || 0} 项` + (j.failure_count ? `，失败 ${j.failure_count}` : '')
  if (j.state === 'running') return (j.waiting ? '等待在线转码结束 · ' : '') + progress
    + (j.frame_total ? ` · 当前 ${j.frames || 0}/${j.frame_total} 张` : '')
  return ({ done: '完成', cancelled: '已取消', failed: '失败' }[j.state] || j.state) + ' · ' + progress + (j.error ? ` · ${j.error}` : '')
})
let timer = null
const ctrl = new AbortController()
let disposed = false
async function load(url = `/api/stream/previews/jobs/latest?library_id=${props.libraryId}`) {
  clearTimeout(timer)
  try {
    const result = await api(url, { signal: ctrl.signal })
    if (disposed) return
    job.value = result
    error.value = ''
  } catch (e) { if (!disposed) error.value = e.message }
  if (!disposed && busy.value) timer = setTimeout(() => load(), 2500)
}
async function start() {
  starting.value = true
  error.value = ''
  try {
    const result = await api('/api/stream/previews/jobs', {
      method: 'POST', body: JSON.stringify({ library_id: props.libraryId }), signal: ctrl.signal,
    })
    if (disposed) return
    job.value = result
    await load()
  } catch (e) { if (!disposed) error.value = e.message }
  finally { starting.value = false }
}
async function cancel() {
  try {
    await api(`/api/stream/previews/jobs/${job.value.job_id}/cancel`, { method: 'POST', signal: ctrl.signal })
    if (!disposed) await load()
  } catch (e) { if (!disposed) error.value = e.message }
}
watch(() => props.active, active => { if (active) load() }, { immediate: true })
onBeforeUnmount(() => { disposed = true; ctrl.abort(); clearTimeout(timer) })
</script>

<style scoped>
.preview-maintenance { margin-top: 16px; }
.hint { color: #888; font-size: 0.8125rem; }
.bar { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
</style>
