<template>
  <section class="preview-maintenance" aria-label="播放进度缩略图">
    <h4>播放进度缩略图</h4>
    <p class="hint">为电影、剧集和花絮生成进度预览，已有成品自动跳过。远程库建议空闲时执行。</p>
    <p v-if="auto && auto.mode !== 'off'" class="hint">新入库的{{ auto.mode === 'local' ? '本地' : '所有' }}视频会在服务器{{ autoWindow }}、无人播放时自动生成；全服务队列共 {{ auto.queued }} 项。</p>
    <p v-else-if="auto?.mode === 'off'" class="hint">服务器已关闭自动生成；仍可在此批量生成。</p>
    <p v-if="auto?.queued && auto.pause_reason === 'data_limit'" class="hint" role="status">应用数据已达到设置的总预算，闲时生成暂时暂停。可到“系统维护”重新分配容量或清理缓存。</p>
    <p v-else-if="auto?.queued && auto.pause_reason === 'preview_limit'" class="hint" role="status">进度缩略图缓存接近上限，闲时生成暂时暂停。可在“系统维护”调整容量或清理旧缩略图。</p>
    <p v-else-if="auto?.queued && auto.pause_reason === 'usage_unavailable'" class="hint" role="status">暂时无法统计应用数据占用，自动生成已暂停。可到“系统维护”重试统计。</p>
    <div class="bar">
      <JzButton class="tool-action" @click="start" :disabled="busy || !!mutating" type="button" icon="image">{{ busy ? '生成缩略图中…' : '立即批量生成' }}</JzButton>
      <JzButton v-if="canQueueExisting" @click="queueExisting" :disabled="starting || !!mutating" type="button">{{ mutating === 'add' ? '正在加入…' : '将已有影片加入闲时队列' }}</JzButton>
      <span class="hint" role="status">{{ message }}</span>
    </div>
    <p v-if="feedback" class="hint" role="status">{{ feedback }}</p>

    <div class="completed-heading">
      <JzButton ref="completedToggleRef" type="button" variant="ghost" class="completed-toggle" icon="image"
        aria-label="已生成缩略图" :aria-expanded="completedOpen" :aria-controls="completedPanelId" @click="toggleCompleted">
        <span>已生成缩略图<span v-if="completed"> · {{ completed.total }} 项</span></span>
        <AppIcon :name="completedOpen ? 'chevron-up' : 'chevron-down'" :size="18" />
      </JzButton>
    </div>
    <div v-if="completedOpen" :id="completedPanelId" class="completed-panel" role="region" aria-label="已生成缩略图清单">
      <div class="completed-summary">
        <p class="hint">当前视频库<template v-if="completed"> · 共 {{ completed.total }} 项</template></p>
        <JzButton ref="completedRefreshRef" type="button" variant="ghost" size="compact" icon="refresh" :loading="completedLoading"
          aria-label="刷新已生成缩略图" @click="loadCompleted">刷新</JzButton>
      </div>
      <p v-if="completedLoading && !completed" class="hint" role="status">正在加载已生成缩略图…</p>
      <p v-else-if="completedError" class="hint error" role="alert">{{ completedError }}
        <JzButton ref="completedRetryRef" type="button" aria-label="重试加载已生成缩略图" @click="loadCompleted">重试</JzButton>
      </p>
      <template v-else-if="completed">
        <p v-if="!completed.total" class="hint">当前视频库暂无可用的完整缩略图。</p>
        <template v-else>
          <p class="hint completed-note">已完整生成并可用于播放进度预览。</p>
          <ul class="completed-list">
            <li v-for="item in completed.items" :key="`${item.kind}-${item.id}`" class="completed-row">
              <div class="queue-detail">
                <div class="queue-title-line"><strong :title="item.title">{{ item.title }}</strong>
                  <span v-if="item.needs_update" class="queue-state legacy-state">旧版可用</span>
                </div>
                <span class="hint queue-file" :title="item.file_name">{{ kindLabel(item.kind) }}<template v-if="item.file_name"> · {{ item.file_name }}</template></span>
                <span class="hint">共 {{ item.frames }} 张<template v-if="item.interval"> · {{ item.interval }} 秒/张</template></span>
              </div>
            </li>
          </ul>
          <nav v-if="completed.total > pageSize" class="bar page-bar" aria-label="已生成分页">
            <JzButton ref="completedPreviousRef" type="button" aria-label="已生成上一页" :disabled="completedOffset === 0 || completedLoading" @click="changeCompletedPage(-1)">上一页</JzButton>
            <span class="hint">{{ completedOffset + 1 }}–{{ Math.min(completedOffset + pageSize, completed.total) }} / {{ completed.total }}</span>
            <JzButton ref="completedNextRef" type="button" aria-label="已生成下一页" :disabled="completedOffset + pageSize >= completed.total || completedLoading" @click="changeCompletedPage(1)">下一页</JzButton>
          </nav>
        </template>
      </template>
    </div>

    <div class="processing-queue" role="region" aria-label="处理队列">
      <div class="queue-heading"><h5>处理队列</h5>
        <span v-if="queue" class="hint">共 {{ processingCount }} 项 · 处理中 {{ queue.running ? 1 : 0 }} · 等待 {{ queue.total }}</span>
      </div>
      <p v-if="loading && !queue" class="hint" role="status">正在加载预览任务…</p>
      <p v-else-if="queueError" class="hint error" role="alert">{{ queueError }}
        <JzButton type="button" aria-label="重试加载处理队列" @click="load">重试</JzButton>
      </p>
      <template v-else-if="queue">
        <p v-if="!processingCount" class="hint">当前视频库没有待处理影片。</p>
        <template v-else>
          <div v-if="queue.total" class="selection-bar">
            <label class="select-all"><input type="checkbox" aria-label="全选本库待处理项" :checked="allSelected"
              :indeterminate="selectionCount > 0 && !allSelected" :disabled="starting || !!mutating" @change="toggleAll" />全选待处理</label>
            <span class="hint" role="status">已选 {{ selectionCount }} 项</span>
            <JzButton type="button" variant="danger" :disabled="!selectionCount || starting || !!mutating" @click="removeSelected">{{ mutating === 'bulk' ? '正在移出…' : '批量移出选中项' }}</JzButton>
            <JzButton v-if="selectionCount" type="button" :disabled="starting || !!mutating" @click="clearSelection">取消选择</JzButton>
          </div>
          <ul class="queue-list">
            <li v-if="queue.running" class="queue-row is-running" aria-current="true">
              <div class="queue-main">
                <span class="current-mark"><AppIcon name="image" :size="20" /></span>
                <div class="queue-detail">
                  <div class="queue-title-line"><strong :title="queue.running.title">{{ queue.running.title }}</strong>
                    <span class="queue-state current-state">{{ queue.running.waiting ? '等待转码' : '正在处理' }}</span>
                  </div>
                  <span class="hint queue-file" :title="queue.running.file_name">{{ kindLabel(queue.running.kind) }} · {{ queue.running.auto ? '闲时队列' : '立即生成' }}<template v-if="queue.running.file_name"> · {{ queue.running.file_name }}</template></span>
                  <div class="frame-progress">
                    <progress :max="runningTotal || 1" :value="runningTotal ? Math.min(runningFrames, runningTotal) : undefined"
                      :aria-label="`${queue.running.title} 缩略图抽取进度`"
                      :aria-valuetext="runningTotal ? `${runningFrames}/${runningTotal} 张` : '正在准备抽帧'" />
                    <span class="hint">{{ runningTotal ? `${runningFrames}/${runningTotal} 张` : '准备中' }}</span>
                  </div>
                  <span v-if="queue.running.waiting" class="hint current-note">等待播放转码释放资源，随后继续生成。</span>
                  <span v-else-if="runningTotal && runningFrames >= runningTotal" class="hint current-note">抽帧完成，正在整理缩略图。</span>
                </div>
              </div>
              <JzButton class="queue-action" type="button" variant="ghost" icon="close" icon-only
                :disabled="starting || !!mutating" :loading="mutating === 'running'"
                :aria-label="queue.running.auto ? `取消并移出队列 ${queue.running.title}` : `取消立即生成任务 ${queue.running.title}`"
                :title="queue.running.auto ? '取消并移出队列' : '取消立即生成任务'" @click="removeRunning" />
            </li>
            <li v-for="item in queue.items" :key="`${item.kind}-${item.id}`" class="queue-row is-pending" :class="{ 'is-selected': isSelected(item) }">
              <div class="queue-main">
                <label class="select-item"><input type="checkbox" :aria-label="`选择 ${item.title}`"
                  :checked="isSelected(item)" :disabled="starting || !!mutating" @change="toggleItem(item)" /></label>
                <div class="queue-detail">
                  <div class="queue-title-line"><strong :title="item.title">{{ item.title }}</strong><span class="queue-state pending-state">待处理</span></div>
                  <span class="hint queue-file" :title="item.file_name">{{ kindLabel(item.kind) }}<template v-if="item.file_name"> · {{ item.file_name }}</template></span>
                </div>
              </div>
              <JzButton class="queue-action" type="button" variant="ghost" icon="close" icon-only title="移出队列"
                :disabled="starting || !!mutating" :loading="mutating === `${item.kind}-${item.id}`"
                :aria-label="`从待处理队列移除 ${item.title}`" @click="removeItem(item)" />
            </li>
          </ul>
        </template>
        <nav v-if="queue.total > pageSize" class="bar page-bar" aria-label="处理队列分页">
          <JzButton type="button" :disabled="offset === 0 || starting || !!mutating" @click="changePage(-1)">上一页</JzButton>
          <span class="hint">待处理 {{ offset + 1 }}–{{ Math.min(offset + pageSize, queue.total) }} / {{ queue.total }}</span>
          <JzButton type="button" :disabled="offset + pageSize >= queue.total || starting || !!mutating" @click="changePage(1)">下一页</JzButton>
        </nav>
      </template>
    </div>
  </section>
