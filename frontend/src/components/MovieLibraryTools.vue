<template>
  <section :id="active ? 'sec-pipeline' : undefined" class="card-block">
    <div class="library-tools-header"><div class="tool-views" aria-label="视频库工具">
      <button v-for="view in toolViews" :key="view.key" :class="{ on: toolView === view.key }"
        :aria-pressed="toolView === view.key" @click="chooseView(view.key)">{{ view.label }}</button>
    </div><button type="button" class="open-library-files" @click="openFiles">管理文件</button></div>
    <div v-show="toolView === 'workflow'">
    <div class="status-line">
      <span v-if="pendingTotal" class="chip warn">待处理 {{ pendingTotal }}</span>
      <span v-else-if="pendingLoaded" class="chip ok">✓ 无待匹配</span>
      <span v-if="orgCount" class="chip warn">可归档 {{ orgCount }}</span>
      <span v-if="missing.length" class="chip warn">失效 {{ missing.length }}</span>
      <span v-if="!tab.enabled" class="chip warn">库已停用：扫描/写入被跳过</span>
    </div>

    <div :id="active ? 'sec-sync' : undefined" class="pipe-step">
      <button type="button" class="pipe-head pipe-toggle" :aria-expanded="openStep === 'scan'" @click="toggleStep('scan')">
        <span class="step-title"><span class="step-no">①</span> 扫描入库</span>
        <span class="fhint">{{ openStep === 'scan' ? '收起' : '展开' }}</span>
        <span class="step-state" :class="{ run: scanRunning }">{{ scanStateText }}</span>
      </button>
      <div v-show="openStep === 'scan'" class="pipe-body">
        <p class="hint">读取新影片并匹配资料，同时移除文件已不存在的记录。</p>
        <div class="bar">
          <button class="primary" @click="startScan" :disabled="!!busy || scanBlocked || !tab.enabled">
            {{ scanRunning ? '扫描中…' : '扫描新文件' }}
          </button>
          <button v-if="scanRunning" @click="cancelScan">取消扫描</button>
          <span>{{ scanMsg }}</span>
          <button v-if="scanStateText === '完成'" @click="toggleStep('pending')">核对匹配结果 →</button>
        </div>
        <div class="bar">
          <button @click="loadMissing()" :disabled="!!busy || missingLoading">{{ missingLoading ? '检查中…' : '检查失效条目' }}</button>
          <button v-if="missing.length" @click="scanOpen = !scanOpen">{{ scanOpen ? '收起清单' : '展开清单' }}</button>
          <span v-if="missing.length">共 {{ missing.length }} 条失效</span>

          <span>{{ cleanMsg }}</span>
        </div>
        <div v-show="scanOpen && missing.length">
          <div class="lib-group-head">
            <b>失效条目</b>
            <span class="fhint">{{ missing.length }} 条</span>
            <button @click="toggleMissing">{{ allMissingChecked ? '全不选' : '全选' }}</button>
            <button @click="doClean(checkedMissing)" :disabled="!!busy || !checkedMissing.length">
              {{ busy === 'clean' ? '清理中…' : `移除失效记录 (${checkedMissing.length})` }}
            </button>
          </div>
          <ul class="miss-list">
            <li v-for="m in seeMore" :key="m.id" class="miss-row">
              <input type="checkbox" :value="m.id" v-model="checkedMissing" />
              <span class="miss-title">{{ m.title || '(未命名)' }}<span v-if="m.year"> ({{ m.year }})</span></span>
              <span class="miss-path">{{ m.file_path }}</span>
            </li>
            <li v-if="missing.length > COLLAPSE_N" class="miss-row collapse-row">
              <button @click="missExpand = !missExpand">{{ missExpand ? '收起' : `展开全部 (${missing.length})` }}</button>
            </li>
          </ul>
        </div>
      </div>
    </div>

    <div :id="active ? 'sec-pending' : undefined" class="pipe-step">
      <button type="button" class="pipe-head pipe-toggle" :aria-expanded="openStep === 'pending'" @click="toggleStep('pending')">
        <span class="step-title"><span class="step-no">②</span> 核对匹配 <span v-if="pendingTotal" class="nav-badge">{{ pendingTotal }}</span></span>
        <span class="fhint">{{ openStep === 'pending' ? '收起' : '展开' }}</span>
        <span class="step-state" :class="{ ok: !pendingTotal && pendingLoaded }">{{ pendingTotal ? `${pendingTotal} 项待处理` : (pendingLoaded ? '✓ 全部已匹配' : '') }}</span>
      </button>
      <div v-show="openStep === 'pending'" class="pipe-body">
        <details v-if="pendingTotal" class="settings-details"><summary>如何处理匹配问题</summary><p class="hint">未匹配或标题可疑：进入详情核对。待确认：核对后点击「匹配正确」。未归属花絮：填写影片 ID，将其关联到正片。</p></details>
        <div class="bar">
          <button @click="loadUnmatched()" :disabled="!!busy">刷新列表</button>
          <span v-if="pendingTotal">{{ pendingSummary }}</span>
          <span v-else-if="pendingLoaded">全部已匹配</span>
          <button v-if="needsReview.length > 1" :disabled="!!busy" title="待确认一次性清除" @click="confirmReview(needsReview.map(m => m.id))">
            确认全部匹配（{{ needsReview.length }}）
          </button>
        </div>
        <p v-if="pendingMsg" class="feedback" role="status">{{ pendingMsg }}</p>
        <ul class="miss-list">
          <li v-for="it in visiblePending" :key="it.category + '-' + it.id" class="miss-row">
            <span class="kind-badge" :class="{ bad: it.category === 'unmatched' }">{{ it.categoryLabel }}</span>
            <span class="miss-title">{{ it.title || (it.category === 'orphan' ? (it.kind || '花絮') : '(未命名)') }}<span v-if="it.year"> ({{ it.year }})</span></span>
            <span class="miss-path" :title="it.file_path">{{ it.file_path }}<span v-if="it.guessed_title" class="fhint">（猜测：{{ it.guessed_title }}{{ it.guessed_year ? ' ' + it.guessed_year : '' }}）</span></span>
            <template v-if="it.category === 'orphan'">
              <input v-model="orphanMovie[it.id]" placeholder="影片ID" style="width:80px" />
              <button @click="attachOrphan(it.id)" :disabled="!!busy">关联影片</button>
            </template>
            <template v-else>
              <button v-if="it.category === 'needs_review'" :disabled="!!busy" title="匹配无误，清除待确认" @click="confirmReview([it.id])">匹配正确</button>
              <button @click="$router.push('/m/' + it.id)">核对匹配</button>
            </template>
          </li>
          <li v-if="pendingRows.length > COLLAPSE_N" class="miss-row collapse-row">
            <button @click="pendExpand = !pendExpand">{{ pendExpand ? '收起' : `展开全部 (${pendingRows.length})` }}</button>
          </li>
        </ul>

      </div>
    </div>

    <div class="pipe-step">
      <button type="button" class="pipe-head pipe-toggle" :aria-expanded="openStep === 'organize'" @click="toggleStep('organize')">
        <span class="step-title"><span class="step-no">③</span> 目录整理</span><span class="step-state">{{ orgCount ? `${orgCount} 项可整理` : '' }}</span>
      </button>
    </div>
    <div v-show="openStep === 'organize'">
    <OrganizePanel ref="organizeRef" :library="tab" :active="active" :embedded="true" @status="orgCount = $event" @changed="onOrganized" />
      <details class="settings-details"><summary>整理已关联的花絮</summary><p class="hint">将已关联花絮移动到各影片目录的 extras 文件夹。</p>
        <div class="bar">
          <button @click="doCollectExtras" :disabled="!!busy">{{ busy === 'collect' ? '归位中…' : (armCollect ? '确认移动花絮' : '整理已关联花絮') }}</button>
          <span>{{ collectMsg }}</span>
        </div>
      </details>
    </div>

    </div>
    <div v-if="visitedViews.has('maintenance')" v-show="toolView === 'maintenance'">
      <LibraryMaintenancePanel :library="tab" :active="active && toolView === 'maintenance'" @changed="onChanged" />
    </div>
    <div v-if="visitedViews.has('restore')" v-show="toolView === 'restore'">
      <RestorePanel ref="restoreRef" :library="tab" :active="active && toolView === 'restore'" :preselect-ids="preselectIds" @count="restoreCount = $event" @changed="onChanged" />
    </div>
  </section>
