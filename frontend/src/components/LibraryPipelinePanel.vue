<template>
  <section :id="active ? 'sec-pipeline' : undefined" class="card-block">
    <h3>入库流程 <span class="fhint">仅作用于「{{ library.name }}」</span></h3>
    <p class="hint">按顺序走：① 扫描新增 → <template v-if="isMovie">② 解决未匹配/待确认 → ③ 归档到正式库。</template>库级批量修复见下方「高级维护」。</p>

    <div :id="active ? 'sec-sync' : undefined" class="pipe-step">
      <div class="pipe-head">
        <h4>① 扫描入库</h4>
        <span class="fhint">NAS 直拷 / 软件外删片后用：先扫描新增，再检查并清理失效条目</span>
      </div>
      <div class="pipe-body">
        <div class="bar">
          <button @click="doScan" :disabled="!!busy || !library.enabled">
            {{ busy === 'scan' ? '扫描中…' : (library.kind === 'tv' ? '扫描剧集' : '扫描新文件') }}
          </button>
          <button v-if="busy === 'scan'" @click="cancelScan">取消</button>
          <span>{{ scanMsg }}</span>
        </div>
        <div class="bar">
          <button @click="loadMissing()" :disabled="!!busy || missingLoading">{{ missingLoading ? '检查中…' : '检查失效条目' }}</button>
          <button v-if="missing.length" @click="toggleAllMissing">{{ allChecked ? '全不选' : '全选' }}</button>
          <span v-if="missing.length">共 {{ missing.length }} 条失效</span>
          <span>{{ cleanMsg }}</span>
          <button v-if="missing.length > COLLAPSE_N" @click="showAllMissing = !showAllMissing">{{ showAllMissing ? '收起' : `展开全部 (${missing.length})` }}</button>
        </div>
        <ul v-if="missing.length" class="miss-list">
          <li v-for="m in visibleMissing" :key="m.id" class="miss-row">
            <input type="checkbox" :value="m.id" v-model="checkedMissing" />
            <span class="miss-title">{{ m.title || '(未命名)' }}<span v-if="m.year"> ({{ m.year }})</span></span>
            <span class="miss-path">{{ m.file_path }}</span>
          </li>
        </ul>
        <div v-if="missing.length" class="bar">
          <button @click="doClean" :disabled="!!busy || !checkedMissing.length">
            {{ busy === 'clean' ? '清理中…' : `删除选中 (${checkedMissing.length})` }}
          </button>
        </div>
      </div>
    </div>

    <div v-if="isMovie" :id="active ? 'sec-pending' : undefined" class="pipe-step">
      <div class="pipe-head pipe-toggle" @click="pendingOpen = !pendingOpen">
        <h4>② 待匹配确认 <span v-if="pendingTotal" class="nav-badge">{{ pendingTotal }}</span></h4>
        <span class="fhint">{{ pendingOpen ? '收起' : '展开' }}</span>
      </div>
      <div v-show="pendingOpen" class="pipe-body">
        <p class="hint">扫描/上传后没认出来的片在这里核对。未匹配：TMDB 没找到数据；待确认：模糊命中需人工核对；疑似英文标题：非英语片却显示英文（错配或缺翻译）；未归属花絮：对不上任何影片。点「去处理」到详情页手动绑定。</p>
        <div class="bar">
          <button @click="loadUnmatched()" :disabled="!!busy">刷新</button>
          <span v-if="pendingTotal">未匹配 {{ unmatched.length }} · 待确认 {{ needsReview.length }} · 疑似英文 {{ suspectHigh.length + suspectInfo.length }} · 未归属花絮 {{ orphans.length }}</span>
          <span v-else>全部已匹配</span>
        </div>
        <h4 v-if="unmatched.length" class="sub-h">未匹配（{{ unmatched.length }}）<button v-if="unmatched.length > COLLAPSE_N" @click="showAllUnmatched = !showAllUnmatched">{{ showAllUnmatched ? '收起' : '展开全部' }}</button></h4>
        <ul v-if="unmatched.length" class="miss-list">
          <li v-for="m in visibleUnmatched" :key="'u' + m.id" class="miss-row">
            <span class="miss-title">{{ m.title || '(未命名)' }}<span v-if="m.year"> ({{ m.year }})</span></span>
            <span class="miss-path">{{ m.file_path }}</span>
            <button @click="$router.push('/m/' + m.id)">去处理</button>
          </li>
        </ul>
        <h4 v-if="needsReview.length" class="sub-h">待确认（{{ needsReview.length }}）<button v-if="needsReview.length > COLLAPSE_N" @click="showAllNeedsReview = !showAllNeedsReview">{{ showAllNeedsReview ? '收起' : '展开全部' }}</button><button :disabled="!!busy" title="匹配核对无误时一键清除待确认标记（不重刮；同片多版本一起确认）" @click="confirmAllNeedsReview">全部确认</button></h4>
        <ul v-if="needsReview.length" class="miss-list">
          <li v-for="m in visibleNeedsReview" :key="'n' + m.id" class="miss-row">
            <span class="miss-title">{{ m.title || '(未命名)' }}<span v-if="m.year"> ({{ m.year }})</span></span>
            <span class="miss-path">{{ m.file_path }}</span>
            <button :disabled="!!busy" title="匹配无误，清除待确认" @click="confirmReview([m.id])">确认</button>
            <button @click="$router.push('/m/' + m.id)">去处理</button>
          </li>
        </ul>
        <h4 v-if="suspectHigh.length" class="sub-h">疑似英文标题·重点看（{{ suspectHigh.length }}，非英语片却显示英文）<button v-if="suspectHigh.length > COLLAPSE_N" @click="showAllSuspectHigh = !showAllSuspectHigh">{{ showAllSuspectHigh ? '收起' : '展开全部' }}</button></h4>
        <ul v-if="suspectHigh.length" class="miss-list">
          <li v-for="m in visibleSuspectHigh" :key="'sh' + m.id" class="miss-row">
            <span class="miss-title">{{ m.title || '(未命名)' }}<span v-if="m.year"> ({{ m.year }})</span></span>
            <span class="miss-path">{{ m.file_path }}</span>
            <button @click="$router.push('/m/' + m.id)">去处理</button>
          </li>
        </ul>
        <h4 v-if="suspectInfo.length" class="sub-h">英文标题·信息（{{ suspectInfo.length }}，英语片多为正常）<button v-if="suspectInfo.length > COLLAPSE_N" @click="showAllSuspectInfo = !showAllSuspectInfo">{{ showAllSuspectInfo ? '收起' : '展开全部' }}</button></h4>
        <ul v-if="suspectInfo.length" class="miss-list">
          <li v-for="m in visibleSuspectInfo" :key="'si' + m.id" class="miss-row">
            <span class="miss-title">{{ m.title || '(未命名)' }}<span v-if="m.year"> ({{ m.year }})</span></span>
            <span class="miss-path">{{ m.file_path }}</span>
            <button @click="$router.push('/m/' + m.id)">去处理</button>
          </li>
        </ul>
        <h4 v-if="orphans.length" class="sub-h">未归属花絮（{{ orphans.length }}，文件原地保留，填影片ID认领）<button v-if="orphans.length > COLLAPSE_N" @click="showAllOrphans = !showAllOrphans">{{ showAllOrphans ? '收起' : '展开全部' }}</button></h4>
        <ul v-if="orphans.length" class="miss-list">
          <li v-for="e in visibleOrphans" :key="'o' + e.id" class="miss-row">
            <span class="miss-title">{{ e.kind }}</span>
            <span class="miss-path">{{ e.file_path }}<span v-if="e.guessed_title" class="fhint">（猜测：{{ e.guessed_title }}{{ e.guessed_year ? ' ' + e.guessed_year : '' }}）</span></span>
            <input v-model="orphanMovie[e.id]" placeholder="影片ID" style="width:80px" />
            <button @click="attachOrphan(e.id)" :disabled="!!busy">认领</button>
          </li>
        </ul>
        <div class="bar">
          <button @click="doCollectExtras" :disabled="!!busy">{{ busy === 'collect' ? '归位中…' : (armCollect ? '确认归位花絮' : '归位已归属花絮到各片 extras/') }}</button>
          <span>{{ collectMsg }}</span>
        </div>
      </div>
    </div>

    <OrganizePanel v-if="isMovie" ref="organizeRef" :library="library" :active="active" @changed="onOrganized" />
  </section>