</template>

<script setup>
import JzButton from './JzButton.vue'
import AppIcon from './AppIcon.vue'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api.js'

const props = defineProps({ libraryId: { type: Number, required: true }, librarySource: String, libraryEnabled: Boolean, active: Boolean })
const pageSize = 50
const job = ref(null)
const auto = ref(null)
const queue = ref(null)
const completed = ref(null)
const completedOpen = ref(false)
const completedOffset = ref(0)
const completedLoading = ref(false)
const completedError = ref('')
const completedPanelId = computed(() => `preview-completed-${props.libraryId}`)
const completedToggleRef = ref(null)
const completedRefreshRef = ref(null)
const completedRetryRef = ref(null)
const completedPreviousRef = ref(null)
const completedNextRef = ref(null)
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
const processingCount = computed(() => (queue.value?.total || 0) + (queue.value?.running ? 1 : 0))
const runningFrames = computed(() => Math.max(0, Number(queue.value?.running?.frames) || 0))
const runningTotal = computed(() => Math.max(0, Number(queue.value?.running?.frame_total) || 0))
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
  if (j.state === 'running') return (j.waiting ? '等待播放转码释放资源 · ' : '') + progress
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
let loadCtrl = null
let completedSeq = 0
let completedCtrl = null
let ctrl = new AbortController()
let disposed = false

