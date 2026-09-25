<template>
  <section :id="active ? 'sec-pipeline' : undefined" class="card-block">
    <h3>剧集库「{{ tab.name }}」 <span class="fhint">单步聚焦：先扫描 → 处理未匹配剧 → 整理目录；低频工具收在底部</span></h3>
    <div class="status-line">
      <span v-if="statsReady" class="chip">{{ stats.shows }} 部剧 · {{ stats.episodes }} 集</span>
      <span v-if="unmatchedCount" class="chip warn">未匹配 {{ unmatchedCount }}</span>
      <span v-if="reviewCount" class="chip warn">待确认 {{ reviewCount }}</span>
      <span v-if="episodeReviewCount" class="chip warn">未匹配集号 {{ episodeReviewCount }}</span>
      <span v-if="pendingLoaded && !pendingTotal" class="chip ok">✓ 剧集全部已匹配</span>
      <span v-if="!tab.enabled" class="chip warn">库已停用：扫描/写入被跳过</span>
    </div>

    <div :id="active ? 'sec-sync' : undefined" class="pipe-step">
      <div class="pipe-head pipe-toggle" @click="toggleStep('scan')">
        <h4><span class="step-no">①</span> 扫描入库</h4>
        <span class="fhint">{{ openStep === 'scan' ? '收起' : '展开' }}</span>
        <span class="step-state" :class="{ run: busy === 'scan' }">{{ scanStateText }}</span>
      </div>
      <div v-show="openStep === 'scan'" class="pipe-body">
        <p class="hint">新增一部剧：把剧集文件夹（如 <code>剧名 (年份)/Season 01/剧名-S01E01.mkv</code>）拷到 NAS 后点这里。
          扫描按目录结构识别剧/季/集，并自动匹配 TMDB、拉取元数据与海报；盘上已删的集同步清理。</p>
        <div class="bar">
          <button class="primary" @click="startScan" :disabled="!!busy || !tab.enabled">
            {{ busy === 'scan' ? '扫描中…' : '扫描本视频库' }}
          </button>
          <button v-if="busy === 'scan'" @click="cancelScan">取消</button>
          <span>{{ scanMsg }}</span>
        </div>
      </div>
    </div>

    <div :id="active ? 'sec-pending' : undefined" class="pipe-step">
      <div class="pipe-head pipe-toggle" @click="toggleStep('match')">
        <h4><span class="step-no">②</span> 剧集匹配 <span v-if="pendingTotal" class="nav-badge">{{ pendingTotal }}</span></h4>
        <span class="fhint">{{ openStep === 'match' ? '收起' : '展开' }}</span>
        <span class="step-state" :class="{ ok: !pendingTotal && pendingLoaded }">{{ pendingTotal ? `${pendingTotal} 部待处理` : (pendingLoaded ? '✓ 全部已匹配' : '') }}</span>
      </div>
      <div v-show="openStep === 'match'" class="pipe-body">
        <p class="hint">扫描没认出对应 TMDB 条目的剧在这里处理：
          <b>未匹配</b>→ 去详情页搜 TMDB 手动匹配（匹配成功会自动引导整理目录）；
          <b>待确认</b>→ 模糊命中，核对无误点「确认」；<b>未匹配集号</b>→ 剧内个别集对不上，去详情页逐集指定。</p>
        <div class="bar">
          <button @click="loadPending()" :disabled="!!busy">刷新</button>
          <span v-if="pendingTotal">{{ pendingSummary }}</span>
          <span v-else>全部已匹配</span>
          <button v-if="reviewRows.length > 1" :disabled="!!busy" @click="confirmAll">
            {{ busy === 'confirm' ? '确认中…' : `全部确认（${reviewRows.length}）` }}
          </button>
        </div>
        <ul class="miss-list">
          <li v-for="it in visiblePending" :key="'sp' + it.id" class="miss-row">
            <span class="kind-badge" :class="{ bad: !it.tmdb_id }">{{ rowBadge(it) }}</span>
            <span class="miss-title">{{ it.title || '(未命名)' }}<span v-if="it.year"> ({{ it.year }})</span></span>
            <span class="miss-path">
              {{ it.season_count }} 季 · {{ it.episode_count }} 集
              <span v-if="it.episode_review_count" class="fhint"> · 未匹配集号 {{ it.episode_review_count }}</span>
            </span>
            <button v-if="it.needs_review" :disabled="!!busy" title="匹配无误，清除待确认" @click="confirmOne(it.id)">确认</button>
            <button @click="$router.push('/tv/' + it.id)">{{ it.tmdb_id ? '去处理' : '去匹配' }}</button>
          </li>
          <li v-if="pendingTotal > COLLAPSE_N" class="miss-row collapse-row">
            <button @click="pendExpand = !pendExpand">{{ pendExpand ? '收起' : `展开全部 (${pendingTotal})` }}</button>
          </li>
        </ul>
      </div>
    </div>

    <div :id="active ? 'sec-tvorganize' : undefined" class="pipe-step">
      <div class="pipe-head pipe-toggle" @click="toggleStep('organize')">
        <h4><span class="step-no">③</span> 目录整理</h4>
        <span class="fhint">{{ openStep === 'organize' ? '收起' : '展开' }}</span>
        <span class="step-state">{{ orgState }}</span>
      </div>
      <div v-show="openStep === 'organize'" class="pipe-body">
        <TvOrganizePanel :library="tab" :active="active" @changed="onOrganized" @status="onOrgStatus" />
      </div>
    </div>

    <div class="more-tools">
      <div class="pipe-head pipe-toggle" @click="advancedOpen = !advancedOpen">
        <h4>更多工具 <span class="fhint">剧集刮削 / NFO 落盘 / 文件浏览（低频、耗时操作）</span></h4>
        <span class="fhint">{{ advancedOpen ? '收起' : '展开' }}</span>
      </div>
      <template v-if="advancedOpen">
        <TvMaintenancePanel :library="tab" :active="active" @changed="onOrganized" />
        <FsBrowser :active="active" :media="tabMedia" :video-libs="[tab]" :initial-lib-id="tab.id"
          @changed="onOrganized" @scan="startScan" />
      </template>
    </div>
  </section>
