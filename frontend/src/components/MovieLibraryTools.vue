<template>
  <section :id="active ? 'sec-pipeline' : undefined" class="card-block">
    <h3>电影库「{{ tab.name }}」 <span class="fhint">单步聚焦：先扫描 → 处理未匹配 → 归档整理；低频工具收在底部</span></h3>
    <div class="status-line">
      <span v-if="pendingTotal" class="chip warn">待处理 {{ pendingTotal }}</span>
      <span v-else-if="pendingLoaded" class="chip ok">✓ 无待匹配</span>
      <span v-if="orgCount" class="chip warn">可归档 {{ orgCount }}</span>
      <span v-if="missing.length" class="chip warn">失效 {{ missing.length }}</span>
      <span v-if="!tab.enabled" class="chip warn">库已停用：扫描/写入被跳过</span>
    </div>

    <div :id="active ? 'sec-sync' : undefined" class="pipe-step">
      <div class="pipe-head pipe-toggle" @click="toggleStep('scan')">
        <h4><span class="step-no">①</span> 扫描入库</h4>
        <span class="fhint">{{ openStep === 'scan' ? '收起' : '展开' }}</span>
        <span class="step-state" :class="{ run: busy === 'scan' }">{{ scanStateText }}</span>
      </div>
      <div v-show="openStep === 'scan'" class="pipe-body">
        <p class="hint">把新影片拷到 NAS / 移动硬盘后点这里：按文件名匹配 TMDB 并入库；
          扫描自动同步删除盘上已没有的记录（软件外删片/改名后重扫也用它）。</p>
        <div class="bar">
          <button class="primary" @click="startScan" :disabled="!!busy || !tab.enabled">
            {{ busy === 'scan' ? '扫描中…' : '扫描本视频库' }}
          </button>
          <button v-if="busy === 'scan'" @click="cancelScan">取消</button>
          <span>{{ scanMsg }}</span>
        </div>
        <div class="bar">
          <button @click="loadMissing()" :disabled="!!busy || missingLoading">{{ missingLoading ? '检查中…' : '检查失效条目' }}</button>
          <button v-if="missing.length" @click="scanOpen = !scanOpen">{{ scanOpen ? '收起清单' : '展开清单' }}</button>
          <span v-if="missing.length">共 {{ missing.length }} 条失效</span>
          <span v-else class="fhint">扫描已自动同步删除，无失效即为正常</span>
          <span>{{ cleanMsg }}</span>
        </div>
        <div v-show="scanOpen && missing.length">
          <div class="lib-group-head">
            <b>失效条目</b>
            <span class="fhint">{{ missing.length }} 条</span>
            <button @click="toggleMissing">{{ allMissingChecked ? '全不选' : '全选' }}</button>
            <button @click="doClean(checkedMissing)" :disabled="!!busy || !checkedMissing.length">
              {{ busy === 'clean' ? '清理中…' : `删除选中 (${checkedMissing.length})` }}
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
      <div class="pipe-head pipe-toggle" @click="toggleStep('pending')">
        <h4><span class="step-no">②</span> 待匹配确认 <span v-if="pendingTotal" class="nav-badge">{{ pendingTotal }}</span></h4>
        <span class="fhint">{{ openStep === 'pending' ? '收起' : '展开' }}</span>
        <span class="step-state" :class="{ ok: !pendingTotal && pendingLoaded }">{{ pendingTotal ? `${pendingTotal} 项待处理` : (pendingLoaded ? '✓ 全部已匹配' : '') }}</span>
      </div>
      <div v-show="openStep === 'pending'" class="pipe-body">
        <p class="hint">扫描/上传后没认出来的片在这里核对。未匹配：TMDB 没找到数据；待确认：模糊命中需人工核对；
          疑似英文标题：非英语片却显示英文（错配或缺翻译）；未归属花絮：对不上任何影片。点「去处理」到详情页手动绑定。</p>
        <div class="bar">
          <button @click="loadUnmatched()" :disabled="!!busy">刷新</button>
          <span v-if="pendingTotal">{{ pendingSummary }}</span>
          <span v-else>全部已匹配</span>
          <button v-if="needsReview.length > 1" :disabled="!!busy" title="待确认一次性清除" @click="confirmReview(needsReview.map(m => m.id))">
            全部确认（{{ needsReview.length }}）
          </button>
        </div>
        <ul class="miss-list">
          <li v-for="it in visiblePending" :key="it.category + '-' + it.id" class="miss-row">
            <span class="kind-badge" :class="{ bad: it.category === 'unmatched' }">{{ it.categoryLabel }}</span>
            <span class="miss-title">{{ it.title || (it.category === 'orphan' ? (it.kind || '花絮') : '(未命名)') }}<span v-if="it.year"> ({{ it.year }})</span></span>
            <span class="miss-path">{{ it.file_path }}<span v-if="it.guessed_title" class="fhint">（猜测：{{ it.guessed_title }}{{ it.guessed_year ? ' ' + it.guessed_year : '' }}）</span></span>
            <template v-if="it.category === 'orphan'">
              <input v-model="orphanMovie[it.id]" placeholder="影片ID" style="width:80px" />
              <button @click="attachOrphan(it.id)" :disabled="!!busy">认领</button>
            </template>
            <template v-else>
              <button v-if="it.category === 'needs_review'" :disabled="!!busy" title="匹配无误，清除待确认" @click="confirmReview([it.id])">确认</button>
              <button @click="$router.push('/m/' + it.id)">去处理</button>
            </template>
          </li>
          <li v-if="pendingRows.length > COLLAPSE_N" class="miss-row collapse-row">
            <button @click="pendExpand = !pendExpand">{{ pendExpand ? '收起' : `展开全部 (${pendingRows.length})` }}</button>
          </li>
        </ul>
        <div class="bar">
          <button @click="doCollectExtras" :disabled="!!busy">{{ busy === 'collect' ? '归位中…' : (armCollect ? '确认归位花絮' : '归位已归属花絮到各片 extras/') }}</button>
          <span>{{ collectMsg }}</span>
        </div>
      </div>
    </div>

    <OrganizePanel ref="organizeRef" :library="tab" :active="active" @changed="onOrganized" />

    <div class="more-tools">
      <div class="pipe-head pipe-toggle" @click="advancedOpen = !advancedOpen">
        <h4>更多工具 <span class="fhint">高级维护 / 恢复到原始位置 / 文件浏览（低频、破坏性操作）</span></h4>
        <span class="fhint">{{ advancedOpen ? '收起' : '展开' }}</span>
      </div>
      <template v-if="advancedOpen">
        <LibraryMaintenancePanel :library="tab" :active="active" @changed="onChanged" />
        <RestorePanel ref="restoreRef" :library="tab" :active="active" :preselect-ids="preselectIds" @count="restoreCount = $event" @changed="onChanged" />
        <FsBrowser :active="active" :media="tabMedia" :video-libs="[tab]" :initial-lib-id="tab.id"
          @changed="onChanged" @scan="startScan" />
      </template>
    </div>
  </section>
