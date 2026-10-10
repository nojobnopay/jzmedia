<template>
  <section id="sec-libtools" class="library-workspace" :class="{ 'files-workspace': filesPage }">
    <h3 v-if="!filesPage" class="library-selector-label">选择视频库</h3>

    <div v-if="!tabs.length && librariesError" class="library-load-state" role="alert">
      <strong>视频库加载失败</strong>
      <span>{{ librariesError }}</span>
      <JzButton @click="emit('retry-libraries')" type="button" icon="refresh">重新加载</JzButton>
    </div>
    <div v-else-if="!tabs.length && !librariesReady" class="library-load-state" role="status" aria-live="polite" aria-busy="true">
      <Spinner :size="28" />
      <strong>正在加载视频库…</strong>
      <span>{{ filesPage ? '加载完成后将显示文件和文件夹。' : '正在准备视频库工具。' }}</span>
    </div>
    <div v-else-if="!tabs.length" class="hint">还没有视频库。<JzButton @click="emit('add-library')" type="button" icon="plus">添加媒体库</JzButton></div>
    <template v-else>
      <div v-if="!filesPage" class="lib-tabs">
        <JzButton v-for="t in tabs" :key="t.id"
          :class="{ on: t.id === selectedId, off: !t.enabled }"
          :title="tabTitle(t)" :aria-pressed="t.id === selectedId" @click="select(t.id, { sync: true })" type="button">
          {{ t.label }}
          <span v-if="t.name !== t.kind_text" class="tab-kind">{{ t.kind_text }}</span>
          <span v-if="badgeOf(t.id)" class="nav-badge">{{ badgeOf(t.id) }}</span>
        </JzButton>
      </div>

      <p v-if="selectedTab && !selectedTab.enabled" class="hint warn-text">该视频库已停用：扫描/写入会被跳过，这里仅作查看。</p>

      <div v-if="filesPage && returnOrigin" class="file-return-bar">
        <JzButton @click="returnToTools" type="button" icon="back">返回扫描与整理</JzButton>
        <span>{{ sourceTab?.label }} · {{ sourceViewLabel }}</span>
      </div>
      <FsBrowser v-if="filesVisited" v-show="filesPage" ref="filesRef" :active="filesPage"
        :media="selectedMedia" :video-libs="libs" :initial-lib-id="selectedId" :initial-path="pendingFilesPath" :pending-changes="currentChanges"
        @changed="onFilesChanged" @scan="scanFiles" @library-change="switchFileLibrary" />
      <p v-if="changesError && filesPage" class="hint warn-text" role="status">{{ changesError }}</p>
      <div v-if="!filesPage && currentChanges?.pending" class="settings-notice">
        <span>本库有 {{ currentChanges.count }} 项文件变更待扫描核对。</span>
        <JzButton :disabled="scanBlocked || working || !selectedTab?.enabled" @click="scanFiles({ library_id: selectedId })" type="button" icon="scan">扫描并核对</JzButton>
      </div>
      <p v-if="fileScanError" class="hint warn-text" role="alert">{{ fileScanError }}</p>

      <template v-for="t in tabs" :key="'lt' + t.id">
        <div v-if="openedTabs.has(t.id)" v-show="!filesPage && t.id === selectedId">
          <MovieLibraryTools v-if="t.kind !== 'tv'" :tab="t" :active="active && !filesPage && t.id === selectedId"
            :request="requests[t.id] || null"
            @changed="onChanged" @status="s => onStatus(t.id, s)" @open-files="state => openFiles(t, state)" />
          <TvLibraryTools v-else :tab="t" :active="active && !filesPage && t.id === selectedId"
            :request="requests[t.id] || null"
            @changed="onChanged" @status="s => onStatus(t.id, s)" @open-files="state => openFiles(t, state)" />
        </div>
      </template>
    </template>
    <FileReviewDialog :open="reviewOpen" :library-name="selectedTab?.label" :change="currentChanges"
      :working="working" :scan-blocked="scanBlocked || !selectedTab?.enabled" :scanning="reviewScanning"
      :error="fileScanError || changesError" @stay="settleReview('stay')" @later="settleReview('later')" @scan="reviewScan" />
  </section>
