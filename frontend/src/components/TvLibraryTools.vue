<template>
  <section :id="active ? 'sec-pipeline' : undefined" class="card-block">
    <div class="tool-views" aria-label="视频库工具">
      <button v-for="view in toolViews" :key="view.key" :class="{ on: toolView === view.key }"
        :aria-pressed="toolView === view.key" @click="chooseView(view.key)">{{ view.label }}</button>
    </div>
    <div v-show="toolView === 'workflow'">
    <div class="status-line">
      <span v-if="statsReady" class="chip">{{ stats.shows }} 部剧 · {{ stats.episodes }} 集</span>
      <span v-if="unmatchedCount" class="chip warn">未匹配 {{ unmatchedCount }}</span>
      <span v-if="reviewCount" class="chip warn">待确认 {{ reviewCount }}</span>
      <span v-if="episodeReviewCount" class="chip warn">未匹配集号 {{ episodeReviewCount }}</span>
      <span v-if="pendingLoaded && !pendingTotal" class="chip ok">✓ 剧集全部已匹配</span>
      <span v-if="!tab.enabled" class="chip warn">库已停用：扫描/写入被跳过</span>
    </div>

    <div :id="active ? 'sec-sync' : undefined" class="pipe-step">
      <button type="button" class="pipe-head pipe-toggle" :aria-expanded="openStep === 'scan'" @click="toggleStep('scan')">
        <span class="step-title"><span class="step-no">①</span> 扫描入库</span>
        <span class="fhint">{{ openStep === 'scan' ? '收起' : '展开' }}</span>
        <span class="step-state" :class="{ run: scanRunning }">{{ scanStateText }}</span>
      </button>
      <div v-show="openStep === 'scan'" class="pipe-body">
        <p class="hint">读取新剧集，自动匹配资料和海报，并移除文件已不存在的记录。</p>
        <div class="bar">
          <button class="primary" @click="startScan" :disabled="!!busy || scanBlocked || !tab.enabled">
            {{ scanRunning ? '扫描中…' : '扫描新文件' }}
          </button>
          <button v-if="scanRunning" @click="cancelScan">取消扫描</button>
          <span>{{ scanMsg }}</span>
        </div>
      </div>
    </div>

    <div :id="active ? 'sec-pending' : undefined" class="pipe-step">
      <button type="button" class="pipe-head pipe-toggle" :aria-expanded="openStep === 'match'" @click="toggleStep('match')">
        <span class="step-title"><span class="step-no">②</span> 核对匹配 <span v-if="pendingTotal" class="nav-badge">{{ pendingTotal }}</span></span>
        <span class="fhint">{{ openStep === 'match' ? '收起' : '展开' }}</span>
        <span class="step-state" :class="{ ok: !pendingTotal && pendingLoaded }">{{ pendingTotal ? `${pendingTotal} 部待处理` : (pendingLoaded ? '✓ 全部已匹配' : '') }}</span>
      </button>
      <div v-show="openStep === 'match'" class="pipe-body">
        <details v-if="pendingTotal" class="settings-details"><summary>如何处理匹配问题</summary><p class="hint">未匹配：选择对应剧集。待确认：核对后点击「匹配正确」。集号待处理：进入详情页指定对应集号。</p></details>
        <div class="bar">
          <button @click="loadPending()" :disabled="!!busy">刷新列表</button>
          <span v-if="pendingTotal">{{ pendingSummary }}</span>
          <span v-else-if="pendingLoaded">全部已匹配</span>
          <button v-if="reviewRows.length > 1" :disabled="!!busy" @click="confirmAll">
            {{ busy === 'confirm' ? '确认中…' : `确认全部匹配（${reviewRows.length}）` }}
          </button>
        </div>
        <p v-if="pendingMsg" class="feedback" role="status">{{ pendingMsg }}</p>
        <ul class="miss-list">
          <li v-for="it in visiblePending" :key="'sp' + it.id" class="miss-row">
            <span class="kind-badge" :class="{ bad: !it.tmdb_id }">{{ rowBadge(it) }}</span>
            <span class="miss-title">{{ it.title || '(未命名)' }}<span v-if="it.year"> ({{ it.year }})</span></span>
            <span class="miss-path">
              {{ it.season_count }} 季 · {{ it.episode_count }} 集
              <span v-if="it.episode_review_count" class="fhint"> · 未匹配集号 {{ it.episode_review_count }}</span>
            </span>
            <button v-if="it.needs_review" :disabled="!!busy" title="匹配无误，清除待确认" @click="confirmOne(it.id)">匹配正确</button>
            <button @click="$router.push('/tv/' + it.id)">{{ it.tmdb_id ? '核对集号' : '匹配剧集' }}</button>
          </li>
          <li v-if="pendingTotal > COLLAPSE_N" class="miss-row collapse-row">
            <button @click="pendExpand = !pendExpand">{{ pendExpand ? '收起' : `展开全部 (${pendingTotal})` }}</button>
          </li>
        </ul>
      </div>
    </div>

    <div class="pipe-step">
      <button type="button" class="pipe-head pipe-toggle" :aria-expanded="openStep === 'organize'" @click="toggleStep('organize')">
        <span class="step-title"><span class="step-no">③</span> 目录整理</span>
        <span class="fhint">{{ openStep === 'organize' ? '收起' : '展开' }}</span>
        <span class="step-state">{{ orgState }}</span>
      </button>
      <div v-show="openStep === 'organize'" class="pipe-body">
        <TvOrganizePanel :library="tab" :active="active" @changed="onOrganized" @status="onOrgStatus" />
      </div>
    </div>

    </div>
    <div v-if="visitedViews.has('maintenance')" v-show="toolView === 'maintenance'">
      <TvMaintenancePanel :library="tab" :active="active && toolView === 'maintenance'" @changed="onOrganized" />
    </div>
    <div v-if="visitedViews.has('files')" v-show="toolView === 'files'">
      <FsBrowser :active="active && toolView === 'files'" :media="tabMedia" :video-libs="[tab]" :initial-lib-id="tab.id"
        @changed="onOrganized" @scan="scanFromFiles" />
    </div>
  </section>