</template>
<script setup>
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { api } from '../api.js'
import { usePolling } from '../usePolling.js'
import { pickOpenStep, sectionNeedsAdvanced, stepForSection } from '../libraryToolsTabs.js'
import OrganizePanel from './OrganizePanel.vue'
import LibraryMaintenancePanel from './LibraryMaintenancePanel.vue'
import RestorePanel from './RestorePanel.vue'
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
const preselectIds = ref([])
const restoreCount = ref(0)
const userToggled = ref(false)

function toggleStep(key) {
  userToggled.value = true
  openStep.value = openStep.value === key ? '' : key
}

// ① 扫描入库（本视频库；后台任务轮询，可取消）
const busy = ref(null)
const scanMsg = ref('')
const scanStateText = ref('随时可用')
let scanJobId = ''
const scanPoll = usePolling(pollScanJob, { interval: 1000 })
function finishScanJob() {
  scanPoll.stop()
  scanJobId = ''
  busy.value = null
}
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
  const ok = (c.ok || 0) + (c.ok_needs_review || 0)
  if (ok) parts.push(`新增/更新 ${ok}`)
  if (c.removed_movie) parts.push(`删除失效 ${c.removed_movie}`)
  if (c.skipped_cached) parts.push(`跳过已同步 ${c.skipped_cached}`)
  if (c.no_match) parts.push(`未匹配 ${c.no_match}`)
  if (c.scan_failed) parts.push(`刮削失败 ${c.scan_failed}`)
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
      const c = sum.counts || {}
      const by = sum.by_library || {}
      const mine = by[props.tab.id] || c
      scanStateText.value = '完成'
      scanMsg.value = '完成：' + libResultText(mine)
      finishScanJob()
      emit('changed')
      await Promise.all([loadMissing(true), loadUnmatched(true)])
      organizeRef.value?.refresh(true)
    } else if (st.state === 'cancelled') {
      scanMsg.value = `已取消（${st.done}/${st.total}）`
      scanStateText.value = '已取消'
      finishScanJob()
    } else {
      scanMsg.value = '扫描失败：' + (st.error || '未知')
      scanStateText.value = '失败'
      finishScanJob()
    }
  } catch (e) { /* 轮询失败下次继续 */ }
}
async function cancelScan() {
  if (!scanJobId) return
  try { await api('/api/jobs/scan/' + scanJobId + '/cancel', { method: 'POST' }) } catch (e) { /* 忽略 */ }
}

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