</template>
<script setup>
import JzButton from './JzButton.vue'

import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'
import { buildMediaLibs, currentMediaId, getStoredLibId, setStoredLibId, switchMedia } from '../libraries.js'
import { buildTabs, pickTab, resolveFocusTab } from '../libraryToolsTabs.js'
import MovieLibraryTools from './MovieLibraryTools.vue'
import TvLibraryTools from './TvLibraryTools.vue'
import FsBrowser from './FsBrowser.vue'
import FileReviewDialog from './FileReviewDialog.vue'
import Spinner from './Spinner.vue'
import { useFileChanges } from '../useFileChanges.js'
import { useLibraryScan } from '../useLibraryScan.js'
import { leavesFileLibrary, needsFileReview, scanTarget } from '../fileReview.js'
import { createFileOrigin, readFileOrigin, writeFileOrigin, fileOriginMatches,
  fileWorkspaceTarget, fileReturnTarget, fileOriginTransition } from '../fileNavigation.js'

// 媒体库工具容器（视频库 Tab 版）：顶层标签 = 每个视频库；按 kind 挂
// MovieLibraryTools / TvLibraryTools，各自内部单步聚焦 + 更多工具折叠。
// 深链：?library=<视频库id> 直选；?media=<媒体库id> 按区块类型偏好选库。
const props = defineProps({
  libs: { type: Array, default: () => [] },
  active: { type: Boolean, default: true },
  currentMediaId: { type: Number, default: null },
  librariesReady: { type: Boolean, default: true },
  librariesError: { type: String, default: '' },
})
const emit = defineEmits(['changed', 'add-library', 'retry-libraries'])

const route = useRoute()
const router = useRouter()

const mediaLibs = computed(() => buildMediaLibs(props.libs))
const tabs = computed(() => buildTabs(mediaLibs.value))
const selectedId = ref(null)
const openedTabs = ref(new Set())
const selectedTab = computed(() => tabs.value.find(t => t.id === selectedId.value) || null)
const requests = ref({})          // 深链请求：tab id → { sec, ids, nonce }
const statusByTab = ref({})       // 子组件状态上报：tab id → { pending, ... }
const savedOrigin = readFileOrigin()
const fileOrigin = ref(fileOriginMatches(savedOrigin, route) ? savedOrigin : null)
if (!fileOrigin.value) writeFileOrigin(null)
const returnOrigin = computed(() => fileOriginMatches(fileOrigin.value, route) ? fileOrigin.value : null)
const sourceTab = computed(() => tabs.value.find(tab => tab.id === returnOrigin.value?.library))
const sourceViewLabel = computed(() => ({ workflow: '入库整理', maintenance: '资料维护', restore: '还原位置' }[returnOrigin.value?.view] || ''))
const restoreSnapshot = ref(null)
const removeNavigationObserver = router.afterEach((to, from, failure) => {
  const transition = fileOriginTransition(fileOrigin.value, to, from, failure)
  if (failure) return
  restoreSnapshot.value = transition.restore
  fileOrigin.value = writeFileOrigin(transition.origin)
})
watch([tabs, () => props.librariesReady], ([list, ready]) => {
  if (!ready) return
  if (fileOrigin.value && !list.some(tab => tab.id === fileOrigin.value.library)) fileOrigin.value = writeFileOrigin(null)
  if (restoreSnapshot.value && !list.some(tab => tab.id === restoreSnapshot.value.library)) restoreSnapshot.value = null
}, { immediate: true })
const filesPage = computed(() => props.active && route.query.sec === 'sec-files')
const filesVisited = ref(route.query.sec === 'sec-files')
const filesRef = ref(null)
// 详情页“在文件管理中打开”带来的目标目录（库内相对路径）：透传给 FsBrowser 消费一次。
const pendingFilesPath = ref('')
const selectedMedia = computed(() => mediaLibs.value.find(m => Number(m.id) === selectedTab.value?.media_id) || null)
const { changes, errors: changeErrors, refresh: refreshChanges } = useFileChanges()
const changesError = computed(() => changeErrors.value[selectedId.value] || '')
const currentChanges = computed(() => changes.value[selectedId.value] || null)
const working = computed(() => Boolean(filesRef.value?.hasPendingOperation?.() || currentChanges.value?.active_jobs?.length))
const reviewOpen = ref(false)
const reviewScanning = ref(false)
const fileScanError = ref('')
const { blocked: scanBlocked, message: scanMessage, start: startFileScan } = useLibraryScan(
  () => selectedTab.value,
  async () => { await refreshChanges(selectedId.value); emit('changed') },
)
let reviewResolve = null
let reviewPromise = null
let reviewChecking = false
let allowedScanLibrary = null
let changesTimer = null
let disposed = false
let focusGeneration = 0