</template>
<script setup>
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { api } from '../api.js'
import { useLibraryScan } from '../useLibraryScan.js'
import { toolViewForSection } from '../settingsNavigation.js'
import { pickOpenStep, stepForSection } from '../libraryToolsTabs.js'
import OrganizePanel from './OrganizePanel.vue'
import LibraryMaintenancePanel from './LibraryMaintenancePanel.vue'
import RestorePanel from './RestorePanel.vue'

const COLLAPSE_N = 20
const props = defineProps({
  tab: { type: Object, required: true },
  active: { type: Boolean, default: false },
  request: { type: Object, default: null },
})
const emit = defineEmits(['changed', 'status', 'open-files'])

const openStep = ref('scan')
const toolView = ref('workflow')
const visitedViews = ref(new Set(['workflow']))
const toolViews = [
  { key: 'workflow', label: '入库整理' },
  { key: 'maintenance', label: '资料维护' },
  { key: 'restore', label: '还原位置' },
]
function chooseView(key) {
  toolView.value = key
  visitedViews.value.add(key)
}
const preselectIds = ref([])
const restoreCount = ref(0)
const userToggled = ref(false)
function openFiles() {
  emit('open-files', { view: toolView.value, step: openStep.value,
    scrollTop: window.scrollY, ids: restoreRef.value?.selectedIds?.() || [...preselectIds.value] })
}