// ② 待匹配确认（单库；类别混排，行上带徽章）
const unmatched = ref([])
const needsReview = ref([])
const suspectHigh = ref([])
const suspectInfo = ref([])
const orphans = ref([])
const orphanMovie = ref({})
const pendingLoaded = ref(false)
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
const pendingSummary = computed(() =>
  `未匹配 ${unmatched.value.length} · 待确认 ${needsReview.value.length} · 疑似英文 ${suspectHigh.value.length + suspectInfo.value.length} · 未归属花絮 ${orphans.value.length}`)
async function loadUnmatched(silent) {
  try {
    const d = await api('/api/files/unmatched?library=' + props.tab.id)
    unmatched.value = d.unmatched || []
    needsReview.value = d.needs_review || []
    suspectHigh.value = d.suspect_title_high || []
    suspectInfo.value = d.suspect_title_info || []
    orphans.value = d.orphan_extras || []
    pendingLoaded.value = true
  } catch (e) {
    if (!silent) scanMsg.value = '待处理加载失败：' + e.message
  }
}
async function confirmReview(ids) {
  const list = (ids || []).slice(0, 500)
  if (!list.length) return
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
    scanMsg.value = '确认失败：' + e.message
  } finally {
    busy.value = null
  }
}
async function attachOrphan(id) {
  const mid = Number((orphanMovie.value[id] || '').toString().trim())
  if (!mid) return
  busy.value = 'attach'
  try {
    await api('/api/extras/' + id + '/attach', { method: 'POST', body: JSON.stringify({ movie_id: mid }) })
    orphans.value = orphans.value.filter(e => e.id !== id)
    emit('changed')
  } catch (e) {
    scanMsg.value = '认领失败：' + e.message
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
  const step = stepForSection(req.sec)
  if (step) openStep.value = step
  if (sectionNeedsAdvanced(req.sec) || (req.ids || []).length) advancedOpen.value = true
  if ((req.ids || []).length) preselectIds.value = req.ids
  await nextTick()
  if (req.sec) document.getElementById(req.sec)?.scrollIntoView({ block: 'start' })
}, { immediate: true })

// 首次激活加载（每个 Tab 一个实例，懒加载）
const loaded = ref(false)
async function ensure() {
  if (loaded.value) return
  loaded.value = true
  await Promise.all([loadMissing(true), loadUnmatched(true), organizeRef.value?.ensure(true)])
  orgCount.value = organizeRef.value?.count() || 0
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