async function openFiles(tab, state) {
  const previous = fileOrigin.value
  const token = globalThis.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
  const snapshot = createFileOrigin(tab, state, token)
  fileOrigin.value = writeFileOrigin(snapshot)
  try {
    const failure = await router.push(fileWorkspaceTarget(snapshot))
    if (failure && fileOrigin.value?.token === snapshot.token) fileOrigin.value = writeFileOrigin(previous)
    else if (fileOriginMatches(snapshot, route)) window.scrollTo({ top: 0, behavior: 'instant' })
  } catch (error) {
    if (fileOrigin.value?.token === snapshot.token) fileOrigin.value = writeFileOrigin(previous)
    fileScanError.value = '无法打开文件管理：' + error.message
  }
}
function returnToTools() {
  if (returnOrigin.value) return router.push(fileReturnTarget(returnOrigin.value))
}
function settleReview(action) {
  reviewOpen.value = false
  reviewResolve?.(action)
  reviewResolve = null
  reviewPromise = null
}
async function beginFileScan() {
  fileScanError.value = ''
  const fresh = await refreshChanges(selectedId.value, { fresh: true })
  if (!fresh) { fileScanError.value = changesError.value || '无法确认文件任务状态，请重试。'; return false }
  if (working.value) { fileScanError.value = '文件操作仍在进行，请完成后再扫描。'; return false }
  const started = await startFileScan()
  if (!started) fileScanError.value = scanMessage.value || '暂时无法启动扫描，请稍后重试。'
  return started
}
async function reviewScan() {
  if (reviewScanning.value) return
  reviewScanning.value = true
  try { if (await beginFileScan()) settleReview('scan') }
  finally { reviewScanning.value = false }
}
async function guardFiles(to, from) {
  if (from.path !== '/settings' || from.query.sec !== 'sec-files') return true
  const id = selectedId.value
  if (allowedScanLibrary === id && to.path === '/settings' && to.query.sec === 'sec-sync' && Number(to.query.library) === id) {
    allowedScanLibrary = null
    return true
  }
  const resolveLibrary = q => resolveFocusTab(tabs.value, q)?.id || id
  if (!leavesFileLibrary(to, id, resolveLibrary)) return true
  reviewChecking = true
  try { await refreshChanges(id, { fresh: true }) } finally { reviewChecking = false }
  if (disposed) return false
  if (!needsFileReview(changes.value[id], working.value) && !changesError.value) return true
  if (!reviewPromise) {
    fileScanError.value = ''
    filesRef.value?.closePreview?.()
    reviewOpen.value = true
    reviewPromise = new Promise(resolve => { reviewResolve = resolve })
  }
  const action = await reviewPromise
  if (action === 'later') return true
  if (action === 'scan') {
    allowedScanLibrary = id
    return scanTarget(selectedTab.value)
  }
  return false
}
onBeforeRouteLeave(guardFiles)
onBeforeRouteUpdate(guardFiles)

