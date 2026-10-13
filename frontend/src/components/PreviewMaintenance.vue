<template>
  <section class="preview-maintenance" aria-label="播放进度缩略图">
    <h4>播放进度缩略图</h4>
    <p class="hint">为电影、剧集和花絮生成进度预览，已有成品自动跳过。远程库建议空闲时执行。</p>
    <p v-if="auto && auto.mode !== 'off'" class="hint">新入库的{{ auto.mode === 'local' ? '本地' : '所有' }}视频会在服务器{{ autoWindow }}、无人播放时自动生成；全服务队列共 {{ auto.queued }} 项。</p>
    <p v-else-if="auto?.mode === 'off'" class="hint">服务器已关闭自动生成；仍可在此批量生成。</p>
    <p v-if="auto?.queued && auto.pause_reason === 'data_limit'" class="hint" role="status">应用数据已达到 20 GB 提醒线，自动生成暂时暂停。请到“系统维护”检查空间。</p>
    <p v-else-if="auto?.queued && auto.pause_reason === 'preview_limit'" class="hint" role="status">进度缩略图缓存接近上限，自动生成暂时暂停。可在“系统维护”清理旧缩略图。</p>
    <p v-else-if="auto?.queued && auto.pause_reason === 'usage_unavailable'" class="hint" role="status">暂时无法统计应用数据占用，自动生成已暂停。可到“系统维护”重试统计。</p>
    <div class="bar">
      <JzButton class="tool-action" @click="start" :disabled="busy || !!mutating" type="button" icon="image">{{ busy ? '生成缩略图中…' : '立即批量生成' }}</JzButton>
      <JzButton v-if="canQueueExisting" @click="queueExisting" :disabled="!!mutating" type="button">{{ mutating === 'add' ? '正在加入…' : '将已有影片加入闲时队列' }}</JzButton>
      <span class="hint" role="status">{{ message }}</span>
    </div>
    <p v-if="feedback" class="hint" role="status">{{ feedback }}</p>

    <div class="queue-heading"><h5>正在处理</h5><span class="hint">当前视频库</span></div>
    <p v-if="loading && !queue" class="hint" role="status">正在加载预览任务…</p>
    <p v-else-if="queueError" class="hint" role="alert">{{ queueError }} <JzButton type="button" @click="load">重试</JzButton></p>
    <div v-else-if="queue?.running" class="queue-row">
      <div class="queue-detail">
        <strong>{{ queue.running.title }}</strong>
        <span class="hint">{{ kindLabel(queue.running.kind) }}{{ queue.running.auto ? ' · 闲时队列' : ' · 立即生成' }}{{ queue.running.waiting ? ' · 等待在线转码' : '' }}{{ queue.running.frame_total ? ` · ${queue.running.frames}/${queue.running.frame_total} 张` : '' }}</span>
      </div>
      <JzButton type="button" :disabled="!!mutating" @click="removeRunning">{{ queue.running.auto ? '取消并移出队列' : '取消任务' }}</JzButton>
    </div>
    <p v-else-if="queue" class="hint">当前视频库没有正在处理的预览影片。</p>

    <div class="queue-heading"><h5>待处理队列 <span v-if="queue">（本库 {{ queue.total }} 项）</span></h5></div>
    <template v-if="queue && !queueError">
      <p v-if="!queue.total" class="hint">当前视频库没有待处理影片。</p>
      <template v-else>
        <div class="selection-bar">
          <label class="select-all"><input type="checkbox" aria-label="全选本库待处理项" :checked="allSelected"
            :indeterminate="selectionCount > 0 && !allSelected" :disabled="!!mutating" @change="toggleAll" />全选本库待处理项</label>
          <span class="hint" role="status">已选 {{ selectionCount }} 项</span>
          <JzButton type="button" variant="danger" :disabled="!selectionCount || !!mutating" @click="removeSelected">{{ mutating === 'bulk' ? '正在移出…' : '批量移出选中项' }}</JzButton>
          <JzButton v-if="selectionCount" type="button" :disabled="!!mutating" @click="clearSelection">取消选择</JzButton>
        </div>
        <ul class="queue-list">
          <li v-for="item in queue.items" :key="`${item.kind}-${item.id}`" class="queue-row" :class="{ 'is-selected': isSelected(item) }">
            <div class="queue-main">
              <label class="select-item"><input type="checkbox" :aria-label="`选择 ${item.title}`"
                :checked="isSelected(item)" :disabled="!!mutating" @change="toggleItem(item)" /></label>
              <div class="queue-detail">
                <strong>{{ item.title }}</strong>
                <span class="hint">{{ kindLabel(item.kind) }}<template v-if="item.file_name"> · {{ item.file_name }}</template></span>
              </div>
            </div>
            <JzButton type="button" :disabled="!!mutating" :aria-label="`从待处理队列移除 ${item.title}`" @click="removeItem(item)">移出队列</JzButton>
          </li>
        </ul>
      </template>
      <div v-if="queue.total > pageSize" class="bar page-bar">
        <JzButton type="button" :disabled="offset === 0" @click="changePage(-1)">上一页</JzButton>
        <span class="hint">{{ offset + 1 }}–{{ Math.min(offset + pageSize, queue.total) }} / {{ queue.total }}</span>
        <JzButton type="button" :disabled="offset + pageSize >= queue.total" @click="changePage(1)">下一页</JzButton>
      </div>
    </template>
  </section>