</template>
<script setup>
import { ref, computed } from 'vue'
import { api } from '../api.js'
import { usePolling } from '../usePolling.js'
import OrganizePanel from './OrganizePanel.vue'

const COLLAPSE_N = 20
const props = defineProps({
  library: { type: Object, required: true },
  active: { type: Boolean, default: false },
})
const emit = defineEmits(['changed', 'organized'])

const isMovie = computed(() => (props.library.kind || 'movie') !== 'tv')

const busy = ref(null)
const scanMsg = ref('')
const cleanMsg = ref('')
const collectMsg = ref('')

// ① 扫描入库（后台任务轮询，可取消）
let scanJobId = ''
const scanPoll = usePolling(pollScanJob, { interval: 1000 })
function finishScanJob() {
  scanPoll.stop()
  scanJobId = ''
  busy.value = null
}
async function startScan() {
  if (busy.value || !props.library.enabled) return
  busy.value = 'scan'
  scanMsg.value = ''
  try {
    const d = await api('/api/jobs/scan', {
      method: 'POST', body: JSON.stringify({ library_id: props.library.id })
    })
    scanJobId = d.job_id
    if (d.resumed) scanMsg.value = '已有扫描在跑，跟踪进度…'
    scanPoll.start()
  } catch (e) {
    scanMsg.value = '扫描启动失败：' + e.message
    busy.value = null
  }
}
const doScan = startScan
async function pollScanJob() {
  if (!scanJobId) return
  try {
    const st = await api('/api/jobs/scan/' + scanJobId)
    if (st.state === 'running') {
      if (st.total) scanMsg.value = `刮削中 ${st.done}/${st.total}…`
      return
    }
    if (st.state === 'done') {
      const sum = st.summary || {}
      const c = sum.counts || {}
      const ok = (c.ok || 0) + (c.ok_needs_review || 0)
      if (c.library_offline) {
        scanMsg.value = `库离线：跳过扫描（${c.library_offline} 项），未删除任何记录`
      } else {
        scanMsg.value = `完成：新增/更新 ${ok}，已同步跳过 ${c.skipped_cached || 0}，未匹配 ${c.no_match || 0}`
          + (c.scan_failed ? `，刮削失败 ${c.scan_failed}（可重试）` : '')
          + (c.skipped_episode_v1 ? `，剧集跳过 ${c.skipped_episode_v1}` : '')
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

// ① 失效条目
const missing = ref([])
const checkedMissing = ref([])
const missingLoading = ref(false)
const showAllMissing = ref(false)
const allChecked = computed(() => missing.value.length > 0 && checkedMissing.value.length === missing.value.length)
const visibleMissing = computed(() => showAllMissing.value ? missing.value : missing.value.slice(0, COLLAPSE_N))
async function loadMissing(silent) {
  if (missingLoading.value) return
  missingLoading.value = true
  if (!silent) cleanMsg.value = ''
  try {
    const d = await api('/api/files/missing?library=' + props.library.id)
    missing.value = d.items
    checkedMissing.value = d.items.map(m => m.id)
    if (!silent) cleanMsg.value = d.total ? '' : '没有失效条目'
  } catch (e) {
    if (!silent) cleanMsg.value = '检查失败：' + e.message
  } finally {
    missingLoading.value = false
  }
}
function toggleAllMissing() {
  checkedMissing.value = allChecked.value ? [] : missing.value.map(m => m.id)
}
async function doClean() {
  busy.value = 'clean'
  cleanMsg.value = ''
  try {
    const d = await api('/api/files/clean', {
      method: 'POST',
      body: JSON.stringify({ ids: checkedMissing.value, dry_run: false, library_id: props.library.id })
    })
    const removed = new Set(d.results.map(r => r.id))
    missing.value = missing.value.filter(m => !removed.has(m.id))
    checkedMissing.value = checkedMissing.value.filter(id => !removed.has(id))
    cleanMsg.value = `已删除 ${d.deleted}/${d.total}` + (d.failed.length ? `，失败 ${d.failed.length}` : '')
    emit('changed')
  } catch (e) {
    cleanMsg.value = '清理失败：' + e.message
  } finally {
    busy.value = null
  }
}

// ② 待匹配确认
const pendingOpen = ref(true)
const unmatched = ref([])
const needsReview = ref([])
const suspectHigh = ref([])
const suspectInfo = ref([])
const orphans = ref([])
const orphanMovie = ref({})
const showAllUnmatched = ref(false)
const showAllNeedsReview = ref(false)
const showAllSuspectHigh = ref(false)
const showAllSuspectInfo = ref(false)
const showAllOrphans = ref(false)
const visibleUnmatched = computed(() => showAllUnmatched.value ? unmatched.value : unmatched.value.slice(0, COLLAPSE_N))
const visibleNeedsReview = computed(() => showAllNeedsReview.value ? needsReview.value : needsReview.value.slice(0, COLLAPSE_N))
const visibleSuspectHigh = computed(() => showAllSuspectHigh.value ? suspectHigh.value : suspectHigh.value.slice(0, COLLAPSE_N))
const visibleSuspectInfo = computed(() => showAllSuspectInfo.value ? suspectInfo.value : suspectInfo.value.slice(0, COLLAPSE_N))
const visibleOrphans = computed(() => showAllOrphans.value ? orphans.value : orphans.value.slice(0, COLLAPSE_N))
const pendingTotal = computed(() => unmatched.value.length + needsReview.value.length + suspectHigh.value.length + orphans.value.length)
async function loadUnmatched(silent) {
  try {
    const d = await api('/api/files/unmatched?library=' + props.library.id)
    unmatched.value = d.unmatched || []
    needsReview.value = d.needs_review || []
    suspectHigh.value = d.suspect_title_high || []
    suspectInfo.value = d.suspect_title_info || []
    orphans.value = d.orphan_extras || []
  } catch (e) {
    if (!silent) scanMsg.value = '待处理加载失败：' + e.message
  }
}
// silent 参数与 loadMissing 对齐（仅静默失败，不改变行为）
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
async function confirmAllNeedsReview() {
  if (!needsReview.value.length) return
  await confirmReview(needsReview.value.map(m => m.id))
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
    const body = JSON.stringify({ dry_run: true, library_id: props.library.id })
    const prev = await api('/api/extras/collect', { method: 'POST', body })
    if (!prev.total) {
      collectMsg.value = '没有待归位花絮'
      return
    }
    const d = await api('/api/extras/collect', {
      method: 'POST', body: JSON.stringify({ dry_run: false, library_id: props.library.id })
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

// 父级（库标签页）选中本库时首次加载重清单
const loaded = ref(false)
async function ensure() {
  if (loaded.value) return
  loaded.value = true
  const jobs = [loadMissing(true)]
  if (isMovie.value) jobs.push(loadUnmatched(true), organizeRef.value?.ensure(true))
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
.sub-h { margin: 10px 0 4px; font-size: 0.9375rem; color: #ccc; display: flex; gap: 8px; align-items: center; }
.hint { color: #777; font-size: 0.8125rem; margin: 0 0 4px; }
.fhint { font-size: 0.75rem; color: #888; font-weight: normal; }
.warn-text { color: #e0a63c; }
.nav-badge { margin-left: 6px; font-size: 0.75rem; color: #e0a63c; }
.miss-list { list-style: none; margin: 4px 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.miss-row { display: flex; gap: 8px; align-items: center; background: #262626; border: 1px solid #3a3a3a; border-radius: 8px; padding: 6px 10px; font-size: 0.8125rem; }
.miss-title { white-space: nowrap; }
.miss-path { color: #888; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
</style>