function invalidateCompleted() {
  completedSeq++
  completedCtrl?.abort()
  completedCtrl = null
  completedLoading.value = false
}
function toggleCompleted() {
  completedOpen.value = !completedOpen.value
  if (completedOpen.value) loadCompleted()
  else invalidateCompleted()
}
function completedFocusOwner() {
  if (typeof document === 'undefined') return null
  const controls = [completedRefreshRef, completedRetryRef, completedPreviousRef, completedNextRef]
  const control = controls.find(item => item.value?.el === document.activeElement)
  return control ? { control, element: document.activeElement } : null
}
function restoreCompletedFocus(owner) {
  if (!owner || ![document.body, owner.element].includes(document.activeElement)) return
  // Refresh becomes disabled while busy; retry/paging controls may be replaced.
  const paging = [completedPreviousRef, completedNextRef].includes(owner.control)
  const choices = [owner.control, ...(paging ? [completedPreviousRef, completedNextRef] : []), completedRefreshRef, completedToggleRef]
  const target = choices.find(item => item.value?.el && !item.value.el.disabled && item.value.el.isConnected !== false)
  target?.value?.focus()
}
async function loadCompleted() {
  if (!props.active || !completedOpen.value || disposed) return
  const focusOwner = completedFocusOwner()
  invalidateCompleted()
  const seq = completedSeq
  const libraryId = props.libraryId
  const requestCtrl = new AbortController()
  completedCtrl = requestCtrl
  completedLoading.value = true
  completedError.value = ''
  try {
    const result = await api(`/api/stream/previews/completed?library_id=${libraryId}&offset=${completedOffset.value}&limit=${pageSize}`, { signal: requestCtrl.signal })
    if (disposed || seq !== completedSeq || requestCtrl.signal.aborted) return
    completed.value = result
    if (completedOffset.value > 0 && result.total <= completedOffset.value) {
      completedOffset.value = Math.max(0, Math.floor((result.total - 1) / pageSize) * pageSize)
      return loadCompleted()
    }
  } catch (e) {
    if (!disposed && seq === completedSeq && !requestCtrl.signal.aborted) completedError.value = e.message
  } finally {
    if (!disposed && seq === completedSeq) {
      completedCtrl = null
      completedLoading.value = false
      await nextTick()
      if (!disposed && seq === completedSeq && completedOpen.value) restoreCompletedFocus(focusOwner)
    }
  }
}
function changeCompletedPage(delta) {
  if (completedLoading.value) return
  completedOffset.value = Math.max(0, completedOffset.value + delta * pageSize)
  completed.value = null
  loadCompleted()
}