</template>

<script setup>
import JzButton from './JzButton.vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api.js'

const props = defineProps({ libraryId: { type: Number, required: true }, librarySource: String, libraryEnabled: Boolean, active: Boolean })
const pageSize = 50
const job = ref(null)
const auto = ref(null)
const queue = ref(null)
const offset = ref(0)
const loading = ref(false)
const queueError = ref('')
const error = ref('')
const feedback = ref('')
const starting = ref(false)
const mutating = ref('')
const allMode = ref(false)
const selected = ref(new Set())
const excluded = ref(new Set())
const busy = computed(() => starting.value || job.value?.state === 'running')
const selectionCount = computed(() => allMode.value
  ? Math.max(0, (queue.value?.total || 0) - excluded.value.size) : selected.value.size)
const allSelected = computed(() => !!queue.value?.total && selectionCount.value === queue.value.total)
const canQueueExisting = computed(() => props.librarySource === 'local' && props.libraryEnabled && auto.value && auto.value.mode !== 'off')
const autoWindow = computed(() => auto.value?.start_hour === auto.value?.end_hour ? '全天'
  : ` ${String(auto.value?.start_hour).padStart(2, '0')}:00–${String(auto.value?.end_hour).padStart(2, '0')}:00`)
const message = computed(() => {
  if (error.value) return error.value
  const j = job.value
  if (!j || j.state === 'idle') return ''
  const progress = `${j.done || 0}/${j.total || 0} 项` + (j.failure_count ? `，失败 ${j.failure_count}` : '')
  if (j.state === 'running') return (j.waiting ? '等待在线转码结束 · ' : '') + progress
    + (j.frame_total ? ` · 当前 ${j.frames || 0}/${j.frame_total} 张` : '')
  return ({ done: '完成', cancelled: '已取消', failed: '失败' }[j.state] || j.state) + ' · ' + progress + (j.error ? ` · ${j.error}` : '')
})
const kindLabel = kind => ({ movie: '电影', episode: '剧集', extra: '花絮' }[kind] || '视频')
const itemKey = item => `${item.kind}:${item.id}`
const itemRef = key => { const [kind, id] = key.split(':'); return { kind, id: Number(id) } }
const isSelected = item => allMode.value ? !excluded.value.has(itemKey(item)) : selected.value.has(itemKey(item))
function clearSelection() { allMode.value = false; selected.value = new Set(); excluded.value = new Set() }
function toggleAll() {
  if (allSelected.value) clearSelection()
  else { allMode.value = true; selected.value = new Set(); excluded.value = new Set() }
}
function toggleItem(item) {
  const key = itemKey(item)
  if (allMode.value) {
    const next = new Set(excluded.value)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    excluded.value = next
    if (!selectionCount.value) clearSelection()
  } else {
    const next = new Set(selected.value)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    selected.value = next
  }
}
let timer = null
let requestSeq = 0
let ctrl = new AbortController()
let disposed = false