function beforeUnload(event) {
  if (!filesPage.value || !needsFileReview(currentChanges.value, working.value)) return
  event.preventDefault()
  event.returnValue = ''
}
async function scanFiles({ library_id } = {}) {
  if (Number(library_id) !== selectedId.value || reviewScanning.value) return
  reviewScanning.value = true
  try {
    if (await beginFileScan()) {
      allowedScanLibrary = selectedId.value
      await router.push(scanTarget(selectedTab.value))
    }
  } finally { reviewScanning.value = false }
}
function switchFileLibrary({ library_id } = {}) {
  pendingFilesPath.value = ''
  const tab = tabs.value.find(t => t.id === Number(library_id))
  if (tab) return router.push({ path: '/settings', query: { sec: 'sec-files', library: String(tab.id), media: String(tab.media_id),
    ...(returnOrigin.value ? { files_from: returnOrigin.value.token } : {}) } })
}
async function onFilesChanged(change) {
  const id = Number(change?.library_id) || selectedId.value
  // Keep an immediate marker if a network failure hides the server's durable event.
  if (Number(change?.count) > 0) {
    changes.value = { ...changes.value, [id]: { ...changes.value[id], pending: true, count: (changes.value[id]?.count || 0) + change.count } }
  }
  await refreshChanges(id, { fresh: true })
  emit('changed')
}
async function pollChanges() {
  if (disposed) return
  if ((filesPage.value || reviewOpen.value) && !reviewChecking && !reviewScanning.value) await refreshChanges(selectedId.value)
  if (!disposed) changesTimer = setTimeout(pollChanges, 2000)
}

function onStatus(id, s) {
  statusByTab.value = { ...statusByTab.value, [id]: s || {} }
}
function badgeOf(id) {
  return Number((statusByTab.value[id] || {}).pending) || 0
}
function tabTitle(t) {
  const s = statusByTab.value[t.id] || {}
  const parts = []
  if (s.pending) parts.push(`待处理 ${s.pending}`)
  if (s.organize) parts.push(`可归档 ${s.organize}`)
  if (s.restore) parts.push(`待恢复 ${s.restore}`)
  if (s.shows) parts.push(`${s.shows} 部剧`)
  return parts.length ? parts.join(' · ') : t.label
}

function syncUrl(id) {
  try {
    const q = { ...route.query, sec: filesPage.value ? 'sec-files' : 'sec-libtools' }
    delete q.ids
    delete q.files_path
    const t = tabs.value.find(t => t.id === Number(id))
    if (t) {
      q.library = String(t.id)
      if (t.media_id != null) q.media = String(t.media_id)
      else delete q.media
    } else {
      delete q.library
      delete q.media
    }
    return router.push({ path: '/settings', query: q })
  } catch (e) { /* URL 同步失败不影响功能 */ }
}