function toggleStep(key) {
  userToggled.value = true
  openStep.value = openStep.value === key ? '' : key
}

// ① 扫描入库（本视频库；后台任务轮询，可取消）
const busy = ref(null)
const { running: scanRunning, blocked: scanBlocked, message: scanMsg, stateText: scanStateText, start: startScan, cancel: cancelScan } = useLibraryScan(() => props.tab, async () => { emit('changed'); await Promise.all([loadMissing(true), loadUnmatched(true)]); await organizeRef.value?.refresh(true); orgCount.value = organizeRef.value?.count() || 0 })

// ① 失效条目（单库）
const missing = ref([])
const checkedMissing = ref([])
const missingLoading = ref(false)
const missExpand = ref(false)
const scanOpen = ref(true)
const cleanMsg = ref('')
const seeMore = computed(() => missExpand.value ? missing.value : missing.value.slice(0, COLLAPSE_N))
const allMissingChecked = computed(() => missing.value.length > 0
  && checkedMissing.value.length === missing.value.length)
function toggleMissing() {
  checkedMissing.value = allMissingChecked.value ? [] : missing.value.map(m => m.id)
}
async function loadMissing(silent) {
  if (missingLoading.value) return
  missingLoading.value = true
  if (!silent) cleanMsg.value = ''
  try {
    const d = await api('/api/files/missing?library=' + props.tab.id)
    missing.value = d.items || []
    checkedMissing.value = (d.items || []).map(m => m.id)
    if (!silent) cleanMsg.value = d.total ? '' : '没有失效条目'
  } catch (e) {
    if (!silent) cleanMsg.value = '检查失败：' + e.message
  } finally {
    missingLoading.value = false
  }
}
async function doClean(ids) {
  const list = (ids || []).slice(0, 5000)
  if (!list.length) return
  busy.value = 'clean'
  cleanMsg.value = ''
  try {
    const d = await api('/api/files/clean', {
      method: 'POST',
      body: JSON.stringify({ ids: list, dry_run: false, library_id: props.tab.id })
    })
    const removed = new Set((d.results || []).map(r => r.id))
    missing.value = missing.value.filter(m => !removed.has(m.id))
    checkedMissing.value = checkedMissing.value.filter(id => !removed.has(id))
    cleanMsg.value = `已删除 ${d.deleted}/${d.total}` + ((d.failed || []).length ? `，失败 ${d.failed.length}` : '')
    emit('changed')
  } catch (e) {
    cleanMsg.value = '清理失败：' + e.message
  } finally {
    busy.value = null
  }
}

// ② 核对匹配（单库；类别混排，行上带徽章）
const unmatched = ref([])
const needsReview = ref([])
const suspectHigh = ref([])
const suspectInfo = ref([])
const orphans = ref([])
const orphanMovie = ref({})
const pendingLoaded = ref(false)
const pendingMsg = ref('')
const pendExpand = ref(false)
const pendingRows = computed(() => {
  const rows = []
  const add = (arr, category, categoryLabel) => (arr || [])
    .forEach(it => rows.push({ ...it, category, categoryLabel }))
  add(unmatched.value, 'unmatched', '未匹配')
  add(needsReview.value, 'needs_review', '待确认')
  add(suspectHigh.value, 'suspect_high', '疑似英文')
  add(suspectInfo.value, 'suspect_info', '英文标题')
  add(orphans.value, 'orphan', '未归属花絮')
  return rows
})
const visiblePending = computed(() => pendExpand.value
  ? pendingRows.value : pendingRows.value.slice(0, COLLAPSE_N))