async function load() {
  if (!props.active || disposed) return
  clearTimeout(timer)
  const seq = ++requestSeq
  const libraryId = props.libraryId
  loading.value = true
  try {
    const [result, autoStatus, queueResult] = await Promise.all([
      api(`/api/stream/previews/jobs/latest?library_id=${libraryId}`, { signal: ctrl.signal }),
      api('/api/stream/previews/auto', { signal: ctrl.signal }),
      api(`/api/stream/previews/queue?library_id=${libraryId}&offset=${offset.value}&limit=${pageSize}`, { signal: ctrl.signal }),
    ])
    if (disposed || seq !== requestSeq) return
    job.value = result
    auto.value = autoStatus
    if (queue.value && queueResult.revision !== queue.value.revision && selectionCount.value) {
      clearSelection()
      feedback.value = '待处理队列已变化，请重新选择。'
    }
    queue.value = queueResult
    queueError.value = ''
    error.value = ''
    if (offset.value > 0 && queueResult.total <= offset.value) {
      offset.value = Math.max(0, Math.floor((queueResult.total - 1) / pageSize) * pageSize)
      return load()
    }
  } catch (e) {
    if (!disposed && seq === requestSeq && e.name !== 'AbortError') queueError.value = e.message
  } finally {
    if (!disposed && seq === requestSeq) {
      loading.value = false
      if (props.active) timer = setTimeout(load, busy.value || queue.value?.running ? 2500 : 30000)
    }
  }
}
async function start() {
  const libraryId = props.libraryId
  starting.value = true
  error.value = ''
  try {
    const result = await api('/api/stream/previews/jobs', {
      method: 'POST', body: JSON.stringify({ library_id: libraryId }), signal: ctrl.signal,
    })
    if (!disposed && libraryId === props.libraryId) { job.value = result; await load() }
  } catch (e) { if (!disposed && libraryId === props.libraryId && e.name !== 'AbortError') error.value = e.message }
  finally { if (libraryId === props.libraryId) starting.value = false }
}
async function queueExisting() {
  const libraryId = props.libraryId
  mutating.value = 'add'
  feedback.value = ''
  try {
    const result = await api('/api/stream/previews/queue', {
      method: 'POST', body: JSON.stringify({ library_id: libraryId }), signal: ctrl.signal,
    })
    if (disposed || libraryId !== props.libraryId) return
    clearSelection()
    feedback.value = `已加入 ${result.added} 项；已有预览 ${result.ready} 项，已在队列 ${result.already_queued} 项，源文件不可用 ${result.unavailable} 项。`
    offset.value = 0
    await load()
  } catch (e) { if (!disposed && libraryId === props.libraryId && e.name !== 'AbortError') queueError.value = e.message }
  finally { if (libraryId === props.libraryId) mutating.value = '' }
}
async function removeItem(item) {
  const libraryId = props.libraryId
  mutating.value = `${item.kind}-${item.id}`
  feedback.value = ''
  try {
    await api(`/api/stream/previews/queue/${item.kind}/${item.id}?library_id=${libraryId}`, { method: 'DELETE', signal: ctrl.signal })
    if (disposed || libraryId !== props.libraryId) return
    clearSelection()
    feedback.value = `已将“${item.title}”移出队列。`
    await load()
  } catch (e) { if (!disposed && libraryId === props.libraryId && e.name !== 'AbortError') queueError.value = e.message }
  finally { if (libraryId === props.libraryId) mutating.value = '' }
}
async function removeSelected() {
  if (!selectionCount.value || !queue.value) return
  const libraryId = props.libraryId
  const body = { library_id: libraryId, revision: queue.value.revision, all_pending: allMode.value,
    items: [...selected.value].map(itemRef), exclude: [...excluded.value].map(itemRef) }
  mutating.value = 'bulk'
  feedback.value = ''
  try {
    const result = await api('/api/stream/previews/queue/remove', {
      method: 'POST', body: JSON.stringify(body), signal: ctrl.signal,
    })
    if (disposed || libraryId !== props.libraryId) return
    clearSelection()
    feedback.value = `已移出 ${result.removed} 项待处理影片。`
    await load()
  } catch (e) {
    if (disposed || libraryId !== props.libraryId) return
    if (e.message?.startsWith('409 ')) {
      clearSelection()
      await load()
      feedback.value = '待处理队列已变化，请重新选择。'
    } else queueError.value = e.message
  } finally { if (libraryId === props.libraryId) mutating.value = '' }
}
async function removeRunning() {
  const running = queue.value?.running
  if (!running) return
  const libraryId = props.libraryId
  mutating.value = 'running'
  feedback.value = ''
  try {
    if (running.auto) {
      await api(`/api/stream/previews/queue/${running.kind}/${running.id}?library_id=${libraryId}`, { method: 'DELETE', signal: ctrl.signal })
      if (disposed || libraryId !== props.libraryId) return
      feedback.value = `已取消并移出“${running.title}”。`
    } else {
      await api(`/api/stream/previews/jobs/${running.job_id}/cancel`, { method: 'POST', signal: ctrl.signal })
      if (disposed || libraryId !== props.libraryId) return
      feedback.value = '已取消立即生成任务。'
    }
    await load()
  } catch (e) { if (!disposed && libraryId === props.libraryId && e.name !== 'AbortError') queueError.value = e.message }
  finally { if (libraryId === props.libraryId) mutating.value = '' }
}
function changePage(delta) { offset.value = Math.max(0, offset.value + delta * pageSize); load() }
watch(() => [props.active, props.libraryId], ([active]) => {
  clearTimeout(timer)
  ctrl.abort()
  ctrl = new AbortController()
  requestSeq++
  queue.value = null
  job.value = null
  starting.value = false
  mutating.value = ''
  offset.value = 0
  clearSelection()
  feedback.value = ''
  queueError.value = ''
  if (active) load()
}, { immediate: true })
onBeforeUnmount(() => { disposed = true; ctrl.abort(); clearTimeout(timer) })
</script>

