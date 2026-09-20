<template>
  <section :id="active ? 'sec-pipeline' : undefined" class="card-block">
    <h3>入库流程 <span class="fhint">作用于「{{ media.name }}」整个媒体库</span></h3>
    <p class="hint">① 扫描整个媒体库（电影按文件名刮削入库；剧集按目录结构入清单，不刮削）→
      <template v-if="movieLibs.length">② 处理未匹配/待确认 → ③ 归档整理。</template>
      列表按视频库分表；库级批量修复见下方「高级维护」。</p>

    <div :id="active ? 'sec-sync' : undefined" class="pipe-step">
      <div class="pipe-head">
        <h4>① 扫描入库</h4>
        <span class="fhint">NAS 直拷 / 软件外删片后用：扫描整个媒体库，再检查并清理失效条目</span>
      </div>
      <div class="pipe-body">
        <div class="bar">
          <button @click="startScan" :disabled="!!busy || media.enabled === false">
            {{ busy === 'scan' ? '扫描中…' : '扫描新文件（整个媒体库）' }}
          </button>
          <button v-if="busy === 'scan'" @click="cancelScan">取消</button>
          <span>{{ scanMsg }}</span>
        </div>
        <table v-if="scanLibRows.length" class="lib-table">
          <thead><tr><th>视频库</th><th>本次结果</th></tr></thead>
          <tbody>
            <tr v-for="r in visibleScanRows" :key="'sc' + r.lib_id">
              <td class="nowrap">{{ r.name }}<span class="tab-kind">{{ kindText(r.kind) }}</span></td>
              <td>{{ r.text }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="scanLibRows.length > COLLAPSE_N" class="bar">
          <button @click="showAllScan = !showAllScan">{{ showAllScan ? '收起' : `展开全部 (${scanLibRows.length})` }}</button>
        </div>
        <div class="bar">
          <button @click="loadMissing()" :disabled="!!busy || missingLoading">{{ missingLoading ? '检查中…' : '检查失效条目' }}</button>
          <button v-if="missing.length" @click="scanOpen = !scanOpen">{{ scanOpen ? '收起清单' : '展开清单' }}</button>
          <span v-if="missing.length">共 {{ missing.length }} 条失效</span>
          <span>{{ cleanMsg }}</span>
        </div>
        <div v-show="scanOpen && missing.length">
          <div v-for="g in missingGroups" :key="'mg' + g.key" class="lib-group">
            <div class="lib-group-head">
              <b>{{ groupTitle(g) }}</b>
              <span class="fhint">{{ g.items.length }} 条</span>
              <button @click="toggleMissing(g)">{{ groupAllChecked(g) ? '全不选本表' : '全选本表' }}</button>
              <button @click="doClean(groupCheckedIds(g))" :disabled="!!busy || !groupCheckedIds(g).length">
                删除本表选中 ({{ groupCheckedIds(g).length }})
              </button>
            </div>
            <ul class="miss-list">
              <li v-for="m in seeMore(missExpand, g)" :key="m.id" class="miss-row">
                <input type="checkbox" :value="m.id" v-model="checkedMissing" />
                <span class="miss-title">{{ m.title || '(未命名)' }}<span v-if="m.year"> ({{ m.year }})</span></span>
                <span class="miss-path">{{ m.file_path }}</span>
              </li>
              <li v-if="g.items.length > COLLAPSE_N" class="miss-row collapse-row">
                <button @click="toggleExpand(missExpand, g.key)">
                  {{ expanded(missExpand, g.key) ? '收起' : `展开全部 (${g.items.length})` }}
                </button>
              </li>
            </ul>
          </div>
          <div class="bar">
            <button @click="doClean(checkedMissing)" :disabled="!!busy || !checkedMissing.length">
              {{ busy === 'clean' ? '清理中…' : `删除全部选中 (${checkedMissing.length})` }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="movieLibs.length" :id="active ? 'sec-pending' : undefined" class="pipe-step">
      <div class="pipe-head pipe-toggle" @click="pendingOpen = !pendingOpen">
        <h4>② 待匹配确认 <span v-if="pendingTotal" class="nav-badge">{{ pendingTotal }}</span></h4>
        <span class="fhint">{{ pendingOpen ? '收起' : '展开' }}</span>
      </div>
      <div v-show="pendingOpen" class="pipe-body">
        <p class="hint">扫描/上传后没认出来的片在这里核对。未匹配：TMDB 没找到数据；待确认：模糊命中需人工核对；
          疑似英文标题：非英语片却显示英文（错配或缺翻译）；未归属花絮：对不上任何影片。点「去处理」到详情页手动绑定。</p>
        <div class="bar">
          <button @click="loadUnmatched()" :disabled="!!busy">刷新</button>
          <span v-if="pendingTotal">{{ pendingSummary }}</span>
          <span v-else>全部已匹配</span>
          <button v-if="needsReview.length > 1" :disabled="!!busy" title="所有视频库的待确认一次性清除" @click="confirmReview(needsReview.map(m => m.id))">
            全部确认（{{ needsReview.length }}）
          </button>
        </div>
        <div v-for="g in pendingGroups" :key="'pg' + g.key" class="lib-group">
          <div class="lib-group-head">
            <b>{{ groupTitle(g) }}</b>
            <span class="fhint">{{ g.items.length }} 条</span>
            <button v-if="groupNeedsReview(g).length" :disabled="!!busy" @click="confirmReview(groupNeedsReview(g).map(m => m.id))">
              全部确认（{{ groupNeedsReview(g).length }}）
            </button>
          </div>
          <ul class="miss-list">
            <li v-for="it in seeMore(pendExpand, g)" :key="it.category + '-' + it.id" class="miss-row">
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
            <li v-if="g.items.length > COLLAPSE_N" class="miss-row collapse-row">
              <button @click="toggleExpand(pendExpand, g.key)">
                {{ expanded(pendExpand, g.key) ? '收起' : `展开全部 (${g.items.length})` }}
              </button>
            </li>
          </ul>
        </div>
        <div class="bar">
          <button @click="doCollectExtras" :disabled="!!busy">{{ busy === 'collect' ? '归位中…' : (armCollect ? '确认归位花絮' : '归位已归属花絮到各片 extras/') }}</button>
          <span>{{ collectMsg }}</span>
        </div>
      </div>
    </div>

    <OrganizePanel v-if="movieLibs.length" ref="organizeRef" :media="media" :lib-filter="libFilter"
      :active="active" @changed="onOrganized" />
  </section>
</template>
<script setup>
import { ref, computed, reactive, watch } from 'vue'
import { api } from '../api.js'
import { usePolling } from '../usePolling.js'
import OrganizePanel from './OrganizePanel.vue'
import { groupByVideoLib, kindText } from '../libraryToolGroups.js'

const COLLAPSE_N = 20
const props = defineProps({
  media: { type: Object, required: true },
  libFilter: { type: Number, default: null },
  active: { type: Boolean, default: false },
})
const emit = defineEmits(['changed', 'organized'])

const movieLibs = computed(() => (props.media.video_libraries || [])
  .filter(v => (v.kind || 'movie') !== 'tv'))
// 展示作用域：数据按整个媒体库加载，筛选仅影响分组展示（切筛选不重拉）
const scopeLibs = computed(() => {
  const libs = props.media.video_libraries || []
  if (props.libFilter == null) return libs
  return libs.filter(l => Number(l.id) === Number(props.libFilter))
})
const mediaIdsCsv = computed(() => (props.media.video_libraries || [])
  .map(l => Number(l.id)).filter(Number.isFinite).join(','))

const busy = ref(null)
const scanMsg = ref('')
const cleanMsg = ref('')
const collectMsg = ref('')
const scanLibRows = ref([])
const showAllScan = ref(false)
const visibleScanRows = computed(() => showAllScan.value
  ? scanLibRows.value : scanLibRows.value.slice(0, COLLAPSE_N))
const scanOpen = ref(true)
const missExpand = reactive({})
const pendExpand = reactive({})

function expanded(map, key) { return !!map[key] }
function toggleExpand(map, key) { map[key] = !map[key] }
function seeMore(map, g) {
  return (expanded(map, g.key) ? g.items : g.items.slice(0, COLLAPSE_N))
}
function groupTitle(g) {
  return g.lib ? `${g.lib.name} · ${kindText(g.lib.kind)}` : '未识别库'
}

// ① 扫描入库（后台任务轮询，可取消）
let scanJobId = ''
const scanPoll = usePolling(pollScanJob, { interval: 1000 })
function finishScanJob() {
  scanPoll.stop()
  scanJobId = ''
  busy.value = null
}
async function startScan() {
  if (busy.value || props.media.enabled === false) return
  busy.value = 'scan'
  scanMsg.value = ''
  scanLibRows.value = []
  try {
    const d = await api('/api/jobs/scan', {
      method: 'POST', body: JSON.stringify({ media_library_id: props.media.id })
    })
    scanJobId = d.job_id
    if (d.resumed) scanMsg.value = '已有扫描在跑，跟踪进度…'
    scanPoll.start()
  } catch (e) {
    scanMsg.value = '扫描启动失败：' + e.message
    busy.value = null
  }
}
// 单库结果文案：与后端 status 词表对应
function libResultText(c) {
  const parts = []
  const ok = (c.ok || 0) + (c.ok_needs_review || 0)
  if (c.library_offline) return '库离线：跳过（未删除任何记录）'
  if (ok) parts.push(`新增/更新 ${ok}`)
  if (c.skipped_cached) parts.push(`跳过已同步 ${c.skipped_cached}`)
  if (c.no_match) parts.push(`未匹配 ${c.no_match}`)
  if (c.scan_failed) parts.push(`刮削失败 ${c.scan_failed}`)
  if (c.skipped_episode_v1) parts.push(`剧集跳过 ${c.skipped_episode_v1}`)
  if (c.skipped_tv_unknown) parts.push(`无法解析集 ${c.skipped_tv_unknown}`)
  if (c.skipped_sidecar) parts.push(`花絮跳过 ${c.skipped_sidecar}`)
  return parts.length ? parts.join('，') : '无变化'
}
function syncScanRows(sum) {
  const by = (sum && sum.by_library) || {}
  scanLibRows.value = (props.media.video_libraries || []).map(l => {
    const c = by[l.id]
    if (!c) return null
    return { lib_id: l.id, name: l.name, kind: l.kind, text: libResultText(c) }
  }).filter(Boolean)
}
async function pollScanJob() {
  if (!scanJobId) return
  try {
    const st = await api('/api/jobs/scan/' + scanJobId)
    if (st.state === 'running') {
      if (st.total) scanMsg.value = `扫描中 ${st.done}/${st.total}…`
      return
    }
    if (st.state === 'done') {
      const sum = st.summary || {}
      const c = sum.counts || {}
      syncScanRows(sum)
      if (scanLibRows.value.length) {
        const ok = (c.ok || 0) + (c.ok_needs_review || 0)
        scanMsg.value = `完成：新增/更新 ${ok}` + (c.library_offline ? '；有库离线已跳过' : '') + '。见下方各视频库结果'
      } else {
        const ok = (c.ok || 0) + (c.ok_needs_review || 0)
        scanMsg.value = c.library_offline
          ? `库离线：跳过扫描（${c.library_offline} 项），未删除任何记录`
          : `完成：新增/更新 ${ok}，已同步跳过 ${c.skipped_cached || 0}，未匹配 ${c.no_match || 0}`
            + (c.scan_failed ? `，刮削失败 ${c.scan_failed}（可重试）` : '')
            + ((sum.errors || []).length ? `，失败 ${sum.errors.length}` : '')
      }
    } else if (st.state === 'cancelled') {
      scanMsg.value = `已取消（${st.done}/${st.total}）`
    } else {
      scanMsg.value = '扫描失败：' + (st.error || '未知')
    }
    finishScanJob()
    emit('changed')
    ensureMissing(true)
    ensureUnmatched(true)
    // 扫描入库后主动提示归档：有可归档项就展开 ③ 并说明下一步
    organizeRef.value?.refresh(true)
  } catch (e) { /* 轮询失败下次继续 */ }
}
async function cancelScan() {
  if (!scanJobId) return
  try { await api('/api/jobs/scan/' + scanJobId + '/cancel', { method: 'POST' }) } catch (e) { /* 忽略 */ }
}

// ① 失效条目（按视频库分表）
const missing = ref([])
const checkedMissing = ref([])
const missingLoading = ref(false)
const missingGroups = computed(() => groupByVideoLib(missing.value, scopeLibs.value)
  .map(g => ({ ...g, key: String(g.library_id) })))
function groupCheckedIds(g) {
  const ids = new Set(g.items.map(it => it.id))
  return checkedMissing.value.filter(id => ids.has(id))
}
function groupAllChecked(g) {
  const ids = groupCheckedIds(g)
  return g.items.length > 0 && ids.length === g.items.length
}
function toggleMissing(g) {
  const ids = g.items.map(it => it.id)
  if (groupAllChecked(g)) checkedMissing.value = checkedMissing.value.filter(id => !ids.includes(id))
  else checkedMissing.value = [...new Set([...checkedMissing.value, ...ids])]
}
async function loadMissing(silent) {
  if (missingLoading.value) return
  if (!mediaIdsCsv.value) { missing.value = []; return }
  missingLoading.value = true
  if (!silent) cleanMsg.value = ''
  try {
    const d = await api('/api/files/missing?library=' + mediaIdsCsv.value)
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
      body: JSON.stringify({ ids: list, dry_run: false, library_id: mediaIdsCsv.value })
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

// ② 待匹配确认（按视频库分表；同一表内混排类别，行上带类别徽章）
const pendingOpen = ref(false)
const pendingAuto = ref(false)
const unmatched = ref([])
const needsReview = ref([])
const suspectHigh = ref([])
const suspectInfo = ref([])
const orphans = ref([])
const orphanMovie = ref({})
async function loadUnmatched(silent) {
  if (!mediaIdsCsv.value) {
    unmatched.value = []; needsReview.value = []; suspectHigh.value = []
    suspectInfo.value = []; orphans.value = []
    return
  }
  try {
    const d = await api('/api/files/unmatched?library=' + mediaIdsCsv.value)
    unmatched.value = d.unmatched || []
    needsReview.value = d.needs_review || []
    suspectHigh.value = d.suspect_title_high || []
    suspectInfo.value = d.suspect_title_info || []
    orphans.value = d.orphan_extras || []
  } catch (e) {
    if (!silent) scanMsg.value = '待处理加载失败：' + e.message
  }
}
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
const pendingGroups = computed(() => groupByVideoLib(pendingRows.value, scopeLibs.value)
  .map(g => ({ ...g, key: String(g.library_id) })))
const pendingTotal = computed(() => pendingRows.value.length)
const pendingSummary = computed(() =>
  `未匹配 ${unmatched.value.length} · 待确认 ${needsReview.value.length} · 疑似英文 ${suspectHigh.value.length + suspectInfo.value.length} · 未归属花絮 ${orphans.value.length}`)
function groupNeedsReview(g) {
  return g.items.filter(it => it.category === 'needs_review')
}
// 展开规则：有内容首次到达时自动展开（手动收起后不再抢回）
watch(pendingTotal, (n) => {
  if (n > 0 && !pendingAuto.value) {
    pendingAuto.value = true
    pendingOpen.value = true
  }
})
function ensureMissing(silent) { return loadMissing(silent) }
function ensureUnmatched(silent) { return loadUnmatched(silent) }
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
    const body = JSON.stringify({ dry_run: true, media_library_id: props.media.id })
    const prev = await api('/api/extras/collect', { method: 'POST', body })
    if (!prev.total) {
      collectMsg.value = '没有待归位花絮'
      return
    }
    const d = await api('/api/extras/collect', {
      method: 'POST', body: JSON.stringify({ dry_run: false, media_library_id: props.media.id })
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

// ③ 归档整理：路径变化后失效清单要刷新，恢复面板由父级刷新
const organizeRef = ref(null)
async function onOrganized() {
  await loadMissing(true)
  emit('organized')
}

// 父级（媒体库标签页）选中本库时首次加载重清单
const loaded = ref(false)
async function ensure() {
  if (loaded.value) return
  loaded.value = true
  const jobs = [loadMissing(true)]
  if (movieLibs.value.length) jobs.push(loadUnmatched(true), organizeRef.value?.ensure(true))
  await Promise.all(jobs)
}
defineExpose({ ensure, startScan, loadMissing, loadUnmatched })
</script>
<style scoped>
.card-block { background: #1c1c1c; border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }
.card-block h3 { margin: 0 0 10px; font-size: 1.0625rem; color: #ddd; }
.pipe-step { margin: 12px 0 0; border-top: 1px dashed #3a3a3a; padding-top: 10px; }
.pipe-head { display: flex; gap: 8px; align-items: baseline; }
.pipe-head h4 { margin: 0; font-size: 0.9375rem; color: #ccc; }
.pipe-toggle { cursor: pointer; user-select: none; }
.pipe-toggle:hover h4 { color: #fff; }
.pipe-body { margin-top: 6px; }
.hint { color: #777; font-size: 0.8125rem; margin: 0 0 4px; }
.fhint { font-size: 0.75rem; color: #888; font-weight: normal; }
.nav-badge { margin-left: 6px; font-size: 0.75rem; color: #e0a63c; }
.lib-table { width: 100%; border-collapse: collapse; font-size: 0.8125rem; margin: 4px 0; }
.lib-table th, .lib-table td { text-align: left; padding: 4px 8px; border-bottom: 1px solid #333; vertical-align: top; }
.lib-table th { color: #888; font-weight: normal; font-size: 0.75rem; }
.nowrap { white-space: nowrap; }
.tab-kind { margin-left: 6px; font-size: 0.6875rem; color: #888; border: 1px solid #444; border-radius: 999px; padding: 0 6px; }
.lib-group { margin: 8px 0; }
.lib-group-head { display: flex; gap: 8px; align-items: center; font-size: 0.8125rem; color: #ccc; margin-bottom: 4px; }
.miss-list { list-style: none; margin: 4px 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.miss-row { display: flex; gap: 8px; align-items: center; background: #262626; border: 1px solid #3a3a3a; border-radius: 8px; padding: 6px 10px; font-size: 0.8125rem; }
.miss-title { white-space: nowrap; }
.miss-path { color: #888; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.kind-badge { font-size: 0.75rem; border: 1px solid #444; border-radius: 999px; padding: 1px 10px; color: #aaa; white-space: nowrap; }
.kind-badge.bad { color: #ff8a8a; border-color: #6e2b2b; }
.collapse-row { background: transparent; border: none; padding: 0; }
</style>