function invalidateLoad() {
  clearTimeout(timer)
  requestSeq++
  loadCtrl?.abort()
  loadCtrl = null
  loading.value = false
}
function beginAction() {
  invalidateLoad()
  error.value = ''
  feedback.value = ''
  return ctrl.signal
}
const isCurrent = signal => !disposed && !signal.aborted
async function finishAction(signal) {
  if (!isCurrent(signal)) return
  starting.value = false
  mutating.value = ''
  await load()
}
async function load() {
  if (!props.active || disposed || starting.value || mutating.value) return
  invalidateLoad()
  const seq = requestSeq
  const libraryId = props.libraryId
  const requestCtrl = new AbortController()
  loadCtrl = requestCtrl
  loading.value = true
  try {
    const [result, autoStatus, queueResult] = await Promise.all([
      api(`/api/stream/previews/jobs/latest?library_id=${libraryId}`, { signal: requestCtrl.signal }),
      api('/api/stream/previews/auto', { signal: requestCtrl.signal }),
      api(`/api/stream/previews/queue?library_id=${libraryId}&offset=${offset.value}&limit=${pageSize}`, { signal: requestCtrl.signal }),
    ])
    if (disposed || seq !== requestSeq) return
    const previousRunning = queue.value?.running
    const currentChanged = previousRunning && (!queueResult.running || itemKey(previousRunning) !== itemKey(queueResult.running))
    const jobFinished = job.value?.state === 'running' && result.state !== 'running'
    job.value = result
    auto.value = autoStatus
    if (queue.value && queueResult.revision !== queue.value.revision && selectionCount.value) {
      clearSelection()
      feedback.value = '待处理队列已变化，请重新选择。'
    }
    queue.value = queueResult
    queueError.value = ''
    // Read full completed pages only while requested and after a task transition.
    if (completedOpen.value && (currentChanged || jobFinished)) loadCompleted()
    if (offset.value > 0 && queueResult.total <= offset.value) {
      offset.value = Math.max(0, Math.floor((queueResult.total - 1) / pageSize) * pageSize)
      return load()
    }
  } catch (e) {
    if (!disposed && seq === requestSeq && e.name !== 'AbortError') queueError.value = e.message
  } finally {
    if (!disposed && seq === requestSeq) {
      loadCtrl = null
      loading.value = false
      if (props.active) timer = setTimeout(load, busy.value || queue.value?.running || queue.value?.total ? 2500 : 30000)
    }
  }
}
async function start() {
  if (busy.value || mutating.value) return
  const libraryId = props.libraryId
  const signal = beginAction()
  starting.value = true
  try {
    const result = await api('/api/stream/previews/jobs', {
      method: 'POST', body: JSON.stringify({ library_id: libraryId }), signal,
    })
    if (isCurrent(signal)) job.value = result
  } catch (e) { if (isCurrent(signal)) error.value = e.message }
  finally { await finishAction(signal) }
}
async function queueExisting() {
  if (starting.value || mutating.value) return
  const libraryId = props.libraryId
  const signal = beginAction()
  mutating.value = 'add'
  try {
    const result = await api('/api/stream/previews/queue', {
      method: 'POST', body: JSON.stringify({ library_id: libraryId }), signal,
    })
    if (!isCurrent(signal)) return
    clearSelection()
    feedback.value = `已加入 ${result.added} 项；已有预览 ${result.ready} 项，已在队列 ${result.already_queued} 项，源文件不可用 ${result.unavailable} 项。`
    offset.value = 0
  } catch (e) { if (isCurrent(signal)) error.value = e.message }
  finally { await finishAction(signal) }
}
async function removeItem(item) {
  if (starting.value || mutating.value) return
  const libraryId = props.libraryId
  const signal = beginAction()
  mutating.value = `${item.kind}-${item.id}`
  try {
    await api(`/api/stream/previews/queue/${item.kind}/${item.id}?library_id=${libraryId}`, { method: 'DELETE', signal })
    if (!isCurrent(signal)) return
    clearSelection()
    feedback.value = `已将“${item.title}”移出队列。`
  } catch (e) { if (isCurrent(signal)) error.value = e.message }
  finally { await finishAction(signal) }
}
async function removeSelected() {
  if (!selectionCount.value || !queue.value || starting.value || mutating.value) return
  const libraryId = props.libraryId
  const body = { library_id: libraryId, revision: queue.value.revision, all_pending: allMode.value,
    items: [...selected.value].map(itemRef), exclude: [...excluded.value].map(itemRef) }
  const signal = beginAction()
  mutating.value = 'bulk'
  try {
    const result = await api('/api/stream/previews/queue/remove', {
      method: 'POST', body: JSON.stringify(body), signal,
    })
    if (!isCurrent(signal)) return
    clearSelection()
    feedback.value = `已移出 ${result.removed} 项待处理影片。`
  } catch (e) {
    if (!isCurrent(signal)) return
    if (e.status === 409) {
      clearSelection()
      feedback.value = '待处理队列已变化，请重新选择。'
    } else error.value = e.message
  } finally { await finishAction(signal) }
}
async function removeRunning() {
  const running = queue.value?.running
  if (!running || starting.value || mutating.value) return
  const libraryId = props.libraryId
  const signal = beginAction()
  mutating.value = 'running'
  try {
    if (running.auto) {
      await api(`/api/stream/previews/queue/${running.kind}/${running.id}?library_id=${libraryId}`, { method: 'DELETE', signal })
      if (!isCurrent(signal)) return
      feedback.value = `已取消并移出“${running.title}”。`
    } else {
      await api(`/api/stream/previews/jobs/${running.job_id}/cancel`, { method: 'POST', signal })
      if (!isCurrent(signal)) return
      feedback.value = '已取消立即生成任务。'
    }
  } catch (e) { if (isCurrent(signal)) error.value = e.message }
  finally { await finishAction(signal) }
}
function changePage(delta) { offset.value = Math.max(0, offset.value + delta * pageSize); load() }
watch(() => [props.active, props.libraryId], ([active]) => {
  invalidateLoad()
  invalidateCompleted()
  ctrl.abort()
  ctrl = new AbortController()
  queue.value = null
  job.value = null
  auto.value = null
  completed.value = null
  completedOffset.value = 0
  completedError.value = ''
  starting.value = false
  mutating.value = ''
  offset.value = 0
  clearSelection()
  feedback.value = ''
  queueError.value = ''
  error.value = ''
  if (active) {
    load()
    if (completedOpen.value) loadCompleted()
  }
}, { immediate: true })
onBeforeUnmount(() => { disposed = true; ctrl.abort(); invalidateLoad(); invalidateCompleted() })
</script>