function select(id, opts = {}) {
  const t = tabs.value.find(x => x.id === Number(id))
  if (!t) return
  if (opts.sync) return syncUrl(t.id)
  selectedId.value = t.id
  openedTabs.value.add(t.id)
  // This branch runs after the router accepts navigation; sync the app header too.
  // switchMedia remembers its first video library, so restore the exact selected tab last.
  if (t.media_id != null && Number(currentMediaId()) !== Number(t.media_id)) switchMedia(t.media_id)
  setStoredLibId(t.id)
  if (opts.scroll) {
    document.getElementById('sec-libtools')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

function pickInitial() {
  const list = tabs.value
  if (!list.length) { selectedId.value = null; return }
  if (selectedId.value != null && list.some(t => t.id === selectedId.value)) return
  const q = route.query || {}
  const ql = q.library != null && q.library !== '' ? Number(q.library) : null
  const qm = q.media != null && q.media !== '' ? Number(q.media) : null
  const target = (ql != null ? list.find(t => t.id === ql) : null)
    || (qm != null ? list.find(t => t.media_id === qm) : null)
    || pickTab(list, { storedId: getStoredLibId(), currentMediaId: props.currentMediaId })
  if (target) select(target.id)
}
watch(tabs, pickInitial)

// 深链承接：?sec=…&media=…&library=…&ids=…&files_path=…
async function focus({ media, library, sec, ids, files_path } = {}) {
  const generation = ++focusGeneration
  // 库列表可能还在异步路上（父级 loadLibs 后 prop 才到）：等它就绪再选中
  for (let i = 0; i < 40 && !tabs.value.length; i++) {
    await new Promise(r => setTimeout(r, 50))
    if (disposed || generation !== focusGeneration) return
  }
  if (disposed || generation !== focusGeneration) return
  const target = resolveFocusTab(tabs.value, { media, library, sec }) || selectedTab.value
  if (!target) {
    if (sec) document.getElementById(sec)?.scrollIntoView({ block: 'start' })
    return
  }
  select(target.id)
  // Directory loading should start immediately, independently of the change summary.
  if (sec === 'sec-files') filesVisited.value = true
  pendingFilesPath.value = sec === 'sec-files' ? (files_path || '') : ''
  await refreshChanges(target.id)
  if (disposed || generation !== focusGeneration) return
  if (sec === 'sec-files') return
  await nextTick()
  if (disposed || generation !== focusGeneration) return
  const restoreIds = (Array.isArray(ids) ? ids : []).map(Number).filter(Number.isFinite)
  const restore = restoreSnapshot.value?.library === target.id && route.query.files_return === restoreSnapshot.value.token
    ? restoreSnapshot.value : null
  restoreSnapshot.value = null
  requests.value = {
    ...requests.value,
    [target.id]: { sec: sec || '', ids: restoreIds, restore, nonce: Date.now() },
  }
  await nextTick()
  if (disposed || generation !== focusGeneration) return
  if (!sec && !restoreIds.length) {
    document.getElementById('sec-libtools')?.scrollIntoView({ block: 'start' })
  }
}

function onChanged() {
  emit('changed')
}

onMounted(() => { pickInitial(); window.addEventListener('beforeunload', beforeUnload); pollChanges() })
onUnmounted(() => {
  disposed = true
  removeNavigationObserver()
  clearTimeout(changesTimer)
  window.removeEventListener('beforeunload', beforeUnload)
  settleReview('stay')
})
defineExpose({ select, focus })
</script>
<style scoped>
.library-workspace { padding: 24px 0 0; }
.library-selector-label { font-size: var(--jz-font-m); color: var(--jz-text-dim); margin: 0 0 12px; font-weight: 500; }
.library-workspace.files-workspace { padding: 20px 0 0; }
.library-load-state { min-height: 280px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px; padding: 24px; text-align: center; color: var(--jz-text-dim); }
.library-load-state strong { color: var(--jz-text); font-size: var(--jz-font-m); font-weight: 500; }
.library-load-state > span { font-size: var(--jz-font-s); overflow-wrap: anywhere; }
.file-return-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-bottom: 16px; }
.file-return-bar span { color: var(--jz-text-dim); font-size: var(--jz-font-m); }

.hint { color: var(--jz-text-dim); font-size: 0.8125rem; margin: 0 0 4px; }
.warn-text { color: var(--jz-warn); }
.fhint { font-size: 0.75rem; color: var(--jz-text-dim); font-weight: normal; }
.lib-tabs { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 24px; }
.lib-tabs button { display: inline-flex; align-items: center; gap: 6px; }
.lib-tabs button.on { border-color: var(--jz-accent); background: var(--jz-surface-3); color: var(--jz-text); }
.lib-tabs button.off { opacity: 0.6; }
.tab-kind { font-size: 0.6875rem; color: var(--jz-text-dim); border: 1px solid var(--jz-border-strong); border-radius: 999px; padding: 0 6px; }
.nav-badge { font-size: 0.75rem; color: var(--jz-warn); }
@media (max-width: 700px) { .lib-tabs { flex-wrap: nowrap; overflow-x: auto; padding-bottom: 4px; margin-bottom: 16px; } .lib-tabs button { flex-shrink: 0; } }
</style>
