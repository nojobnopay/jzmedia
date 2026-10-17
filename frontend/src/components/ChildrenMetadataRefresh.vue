<template>
  <section class="children-refresh">
    <h3>儿童筛选资料</h3>
    <p class="hint">补全已有 TMDB 匹配、但儿童分级资料不足的电影和剧集。本次只更新所选范围的资料，不扫描或修改媒体文件。未匹配的家庭录像可在详情中手工确认适合儿童。</p>
    <div class="settings-form">
      <JzField id="children-refresh-library" label="补全范围" :error="selectionMissing ? '所选视频库已不存在，请重新选择补全范围。' : ''">
        <template #default="{ id, describedby, invalid }">
          <select :id="id" v-model="selectedLibraryId" :disabled="scopeLocked || !librariesReady || !!librariesError || !libs.length" :aria-describedby="describedby" :aria-invalid="invalid">
            <option value="">全部媒体库</option>
            <option v-if="selectionMissing" :value="selectedLibraryId" disabled>视频库 {{ selectedLibraryId }}（已不存在）</option>
            <option v-for="library in libs" :key="library.id" :value="String(library.id)">{{ libraryLabel(library.id) }}</option>
          </select>
        </template>
      </JzField>
    </div>
    <p v-if="librariesError" class="feedback" role="alert">视频库加载失败：{{ librariesError }} <JzButton icon="refresh" @click="emit('retry-libraries')">重新加载视频库</JzButton></p>
    <p v-else-if="!librariesReady" class="hint" role="status">正在加载视频库…</p>
    <p v-else-if="!libs.length" class="hint">还没有视频库，请先在“媒体库连接”中添加视频库。</p>
    <div class="bar">
      <JzButton v-if="!running" icon="refresh" :loading="starting" :disabled="!canStart" @click="start">{{ retrySameScope ? '重试补全儿童筛选资料' : '补全儿童筛选资料' }}</JzButton>
      <JzButton v-else :loading="cancelling" @click="cancel">取消补全</JzButton>
      <JzButton v-if="jobId" variant="ghost" :disabled="loading || starting || cancelling" @click="reload">刷新状态</JzButton>
    </div>
    <p v-if="starting || loading && !task" class="hint" role="status">{{ starting ? '正在提交补全任务…' : '正在读取任务状态…' }}</p>
    <p v-if="task" class="hint">任务范围：{{ libraryLabel(task.library_id) }}</p>
    <p v-if="task" class="hint" role="status">{{ stateLabel }} · {{ task.done || 0 }}/{{ task.total || 0 }}<template v-if="task.current"> · {{ task.current }}</template><template v-if="failureCount"> · {{ failureCount }} 项失败</template></p>
    <p v-if="error || task?.error" class="feedback" role="alert">{{ error || task.error }}</p>
  </section>