<style scoped>
.preview-maintenance { margin-top: 24px; padding-top: 20px; border-top: 1px solid var(--jz-border); }
h4 { margin: 0 0 8px; font-size: var(--jz-font-xl); }
h5 { margin: 0; font-size: var(--jz-font-xl); font-weight: 700; line-height: 1.4; }
.hint { color: var(--jz-text-dim); font-size: 0.8125rem; }
.error { color: var(--jz-danger); }
.bar { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.completed-heading { margin-top: var(--jz-gap-xl); }
.completed-toggle { width: 100%; justify-content: flex-start; padding-inline: var(--jz-gap-m); border: 1px solid var(--jz-border); border-radius: var(--jz-radius-s); }
.completed-toggle > span { flex: 1; min-width: 0; text-align: left; }
.completed-panel { padding: 0 var(--jz-gap-m) var(--jz-gap-m); border: 1px solid var(--jz-border); border-top: 0; border-radius: 0 0 var(--jz-radius-s) var(--jz-radius-s); }
.completed-summary { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: var(--jz-gap-s); }
.completed-summary > p { margin-block: var(--jz-gap-m); }
.completed-note { margin: 0 0 var(--jz-gap-s); }
.completed-list { list-style: none; padding: 0; margin: 0; }
.completed-row { display: flex; padding: var(--jz-gap-m) 0; border-top: 1px solid var(--jz-border); }
.queue-heading { display: flex; align-items: baseline; flex-wrap: wrap; gap: 8px; margin-top: var(--jz-gap-xl); margin-bottom: var(--jz-gap-m); padding-left: var(--jz-gap-m); border-left: 3px solid var(--jz-accent); }
.selection-bar { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; margin: 0 0 8px; padding: 8px 12px; border: 1px solid var(--jz-border); border-radius: var(--jz-radius-s); background: var(--jz-surface-2); }
.select-all, .select-item { display: inline-flex; align-items: center; cursor: pointer; }
.select-all { gap: 8px; min-height: 44px; font-size: var(--jz-font-m); white-space: nowrap; }
.select-item { justify-content: center; width: 44px; min-width: 44px; height: 44px; }
.select-all input, .select-item input { width: 18px; height: 18px; margin: 0; accent-color: var(--jz-accent); cursor: pointer; }
.select-all input:focus-visible, .select-item input:focus-visible { outline: 2px solid var(--jz-link); outline-offset: 3px; }
.queue-list { list-style: none; padding: 0; margin: 0; }
.queue-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 0; border-top: 1px solid var(--jz-border); }
.queue-row.is-selected { background: var(--jz-selected); }
.queue-row.is-running { padding: var(--jz-gap-m) var(--jz-gap-s) var(--jz-gap-m) 0; margin-bottom: var(--jz-gap-s); border: 1px solid var(--jz-border-strong); border-left: 3px solid var(--jz-accent); border-radius: var(--jz-radius-s); background: var(--jz-accent-soft); }
.queue-main { display: flex; align-items: center; flex: 1; min-width: 0; gap: 4px; }
.current-mark { display: inline-flex; align-items: center; justify-content: center; flex: none; width: 44px; color: var(--jz-text); }
.queue-detail { display: grid; flex: 1; gap: 3px; min-width: 0; }
.queue-title-line { display: flex; align-items: center; gap: var(--jz-gap-s); min-width: 0; }
.queue-title-line > strong { flex: 1; min-width: 0; }
.queue-state { flex: none; font-size: var(--jz-font-s); line-height: 1.5; white-space: nowrap; }
.current-state { color: var(--jz-text); }
.pending-state { color: var(--jz-text-faint); }
.legacy-state { color: var(--jz-warn); }
.queue-detail strong { font-size: var(--jz-font-m); font-weight: 500; }
.queue-detail strong, .queue-file { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.queue-detail .hint { margin: 0; }
.queue-action { flex: none; align-self: center; }
.queue-row > .queue-detail .hint { overflow-wrap: anywhere; }
.frame-progress { display: flex; align-items: center; gap: var(--jz-gap-s); min-width: 0; }
.frame-progress progress { display: block; flex: 1; min-width: 0; width: 100%; height: 5px; overflow: hidden; border: 0; border-radius: var(--jz-radius-pill); background: var(--jz-border-strong); accent-color: var(--jz-accent); }
.frame-progress progress::-webkit-progress-bar { background: var(--jz-border-strong); border-radius: var(--jz-radius-pill); }
.frame-progress progress::-webkit-progress-value { background: var(--jz-accent); border-radius: var(--jz-radius-pill); }
.frame-progress progress::-moz-progress-bar { background: var(--jz-accent); border-radius: var(--jz-radius-pill); }
.frame-progress > span { flex: none; font-variant-numeric: tabular-nums; white-space: nowrap; }
.current-note { line-height: 1.5; }
.page-bar { margin-top: 12px; }
</style>