<style scoped>
.preview-maintenance { margin-top: 24px; padding-top: 20px; border-top: 1px solid var(--jz-border); }
h4 { margin: 0 0 8px; font-size: var(--jz-font-xl); }
h5 { margin: 0; font-size: var(--jz-font-xl); font-weight: 700; line-height: 1.4; }
.hint { color: var(--jz-text-dim); font-size: 0.8125rem; }
.bar { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.queue-heading { display: flex; align-items: baseline; flex-wrap: wrap; gap: 8px; margin-top: 28px; margin-bottom: 12px; padding-left: 12px; border-left: 3px solid var(--jz-accent); }
.selection-bar { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; margin: 0 0 8px; padding: 8px 12px; border: 1px solid var(--jz-border); border-radius: var(--jz-radius-s); background: var(--jz-surface-2); }
.select-all, .select-item { display: inline-flex; align-items: center; cursor: pointer; }
.select-all { gap: 8px; min-height: 44px; font-size: var(--jz-font-m); }
.select-item { justify-content: center; width: 44px; min-width: 44px; height: 44px; }
.select-all input, .select-item input { width: 18px; height: 18px; margin: 0; accent-color: var(--jz-accent); cursor: pointer; }
.select-all input:focus-visible, .select-item input:focus-visible { outline: 2px solid var(--jz-link); outline-offset: 3px; }
.queue-list { list-style: none; padding: 0; margin: 0; }
.queue-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 0; border-top: 1px solid var(--jz-border); }
.queue-row.is-selected { background: var(--jz-selected); }
.queue-main { display: flex; align-items: center; flex: 1; min-width: 0; gap: 4px; }
.queue-detail { display: grid; gap: 3px; min-width: 0; overflow-wrap: anywhere; }
.queue-detail strong { font-size: var(--jz-font-m); font-weight: 500; }
.page-bar { margin-top: 12px; }
@media (max-width: 600px) { .queue-row { align-items: flex-start; flex-direction: column; } .queue-row :deep(button) { min-height: 44px; } }
</style>
