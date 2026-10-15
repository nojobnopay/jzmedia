<template>
  <section class="children-refresh">
    <h3>儿童筛选资料</h3>
    <p class="hint">作用于全部媒体库。补全已有 TMDB 匹配、但儿童分级资料不足的电影和剧集；只更新资料，不扫描或修改媒体文件。未匹配的家庭录像可在详情中手工确认适合儿童。</p>
    <div class="bar">
      <JzButton v-if="!running" icon="refresh" :loading="starting" :disabled="loading" @click="start">{{ task?.state === 'failed' || failureCount ? '重试补全儿童筛选资料' : '补全儿童筛选资料' }}</JzButton>
      <JzButton v-else :loading="cancelling" @click="cancel">取消补全</JzButton>
      <JzButton v-if="jobId" variant="ghost" :disabled="loading || starting" @click="reload">刷新状态</JzButton>
    </div>
    <p v-if="starting || loading && !task" class="hint" role="status">{{ starting ? '正在提交补全任务…' : '正在读取任务状态…' }}</p>
    <p v-if="task" class="hint" role="status">{{ stateLabel }} · {{ task.done || 0 }}/{{ task.total || 0 }}<template v-if="task.current"> · {{ task.current }}</template><template v-if="failureCount"> · {{ failureCount }} 项失败</template></p>
    <p v-if="error || task?.error" class="feedback" role="alert">{{ error || task.error }}</p>
  </section>
</template>
<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { api } from '../api.js'
import JzButton from './JzButton.vue'
const props = defineProps({ active: Boolean })
const KEY = 'jzmedia.childrenRefreshJob'
const jobId = ref('')
try { jobId.value = sessionStorage.getItem(KEY) || '' } catch { /* 存储不可用时仅当前页面保持状态 */ }
const task = ref(null)
const starting = ref(false)
const cancelling = ref(false)
const loading = ref(false)
const error = ref('')
const running = computed(() => ['queued', 'pending', 'running'].includes(task.value?.state))
const failureCount = computed(() => Number(task.value?.failed_count) || (Array.isArray(task.value?.failed) ? task.value.failed.length : Number(task.value?.failed) || 0))
const stateLabel = computed(() => ({ queued: '等待补全', pending: '等待补全', running: '正在补全', done: '补全完成', cancelled: '已取消', failed: '补全失败' })[task.value?.state] || '任务已提交')
let timer = null, generation = 0, controller = null
function stop() { generation++; controller?.abort(); clearTimeout(timer); loading.value = false }
function schedule() { clearTimeout(timer); if (props.active && running.value) timer = setTimeout(reload, 1500) }
async function reload() {
  if (!props.active || !jobId.value || loading.value) return
  const seq = ++generation
  controller = new AbortController()
  loading.value = true
  error.value = ''
  try {
    const result = await api('/api/viewing/refresh/' + encodeURIComponent(jobId.value), { signal: controller.signal })
    if (seq !== generation) return
    task.value = result
    schedule()
  } catch (e) {
    if (seq !== generation) return
    error.value = '读取任务失败：' + e.message
    if (e.status === 404) { jobId.value = ''; task.value = null; try { sessionStorage.removeItem(KEY) } catch { /* 忽略 */ } }
  } finally { if (seq === generation) loading.value = false }
}
async function start() {
  if (starting.value || running.value) return
  starting.value = true; error.value = ''
  try {
    const result = await api('/api/viewing/refresh', { method: 'POST', body: '{}' })
    jobId.value = result.job_id
    task.value = { state: 'queued', ...result }
    try { sessionStorage.setItem(KEY, jobId.value) } catch { /* 忽略 */ }
    await reload()
  } catch (e) { error.value = '提交补全失败：' + e.message }
  finally { starting.value = false }
}
async function cancel() {
  if (cancelling.value) return
  cancelling.value = true; error.value = ''
  try {
    await api('/api/viewing/refresh/' + encodeURIComponent(jobId.value) + '/cancel', { method: 'POST', body: '{}' })
    await reload()
  } catch (e) { error.value = '取消失败：' + e.message }
  finally { cancelling.value = false }
}
watch(() => props.active, active => { stop(); if (active) reload() }, { immediate: true })
onUnmounted(stop)
</script>
<style scoped>
.children-refresh { border-bottom: 1px solid var(--jz-border); padding-bottom: 24px; margin-bottom: 24px; }
</style>