</template>
<script setup>
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { api } from '../api.js'
import { usePolling } from '../usePolling.js'
import { pickOpenStep, sectionNeedsAdvanced, stepForSection } from '../libraryToolsTabs.js'
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
const advancedOpen = ref(false)
const userToggled = ref(false)
const busy = ref(null)
const stats = ref({ shows: 0, episodes: 0 })
const statsReady = ref(false)
const scanMsg = ref('')
const scanStateText = ref('随时可用')
const orgState = ref('')
let scanJobId = ''
const scanPoll = usePolling(pollScanJob, { interval: 1000 })

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
const pendExpand = ref(false)
const pendingTotal = computed(() => pending.value.length)
const unmatchedCount = computed(() => pending.value.filter(it => !it.tmdb_id).length)
const reviewCount = computed(() => pending.value.filter(it => it.tmdb_id && it.needs_review).length)
const episodeReviewCount = computed(() => pending.value
  .filter(it => it.tmdb_id && !it.needs_review)
  .reduce((a, it) => a + (Number(it.episode_review_count) || 0), 0))
const reviewRows = computed(() => pending.value.filter(it => it.needs_review))
const visiblePending = computed(() => pendExpand.value ? pending.value : pending.value.slice(0, COLLAPSE_N))
const pendingSummary = computed(() =>
  `未匹配 ${unmatchedCount.value} · 待确认 ${reviewCount.value} · 未匹配集号 ${episodeReviewCount.value}`)