const pendingTotal = computed(() => pendingRows.value.length)
const pendingSummary = computed(() => [
  ['未匹配', unmatched.value.length], ['待确认', needsReview.value.length],
  ['标题待核对', suspectHigh.value.length + suspectInfo.value.length], ['未归属花絮', orphans.value.length],
].filter(([, count]) => count).map(([label, count]) => `${label} ${count}`).join(' · '))
async function loadUnmatched(silent) {
  if (!silent) pendingMsg.value = ''
  try {
    const d = await api('/api/files/unmatched?library=' + props.tab.id)
    unmatched.value = d.unmatched || []
    needsReview.value = d.needs_review || []
    suspectHigh.value = d.suspect_title_high || []
    suspectInfo.value = d.suspect_title_info || []
    orphans.value = d.orphan_extras || []
    pendingLoaded.value = true
    pendingMsg.value = ''
  } catch (e) {
    pendingMsg.value = '待处理加载失败：' + e.message
  }
}
async function confirmReview(ids) {
  const list = (ids || []).slice(0, 500)
  if (!list.length) return
  pendingMsg.value = ''
  busy.value = 'confirm'
  try {
    await api('/api/movies/batch', {
      method: 'POST',
      body: JSON.stringify({ ids: list, ops: { confirm_review: true } }),
    })
    const set = new Set(list)
    needsReview.value = needsReview.value.filter(m => !set.has(m.id))
    emit('changed')
  } catch (e) {
    pendingMsg.value = '确认失败：' + e.message
  } finally {
    busy.value = null
  }
}
async function attachOrphan(id) {
  const mid = Number((orphanMovie.value[id] || '').toString().trim())
  if (!Number.isInteger(mid) || mid <= 0) {
    pendingMsg.value = '请输入有效的影片 ID，可从影片详情页地址中查看'
    return
  }
  pendingMsg.value = ''
  busy.value = 'attach'
  try {
    await api('/api/extras/' + id + '/attach', { method: 'POST', body: JSON.stringify({ movie_id: mid }) })
    orphans.value = orphans.value.filter(e => e.id !== id)
    emit('changed')
  } catch (e) {
    pendingMsg.value = '关联失败：' + e.message
  } finally {
    busy.value = null
  }
}
const armCollect = ref(false)
const collectMsg = ref('')
async function doCollectExtras() {
  if (!armCollect.value) {
    armCollect.value = true
    collectMsg.value = '把已归属但散落在外的花絮搬进各片 extras/。再点一次确认执行'
    return
  }
  armCollect.value = false
  busy.value = 'collect'
  collectMsg.value = ''
  try {
    const prev = await api('/api/extras/collect', {
      method: 'POST', body: JSON.stringify({ dry_run: true, library_id: props.tab.id })
    })
    if (!prev.total) {
      collectMsg.value = '没有待归位花絮'
      return
    }
    const d = await api('/api/extras/collect', {
      method: 'POST', body: JSON.stringify({ dry_run: false, library_id: props.tab.id })
    })
    collectMsg.value = `已归位 ${d.moved} 个文件（${d.total} 部片）`
    await loadUnmatched(true)
    emit('changed')
  } catch (e) {
    collectMsg.value = '归位失败：' + e.message
  } finally {
    busy.value = null
  }
}

// ③ 归档整理：预览计数（供步骤状态与自动聚焦）；路径变化后刷新失效清单与恢复清单
const organizeRef = ref(null)
const restoreRef = ref(null)
const orgCount = ref(0)
async function onOrganized() {
  await loadMissing(true)
  orgCount.value = organizeRef.value?.count() || 0
  restoreRef.value?.reloadIfLoaded()
  emit('changed')
}
function onChanged() {
  emit('changed')
}

// 单步聚焦：首次数据到达自动展开有待办的步骤；用户手动切换后不抢回
const stepStates = computed(() => [
  { key: 'scan', count: 0 },
  { key: 'pending', count: pendingTotal.value },
  { key: 'organize', count: orgCount.value },
])
watch(stepStates, (st) => {
  if (userToggled.value) return
  openStep.value = pickOpenStep(st) || 'scan'
}, { immediate: true, deep: true })