</template>
<script setup>
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { api } from '../api.js'
import { useLibraryScan } from '../useLibraryScan.js'
import { toolViewForSection } from '../settingsNavigation.js'
import { pickOpenStep, stepForSection } from '../libraryToolsTabs.js'
import TvOrganizePanel from './TvOrganizePanel.vue'
import TvMaintenancePanel from './TvMaintenancePanel.vue'
import FsBrowser from './FsBrowser.vue'

const COLLAPSE_N = 20
const props = defineProps({
  tab: { type: Object, required: true },
  active: { type: Boolean, default: false },
  request: { type: Object, default: null },
})
const emit = defineEmits(['changed', 'status'])

const tabMedia = computed(() => ({ id: props.tab.media_id, name: props.tab.media_name }))
const openStep = ref('scan')
const toolView = ref('workflow')
const visitedViews = ref(new Set(['workflow']))
const toolViews = [
  { key: 'workflow', label: '入库整理' },
  { key: 'maintenance', label: '资料维护' },
  { key: 'files', label: '文件管理' },
]
function chooseView(key) {
  toolView.value = key
  visitedViews.value.add(key)
}
function scanFromFiles() {
  chooseView('workflow')
  toggleStep('scan')
  openStep.value = 'scan'
  startScan()
}
const userToggled = ref(false)
const busy = ref(null)
const stats = ref({ shows: 0, episodes: 0 })
const statsReady = ref(false)
const orgState = ref('')


function toggleStep(key) {
  userToggled.value = true
  openStep.value = openStep.value === key ? '' : key
}

async function loadStats() {
  try {
    stats.value = await api('/api/tv/stats?library=' + props.tab.id)
    statsReady.value = true
  } catch (e) { /* 状态行失败不挡工具 */ }
}

// ② 待处理剧（未匹配 / 剧级待确认 / 有未匹配集）
const pending = ref([])
const pendingLoaded = ref(false)
const pendingMsg = ref('')
const pendExpand = ref(false)
const pendingTotal = computed(() => pending.value.length)
const unmatchedCount = computed(() => pending.value.filter(it => !it.tmdb_id).length)
const reviewCount = computed(() => pending.value.filter(it => it.tmdb_id && it.needs_review).length)
const episodeReviewCount = computed(() => pending.value
  .filter(it => it.tmdb_id && !it.needs_review)
  .reduce((a, it) => a + (Number(it.episode_review_count) || 0), 0))