</template>
<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { api } from '../api.js'
import JzButton from './JzButton.vue'
import JzField from './JzField.vue'
const props = defineProps({
  active: Boolean,
  libs: { type: Array, default: () => [] },
  librariesReady: { type: Boolean, default: false },
  librariesError: { type: String, default: '' },
})
const emit = defineEmits(['retry-libraries'])
const KEY = 'jzmedia.childrenRefreshJob'
const jobId = ref('')
try { jobId.value = sessionStorage.getItem(KEY) || '' } catch { /* 存储不可用时仅当前页面保持状态 */ }
// This operation owns its scope; browsing and scanning selections do not seed it.
const selectedLibraryId = ref('')
const libraryId = computed(() => selectedLibraryId.value === '' ? null : Number(selectedLibraryId.value))
const selectionMissing = computed(() => props.librariesReady && !props.librariesError && selectedLibraryId.value !== '' && !props.libs.some(library => Number(library.id) === libraryId.value))
const task = ref(null)
const statusKnown = ref(!jobId.value)
const starting = ref(false)
const cancelling = ref(false)
const loading = ref(false)
const error = ref('')
const running = computed(() => ['queued', 'pending', 'running'].includes(task.value?.state))
const workerActive = computed(() => running.value || task.value?.worker_finished === false)
const scopeLocked = computed(() => starting.value || cancelling.value || loading.value || workerActive.value || !statusKnown.value)
const canStart = computed(() => props.active && !scopeLocked.value && props.librariesReady && !props.librariesError && props.libs.length > 0 && !selectionMissing.value)
const failureCount = computed(() => Number(task.value?.failed_count) || (Array.isArray(task.value?.failed) ? task.value.failed.length : Number(task.value?.failed) || 0))
const retrySameScope = computed(() => task.value && (task.value.library_id ?? null) === libraryId.value && (task.value.state === 'failed' || failureCount.value))
const stateLabel = computed(() => task.value?.state === 'cancelled' && workerActive.value ? '正在取消' : ({ queued: '等待补全', pending: '等待补全', running: '正在补全', done: '补全完成', cancelled: '已取消', failed: '补全失败' })[task.value?.state] || '任务已提交')
function libraryLabel(id) {
  if (id == null) return '全部媒体库'
  const library = props.libs.find(item => Number(item.id) === Number(id))
  return library ? `${library.media_name ? library.media_name + ' / ' : ''}${library.name} · ${library.kind === 'tv' ? '剧集' : '电影'}`
    : `视频库 ${id}${props.librariesReady && !props.librariesError ? '（已不存在）' : ''}`
}
let timer = null, generation = 0, controller = null, disposed = false
function stop() { generation++; controller?.abort(); clearTimeout(timer); loading.value = false }
function schedule() { clearTimeout(timer); if (!disposed && props.active && workerActive.value) timer = setTimeout(reload, 1500) }
async function reload() {
  if (disposed || !props.active || !jobId.value || loading.value) return
  const seq = ++generation
  controller = new AbortController()
  loading.value = true
  statusKnown.value = false
  error.value = ''
  try {
    const result = await api('/api/viewing/refresh/' + encodeURIComponent(jobId.value), { signal: controller.signal })
    if (seq !== generation) return
    if (!task.value) selectedLibraryId.value = result.library_id == null ? '' : String(result.library_id)
    task.value = result
    statusKnown.value = true
    schedule()
  } catch (e) {
    if (seq !== generation) return
    error.value = '读取任务失败：' + e.message
    if (e.status === 404) { jobId.value = ''; task.value = null; statusKnown.value = true; try { sessionStorage.removeItem(KEY) } catch { /* 忽略 */ } }
  } finally { if (seq === generation) loading.value = false }
}
async function start() {
  if (!canStart.value) return
  const scope = libraryId.value
  let storedJobAtStart = ''
  try { storedJobAtStart = sessionStorage.getItem(KEY) || '' } catch { /* 存储不可用 */ }
  stop()
  starting.value = true; error.value = ''
  try {
    const result = await api('/api/viewing/refresh', { method: 'POST', body: JSON.stringify({ library_id: scope }) })
    // A delayed response from an unmounted page must not replace a newer task.
    try { if ((sessionStorage.getItem(KEY) || '') === storedJobAtStart) sessionStorage.setItem(KEY, result.job_id) } catch { /* 忽略 */ }
    if (disposed) return
    stop()
    jobId.value = result.job_id
    task.value = { state: 'queued', library_id: scope, ...result }
    selectedLibraryId.value = task.value.library_id == null ? '' : String(task.value.library_id)
    statusKnown.value = true
    await reload()
  } catch (e) { error.value = '提交补全失败：' + e.message }
  finally { starting.value = false }
}
async function cancel() {
  if (cancelling.value || !running.value) return
  stop()
  cancelling.value = true; error.value = ''
  try {
    task.value = await api('/api/viewing/refresh/' + encodeURIComponent(jobId.value) + '/cancel', { method: 'POST', body: '{}' })
    statusKnown.value = true
    await reload()
  } catch (e) { error.value = '取消失败：' + e.message }
  finally { cancelling.value = false; schedule() }
}
watch(() => props.active, active => { stop(); if (active && !starting.value && !cancelling.value) reload() }, { immediate: true })
onUnmounted(() => { disposed = true; stop() })
</script>
<style scoped>
.children-refresh { border-bottom: 1px solid var(--jz-border); padding-bottom: 24px; margin-bottom: 24px; }
</style>