function rowBadge(it) {
  if (!it.tmdb_id) return '未匹配'
  if (it.needs_review) return '待确认'
  return '集号待处理'
}
async function loadPending(silent) {
  try {
    const d = await api(`/api/tv/shows?library=${props.tab.id}&pending=1&limit=500`)
    pending.value = d.items || []
    pendingLoaded.value = true
  } catch (e) {
    if (!silent) scanMsg.value = '待处理剧加载失败：' + e.message
  }
}
async function confirmOne(id) {
  busy.value = 'confirm'
  try {
    await api(`/api/tv/shows/${id}/confirm-match`, { method: 'POST' })
    pending.value = pending.value.filter(it => it.id !== id)
    emit('changed')
  } catch (e) {
    scanMsg.value = '确认失败：' + e.message
  } finally {
    busy.value = null
  }
}
async function confirmAll() {
  const ids = reviewRows.value.map(it => it.id)
  if (!ids.length) return
  busy.value = 'confirm'
  try {
    for (const id of ids) {
      await api(`/api/tv/shows/${id}/confirm-match`, { method: 'POST' })
    }
    const set = new Set(ids)
    pending.value = pending.value.filter(it => !set.has(it.id))
    emit('changed')
  } catch (e) {
    scanMsg.value = '确认失败：' + e.message
  } finally {
    busy.value = null
  }
}

// ① 扫描入库（本视频库；扫描链式自动刮削新剧）
async function startScan() {
  if (busy.value || !props.tab.enabled) return
  busy.value = 'scan'
  scanMsg.value = ''
  scanStateText.value = '扫描中…'
  try {
    const d = await api('/api/jobs/scan', {
      method: 'POST', body: JSON.stringify({ library_id: props.tab.id })
    })
    scanJobId = d.job_id
    if (d.resumed) scanMsg.value = '已有扫描在跑，跟踪进度…'
    scanPoll.start()
  } catch (e) {
    scanMsg.value = '扫描启动失败：' + e.message
    scanStateText.value = '启动失败'
    busy.value = null
  }
}
function libResultText(c) {
  if (!c) return ''
  if (c.library_offline) return '库离线：跳过（未删除任何记录）'
  const parts = []
  if (c.tv_ok) parts.push(`新增/更新 ${c.tv_ok}`)
  if (c.removed_episode) parts.push(`删除失效集 ${c.removed_episode}`)
  if (c.removed_show) parts.push(`清理空剧 ${c.removed_show}`)
  if (c.skipped_cached) parts.push(`跳过已同步 ${c.skipped_cached}`)
  if (c.skipped_tv_unknown) parts.push(`无法解析集 ${c.skipped_tv_unknown}`)
  if (c.skipped_sidecar) parts.push(`花絮跳过 ${c.skipped_sidecar}`)
  return parts.length ? parts.join('，') : '无变化'
}
async function pollScanJob() {
  if (!scanJobId) return
  try {
    const st = await api('/api/jobs/scan/' + scanJobId)
    if (st.state === 'running') {
      if (st.total) scanMsg.value = `扫描中 ${st.done}/${st.total}…`
      scanStateText.value = st.total ? `扫描中 ${st.done}/${st.total}` : '扫描中…'
      return
    }
    if (st.state === 'done') {
      const sum = st.summary || {}
      const by = sum.by_library || {}
      scanStateText.value = '完成'
      scanMsg.value = '完成：' + (libResultText(by[props.tab.id] || sum.counts) || '无变化')
        + (sum.tv_scrape && sum.tv_scrape.skipped ? '；剧集刮削跳过（已有任务在跑）' : '')
      scanPoll.stop()
      scanJobId = ''
      busy.value = null
      emit('changed')
      await Promise.all([loadStats(), loadPending(true)])
    } else if (st.state === 'cancelled') {
      scanMsg.value = `已取消（${st.done}/${st.total}）`
      scanStateText.value = '已取消'
      scanPoll.stop()
      scanJobId = ''
      busy.value = null
    } else {
      scanMsg.value = '扫描失败：' + (st.error || '未知')
      scanStateText.value = '失败'
      scanPoll.stop()
      scanJobId = ''
      busy.value = null
    }
  } catch (e) { /* 轮询失败下次继续 */ }
}
async function cancelScan() {
  if (!scanJobId) return
  try { await api('/api/jobs/scan/' + scanJobId + '/cancel', { method: 'POST' }) } catch (e) { /* 忽略 */ }
}

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
  if (step) openStep.value = step
  if (sectionNeedsAdvanced(req.sec)) advancedOpen.value = true
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