const reviewRows = computed(() => pending.value.filter(it => it.needs_review))
const visiblePending = computed(() => pendExpand.value ? pending.value : pending.value.slice(0, COLLAPSE_N))
const pendingSummary = computed(() => [
  ['未匹配', unmatchedCount.value], ['待确认', reviewCount.value], ['集号待处理', episodeReviewCount.value],
].filter(([, count]) => count).map(([label, count]) => `${label} ${count}`).join(' · '))
function rowBadge(it) {
  if (!it.tmdb_id) return '未匹配'
  if (it.needs_review) return '待确认'
  return '集号待处理'
}
async function loadPending(silent) {
  if (!silent) pendingMsg.value = ''
  try {
    const d = await api(`/api/tv/shows?library=${props.tab.id}&pending=1&limit=500`)
    pending.value = d.items || []
    pendingLoaded.value = true
    pendingMsg.value = ''
  } catch (e) {
    pendingMsg.value = '待处理剧加载失败：' + e.message
  }
}
async function confirmOne(id) {
  pendingMsg.value = ''
  busy.value = 'confirm'
  try {
    await api(`/api/tv/shows/${id}/confirm-match`, { method: 'POST' })
    pending.value = pending.value.filter(it => it.id !== id)
    emit('changed')
  } catch (e) {
    pendingMsg.value = '确认失败：' + e.message
  } finally {
    busy.value = null
  }
}
async function confirmAll() {
  const ids = reviewRows.value.map(it => it.id)
  if (!ids.length) return
  pendingMsg.value = ''
  busy.value = 'confirm'
  try {
    for (const id of ids) {
      await api(`/api/tv/shows/${id}/confirm-match`, { method: 'POST' })
    }
    const set = new Set(ids)
    pending.value = pending.value.filter(it => !set.has(it.id))
    emit('changed')
  } catch (e) {
    pendingMsg.value = '确认失败：' + e.message
  } finally {
    busy.value = null
  }
}

const { running: scanRunning, blocked: scanBlocked, message: scanMsg, stateText: scanStateText, start: startScan, cancel: cancelScan } = useLibraryScan(() => props.tab, async () => { emit('changed'); await Promise.all([loadStats(), loadPending(true)]) })

// ③ 目录整理：预览/执行后刷新待处理与状态
function onOrgStatus(s) {
  if (!s) return
  if (s.executed) { orgState.value = `已执行 ${s.executed} 项`; return }
  orgState.value = s.plans ? `${s.plans} 部可整理` : '✓ 无需整理'
}
async function onOrganized() {
  emit('changed')
  await Promise.all([loadStats(), loadPending(true)])
}

// 单步聚焦：首次数据到达自动展开有待办的步骤；用户手动切换后不抢回
const stepStates = computed(() => [
  { key: 'scan', count: 0 },
  { key: 'match', count: pendingTotal.value },
  { key: 'organize', count: 0 },
])
watch(stepStates, (st) => {
  if (userToggled.value) return
  openStep.value = pickOpenStep(st) || 'scan'
}, { immediate: true, deep: true })

// 深链：打开目标步骤/更多工具并定位
watch(() => props.request, async (req) => {
  if (!req || !props.active) return
  const step = stepForSection(req.sec)
  if (step) {
    userToggled.value = true
    openStep.value = step === 'pending' ? 'match' : step
  }
  chooseView(toolViewForSection(req.sec))
  await nextTick()
  if (req.sec) document.getElementById(req.sec)?.scrollIntoView({ block: 'start' })
}, { immediate: true })

// 首次激活加载（每个 Tab 一个实例，懒加载）
const loaded = ref(false)
async function ensure() {
  if (loaded.value) return
  loaded.value = true
  await Promise.all([loadStats(), loadPending(true)])
}
onMounted(() => { if (props.active) ensure() })
watch(() => props.active, (v) => { if (v) ensure() })

// 状态上报（Tab 徽章）
watch([pendingTotal, stats], () => {
  emit('status', { pending: pendingTotal.value, shows: stats.value.shows, episodes: stats.value.episodes })
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
.hint code { color: #9ecfff; }
.fhint { font-size: 0.75rem; color: #888; font-weight: normal; }
.nav-badge { margin-left: 6px; font-size: 0.75rem; color: #e0a63c; }
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