// 深链：打开目标步骤/更多工具并定位（?sec=… / ?ids=…）
watch(() => props.request, async (req) => {
  if (!req || !props.active) return
  if (req.restore) {
    userToggled.value = true
    openStep.value = req.restore.step
    chooseView(req.restore.view)
    preselectIds.value = [...req.restore.ids]
    await nextTick()
    await ensure()
    if (req.restore.view === 'restore') await restoreRef.value?.ensure()
    await nextTick()
    if (props.active && props.request === req) window.scrollTo({ top: req.restore.scrollTop, behavior: 'instant' })
    return
  }
  const step = stepForSection(req.sec)
  if (step) {
    userToggled.value = true
    openStep.value = step
  }
  chooseView(toolViewForSection(req.sec, req.ids))
  if ((req.ids || []).length) preselectIds.value = req.ids
  await nextTick()
  if (req.sec) document.getElementById(req.sec)?.scrollIntoView({ block: 'start' })
}, { immediate: true })

// 首次激活加载（每个 Tab 一个实例，懒加载）
const loaded = ref(false)
let ensurePending = null
async function ensure() {
  if (ensurePending) return ensurePending
  if (loaded.value) return
  loaded.value = true
  ensurePending = (async () => {
    await Promise.all([loadMissing(true), loadUnmatched(true), organizeRef.value?.ensure(true)])
    orgCount.value = organizeRef.value?.count() || 0
  })()
  try { await ensurePending } finally { ensurePending = null }
}
onMounted(() => { if (props.active) ensure() })
watch(() => props.active, (v) => { if (v) ensure() })

// 状态上报（Tab 徽章）
watch([pendingTotal, orgCount, missing], () => {
  emit('status', { pending: pendingTotal.value, organize: orgCount.value, missing: missing.value.length, restore: restoreCount.value })
}, { immediate: true, deep: true })
</script>
<style scoped>
.card-block { background: #1c1c1c; border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }
.card-block h3 { margin: 0 0 10px; font-size: 1.0625rem; color: #ddd; }
.status-line { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 8px; }
.chip { font-size: 0.75rem; border: 1px solid #444; border-radius: 999px; padding: 1px 10px; color: #aaa; }
.chip.warn { color: #e0a63c; border-color: #6b5410; }
.chip.ok { color: #7ed321; border-color: #3a5a1e; }
.pipe-step { margin: 12px 0 0; border-top: 1px dashed #3a3a3a; padding-top: 10px; }
.pipe-head { display: flex; gap: 8px; align-items: baseline; }
.pipe-head h4 { margin: 0; font-size: 0.9375rem; color: #ccc; }
.step-no { color: #e50914; margin-right: 2px; }
.step-state { margin-left: auto; font-size: 0.75rem; color: #888; }
.step-state.run { color: #e0a63c; }
.step-state.ok { color: #7ed321; }
.pipe-toggle { cursor: pointer; user-select: none; }
.pipe-toggle:hover h4 { color: #fff; }
.pipe-body { margin-top: 6px; }
.hint { color: #777; font-size: 0.8125rem; margin: 0 0 4px; }
.fhint { font-size: 0.75rem; color: #888; font-weight: normal; }
.nav-badge { margin-left: 6px; font-size: 0.75rem; color: #e0a63c; }
.lib-group-head { display: flex; gap: 8px; align-items: center; font-size: 0.8125rem; color: #ccc; margin-bottom: 4px; }
.miss-list { list-style: none; margin: 4px 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.miss-row { display: flex; gap: 8px; align-items: center; background: #262626; border: 1px solid #3a3a3a; border-radius: 8px; padding: 6px 10px; font-size: 0.8125rem; }
.miss-title { white-space: nowrap; }
.miss-path { color: #888; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.kind-badge { font-size: 0.75rem; border: 1px solid #444; border-radius: 999px; padding: 1px 10px; color: #aaa; white-space: nowrap; }
.kind-badge.bad { color: #ff8a8a; border-color: #6e2b2b; }
.collapse-row { background: transparent; border: none; padding: 0; }
.more-tools { margin: 14px 0 0; border-top: 1px solid #2c2c2c; padding-top: 10px; }
.more-tools .pipe-head h4 { font-size: 0.875rem; }
</style>
