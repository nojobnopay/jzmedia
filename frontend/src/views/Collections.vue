<template>
  <div class="browse-page collections-page">
    <header class="browse-heading"><h1>合集</h1><JzButton v-if="!isChildren" :disabled="creating" icon="plus" variant="primary" :aria-expanded="createOpen" aria-controls="collection-create" @click="createOpen = !createOpen">新建合集</JzButton></header>
    <form class="bar collection-search" role="search" aria-label="搜索合集" @submit.prevent="load"><input v-model="q" aria-label="搜索合集" placeholder="搜合集名" /><JzButton type="submit" icon="search">搜索</JzButton></form>
    <form v-show="createOpen" id="collection-create" class="bar collection-create" @submit.prevent="create"><input v-model="name" :disabled="creating" aria-label="新合集名称" placeholder="新建合集名，如 周星驰合集" /><JzButton :disabled="!name.trim()" :loading="creating" type="submit" icon="plus">创建</JzButton><JzButton :disabled="creating" type="button" @click="createOpen = false">取消</JzButton></form>
    <p v-if="msg" role="status">{{ msg }}</p>
    <div class="collections-heading"><h2 class="sec-h">我的合集</h2><span v-if="!loading && !loadError">{{ items.length }} 个合集</span></div>
    <EmptyState v-if="loading" state="loading" title="正在加载合集" text="请稍候…" />
    <EmptyState v-else-if="loadError" state="error" title="合集加载失败" :text="loadError" retry @retry="load" />
    <EmptyState v-else-if="!items.length" :state="loadedQuery ? 'no-results' : 'empty'"
      :title="loadedQuery ? '没有符合条件的合集' : '还没有合集'"
      :text="loadedQuery ? '试试其他合集名，或清除搜索条件。' : isChildren ? '当前儿童模式下没有可见的合集。' : viewing.mode === 'private' ? '隐私模式下还没有包含可见影片的合集。' : '创建一个合集，或在海报墙多选影片后加入合集。'">
      <JzButton icon="search" v-if="loadedQuery" @click="clearSearch">清除搜索</JzButton>
      <JzButton icon="plus" v-else-if="!isChildren" variant="primary" @click="createOpen = true">新建合集</JzButton>
    </EmptyState>
    <div v-else class="grid collections-grid">
      <router-link v-for="c in items" :key="c.id" class="card collection-card" :to="'/c/' + c.id">
        <div class="poster-wrap"><img v-if="c.cover" :src="posterUrl(c.cover)" loading="lazy" :alt="c.name || '合集'" /><ArtworkPlaceholder v-else class="cover-empty" kind="collection" :label="c.name" /></div>
        <div class="collection-card-body"><h3>{{ c.name }}</h3><span>{{ c.member_count }} 部影片</span></div>
      </router-link>
    </div>
    <details v-if="!isChildren && (suggest.length || topups.length)" class="collection-section">
      <summary>推荐与可补齐合集 · {{ suggest.length + topups.length }}</summary>
      <section v-if="topups.length"><h3>可补齐</h3><div class="grid collection-action-grid">
        <div v-for="t in topups" :key="t.collection_id" class="card tp-card">
          <div class="t">《{{ t.name }}》有 {{ t.new_count }} 部新片</div><div class="t sub">{{ t.new_members.map(m => m.title).join(' / ') }}</div>
          <div class="bar"><JzButton @click="topUp(t)" :disabled="topping" :loading="topping && toppingCollection === t.collection_id" type="button">补齐合集</JzButton><router-link :to="'/c/' + t.collection_id">查看合集</router-link></div>
        </div>
      </div></section>
      <section v-if="suggest.length"><div class="bar"><h3>推荐合集</h3><JzButton @click="acceptAll" :disabled="accepting" :loading="accepting && acceptingSeries === null" type="button">全部接受（{{ suggest.length }}）</JzButton></div>
        <div class="grid collection-action-grid"><div v-for="entry in suggest" :key="entry.collection_tmdb_id" class="card collection-card sg-card">
          <div class="poster-wrap"><img v-if="entry.cover" :src="posterUrl(entry.cover)" loading="lazy" :alt="entry.collection_name" /><ArtworkPlaceholder v-else class="cover-empty" kind="collection" :label="entry.collection_name" /></div>
          <div class="collection-card-body"><h3>{{ entry.collection_name }}</h3><span>库内 {{ entry.member_count }} 部影片</span></div>
          <div class="t sub">{{ entry.members.map(m => m.title).join(' / ') }}</div>
          <div class="bar"><JzButton @click="accept(entry)" :disabled="accepting" :loading="accepting && acceptingSeries === entry.collection_tmdb_id" type="button">接受</JzButton><JzButton @click="dismiss(entry)" :disabled="accepting" type="button">忽略</JzButton></div>
        </div></div>
      </section>
    </details>
    <details v-if="!isChildren && (!viewing.enabled || viewing.management_unlocked)" class="collection-section" :open="bfRunning">
      <summary>合集维护<span v-if="bfRunning"> · 资料补全中</span></summary>
      <p v-if="coverage" class="fhint">{{ coverage.unchecked ?? coverage.without_collection }} 部待排查 · {{ coverage.standalone || 0 }} 部已确认无系列</p>
      <div class="bar"><JzButton icon="refresh" v-if="!bfRunning" @click="backfill(false)" :disabled="backfilling" type="button">补全系列信息</JzButton><JzButton v-else @click="cancelBackfill" type="button">取消补全</JzButton><JzButton v-if="!bfRunning && coverage?.standalone" @click="backfill(true)" :disabled="backfilling" type="button">全部重查</JzButton></div>
      <p v-if="sgMsg" role="status">{{ sgMsg }}</p>
      <div v-if="bfRunning || bfProgress.total" class="progress-wrap"><div class="progress"><div class="fill" :style="{ width: bfPct + '%' }"></div></div><p>{{ bfProgress.done }}/{{ bfProgress.total }} · {{ bfProgress.current_title || bfStateText }}</p>
        <p v-for="failure in (bfProgress.failed || []).slice(0, 5)" :key="failure.tmdb_id" class="fail-list">{{ failure.title || failure.tmdb_id }}：{{ failure.error }}</p>
      </div>
    </details>
  </div>
</template>
<script setup>
import { isChildren, viewing } from '../viewing.js'
import ArtworkPlaceholder from '../components/ArtworkPlaceholder.vue'

import EmptyState from '../components/EmptyState.vue'
import JzButton from '../components/JzButton.vue'
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { api, posterUrl } from '../api.js'
import { currentMediaId, mediaParam, loadLibs, onLibChange } from '../libraries.js'
import { usePolling } from '../usePolling.js'

const createOpen = ref(false)
const creating = ref(false)
const q = ref('')
const name = ref('')
const items = ref([])
const loading = ref(true)
const loadError = ref('')
const loadedQuery = ref('')
let loadSeq = 0
let disposed = false
const msg = ref('')
const suggest = ref([])
const topups = ref([])
const coverage = ref(null)
const sgMsg = ref('')
const backfilling = ref(false)
const topping = ref(false)
const toppingCollection = ref(null)
const jobId = ref('')
const bfProgress = ref({ state: 'idle', done: 0, total: 0, current_title: '', failed: [] })
const accepting = ref(false)
const acceptingSeries = ref(null)
const DISMISS_KEY = 'jzmedia.dismissedSeries'
const BF_MAX_ROUNDS = 4          // 自动续跑上限（评审 B5a-10/R06-D4：一轮 50 条，防死循环）
let bfRounds = 0                 // 当前补全链已发起的轮数
let bfAuto = false               // 当前链是否为「一键补全」（force=全部重查不自动续）
let scopeGeneration = 0
let suggestSeq = 0
let suggestController = null
let backfillContext = null

function scopeContext() {
  return { generation: scopeGeneration, mediaId: currentMediaId(), mediaScope: mediaParam() }
}
function currentScope(context) {
  return !disposed && context.generation === scopeGeneration
    && context.mediaId === currentMediaId() && context.mediaScope === mediaParam()
}
function currentBackfill(context) {
  return context && context === backfillContext && currentScope(context)
}
function sameJobScope(data, context) {
  return Object.hasOwn(data, 'media_library_id')
    && (data.media_library_id == null ? null : Number(data.media_library_id)) === context.mediaScope
}
function resetBackfill() {
  backfillContext?.controller.abort()
  backfillContext = null
  stopPoll()
  backfilling.value = false
  bfAuto = false
  bfRounds = 0
  jobId.value = ''
  lastPollDone = -1
  bfProgress.value = { state: 'idle', done: 0, total: 0, current_title: '', failed: [] }
}
function newBackfillContext() {
  resetBackfill()
  backfillContext = { ...scopeContext(), controller: new AbortController() }
  return backfillContext
}

function dismissedIds() {
  try {
    return new Set(JSON.parse(localStorage.getItem(DISMISS_KEY) || '[]'))
  } catch (e) {
    return new Set()
  }
}

async function loadSuggest(context = scopeContext()) {
  if (!currentScope(context) || isChildren.value) return null
  const seq = ++suggestSeq
  suggestController?.abort()
  suggestController = new AbortController()
  const signal = suggestController.signal
  try {
    const d = await api('/api/collections/suggest'
      + (context.mediaScope != null ? '?media_library=' + context.mediaScope : ''), { signal })
    if (!currentScope(context) || seq !== suggestSeq) return null
    const gone = dismissedIds()
    suggest.value = (d.items || []).filter(s => !gone.has(s.collection_tmdb_id))
    topups.value = d.topups || []
    coverage.value = d.coverage || null
    return d
  } catch (e) {
    if (currentScope(context) && seq === suggestSeq && !signal.aborted) sgMsg.value = '推荐加载失败：' + e.message
    return null
  }
}
async function backfill(force = false, context = null) {
  if (!context) {
    if (disposed || backfilling.value) return
    context = newBackfillContext()
    bfAuto = !force
  }
  if (!currentBackfill(context)) return
  backfilling.value = true
  sgMsg.value = ''
  try {
    const d = await api('/api/collections/suggest/backfill', {
      method: 'POST', body: JSON.stringify({ limit: 50, force, media_library: context.mediaScope }),
      signal: context.controller.signal,
    })
    if (!currentBackfill(context)) return
    if (Object.hasOwn(d, 'media_library_id') && !sameJobScope(d, context)) {
      resetBackfill()
      sgMsg.value = '另一媒体库的补全正在运行，请稍后重试。'
      return
    }
    // Older servers do not expose the resumed job's scope/force; only follow its progress.
    if (d.resumed) bfAuto = bfAuto && sameJobScope(d, context) && d.force === false
    if (!d.total) {
      sgMsg.value = '没有缺系列信息的影片'
      backfilling.value = false
      bfRounds = 0
      return
    }
    bfRounds += 1
    jobId.value = d.job_id || ''
    lastPollDone = -1
    if (d.resumed) sgMsg.value = '已复用运行中的补全任务，继续跟踪进度…'
    startPoll()
  } catch (e) {
    if (!currentBackfill(context)) return
    sgMsg.value = '补全失败：' + e.message
    backfilling.value = false
    bfRounds = 0
  }
}
const bfRunning = computed(() => backfilling.value && bfProgress.value.state === 'running')
const bfPct = computed(() => {
  const t = bfProgress.value.total || 0
  if (!t) return 0
  return Math.round((bfProgress.value.done || 0) / t * 100)
})
const bfStateText = computed(() => {
  const s = bfProgress.value.state
  if (s === 'done') return '补全完成'
  if (s === 'cancelled') return '已取消'
  return '补全中…'
})
let lastPollDone = -1
const bfPoll = usePolling(pollStatus, { interval: 2000, immediate: true })
function startPoll() { bfPoll.start() }
function stopPoll() { bfPoll.stop() }
async function pollStatus(context = backfillContext) {
  if (!currentBackfill(context) || !jobId.value) return
  const requestedJob = jobId.value
  try {
    const d = await api('/api/collections/suggest/backfill/status' +
      '?job_id=' + encodeURIComponent(requestedJob), { signal: context.controller.signal })
    if (!currentBackfill(context)) return
    if ((d.job_id && d.job_id !== requestedJob)
        || (Object.hasOwn(d, 'media_library_id') && !sameJobScope(d, context))) {
      resetBackfill()
      sgMsg.value = '补全任务范围已变更，请重新检查。'
      return
    }
    bfProgress.value = {
      state: d.state || 'idle', done: d.done || 0, total: d.total || 0,
      current_title: d.current_title || '', failed: d.failed || []
    }
    if (d.job_id) jobId.value = d.job_id
    // 增量呈现：仅进度变化或收尾时刷新推荐（评审 B8/R06-D2：此前每 2s 一次全量聚合）
    const progressed = d.done !== lastPollDone
    lastPollDone = d.done
    const refreshed = progressed || d.state === 'done' || d.state === 'cancelled'
      ? await loadSuggest(context) : null
    if (!currentBackfill(context)) return
    if (d.state === 'done' || d.state === 'cancelled') {
      stopPoll()
      backfilling.value = false
      if (d.state === 'done' && bfAuto && bfRounds < BF_MAX_ROUNDS
          && (refreshed?.coverage?.unchecked || 0) > 0) {
        // 还有未排查的片：自动续跑下一轮（最多 BF_MAX_ROUNDS 轮）
        sgMsg.value = `已完成 ${d.done}/${d.total}，继续补全剩余…`
        await backfill(false, context)
        return
      }
      bfRounds = 0
      bfAuto = false
      sgMsg.value = d.state === 'done'
        ? `补全完成 ${d.done}/${d.total}` + ((d.failed || []).length ? `，失败 ${(d.failed || []).length}` : '')
        : `已取消（${d.done}/${d.total}）`
    }
  } catch (e) {
    if (currentBackfill(context) && e.status === 404) {
      resetBackfill()
      sgMsg.value = '补全任务已失效，可重新开始。'
    }
    // Connection failures remain retryable; a missing job cannot reappear after a restart.
  }
}
async function cancelBackfill() {
  const context = backfillContext
  if (!currentBackfill(context) || !jobId.value) return
  bfAuto = false
  try {
    await api('/api/collections/suggest/backfill/cancel', {
      method: 'POST', body: JSON.stringify({ job_id: jobId.value }), signal: context.controller.signal,
    })
  } catch (e) {
    if (!currentBackfill(context)) return
    if (e.status === 404) {
      resetBackfill()
      sgMsg.value = '补全任务已失效，可重新开始。'
      return
    }
    sgMsg.value = '取消补全失败：' + e.message
  }
  await pollStatus(context)
}
async function accept(s) {
  const context = scopeContext()
  if (!currentScope(context) || accepting.value || !suggest.value.includes(s)) return
  accepting.value = true
  acceptingSeries.value = s.collection_tmdb_id
  sgMsg.value = ''
  try {
    await api('/api/collections/from-tmdb-series', {
      method: 'POST',
      body: JSON.stringify({ movie_id: s.members[0].id, name: s.collection_name })
    })
    if (!currentScope(context)) return
    await load()
    if (!currentScope(context)) return
    await loadSuggest(context)
  } catch (e) {
    if (currentScope(context)) sgMsg.value = '创建失败：' + e.message
  } finally {
    if (currentScope(context)) { accepting.value = false; acceptingSeries.value = null }
  }
}
// 批量接受当前库的全部系列推荐（逐条串行，避免并发写合集/成员）
async function acceptAll() {
  const context = scopeContext()
  if (!currentScope(context) || accepting.value || !suggest.value.length) return
  accepting.value = true
  acceptingSeries.value = null
  sgMsg.value = ''
  const list = [...suggest.value]
  const failed = []
  let ok = 0
  for (const s of list) {
    if (!currentScope(context)) return
    try {
      await api('/api/collections/from-tmdb-series', {
        method: 'POST',
        body: JSON.stringify({ movie_id: s.members[0].id, name: s.collection_name })
      })
      if (!currentScope(context)) return
      ok += 1
      sgMsg.value = `批量建合集 ${ok}/${list.length}…`
    } catch (e) {
      if (!currentScope(context)) return
      failed.push(s.collection_name || String(s.collection_tmdb_id))
    }
  }
  if (!currentScope(context)) return
  sgMsg.value = `已建 ${ok} 个合集`
    + (failed.length ? `，失败 ${failed.length}：${failed.slice(0, 3).join('、')}${failed.length > 3 ? ' 等' : ''}` : '')
  try {
    await load()
    if (!currentScope(context)) return
    await loadSuggest(context)
  } finally { if (currentScope(context)) accepting.value = false }
}
function dismiss(s) {
  if (accepting.value || !suggest.value.includes(s)) return
  try {
    const gone = dismissedIds()
    gone.add(s.collection_tmdb_id)
    localStorage.setItem(DISMISS_KEY, JSON.stringify([...gone]))
  } catch (e) { /* 忽略 */ }
  suggest.value = suggest.value.filter(x => x.collection_tmdb_id !== s.collection_tmdb_id)
}
async function topUp(t) {
  const context = scopeContext()
  if (!currentScope(context) || topping.value || !topups.value.includes(t)) return
  topping.value = true
  toppingCollection.value = t.collection_id
  sgMsg.value = ''
  try {
    const d = await api(`/api/collections/${t.collection_id}/members/top-up`, { method: 'POST' })
    if (!currentScope(context)) return
    sgMsg.value = `已补齐 ${d.added} 部`
    await load()
    if (!currentScope(context)) return
    await loadSuggest(context)
  } catch (e) {
    if (currentScope(context)) sgMsg.value = '补齐失败：' + e.message
  } finally {
    if (currentScope(context)) { topping.value = false; toppingCollection.value = null }
  }
}

async function load() {
  const seq = ++loadSeq
  const query = q.value.trim()
  loading.value = true
  loadError.value = ''
  msg.value = ''
  try {
    const p = new URLSearchParams()
    if (query) p.set('q', query)
    if (mediaParam() != null) p.set('media_library', String(mediaParam()))
    const qs = p.toString()
    const d = await api('/api/collections' + (qs ? '?' + qs : ''))
    if (seq !== loadSeq || disposed) return
    items.value = d.items || []
    loadedQuery.value = query
  } catch (e) {
    if (seq === loadSeq && !disposed) loadError.value = e.message
  } finally {
    if (seq === loadSeq && !disposed) loading.value = false
  }
}
function clearSearch() { q.value = ''; load() }

async function create() {
  const n = name.value.trim()
  const context = scopeContext()
  if (!n || !currentScope(context) || creating.value) return
  creating.value = true
  msg.value = ''
  try {
    const d = await api('/api/collections', {
      method: 'POST',
      body: JSON.stringify({ name: n, media_library_id: context.mediaId })
    })
    if (!currentScope(context)) return
    name.value = ''
    createOpen.value = false
    items.value.unshift({ id: d.id, name: d.name, member_count: 0, cover: d.cover || '' })
  } catch (e) {
    if (currentScope(context)) msg.value = '创建失败：' + e.message
  } finally { if (currentScope(context)) creating.value = false }
}
async function resumeBackfill() {
  if (isChildren.value || viewing.enabled && !viewing.management_unlocked) return
  const context = newBackfillContext()
  // 续跑：刷新页面后后台若还在补全，自动续上进度条
  try {
    const d = await api('/api/collections/suggest/backfill/status', { signal: context.controller.signal })
    if (!currentBackfill(context)) return
    if (Object.hasOwn(d, 'media_library_id') && !sameJobScope(d, context)) return
    if (d.state === 'running' && d.total) {
      jobId.value = d.job_id || ''
      backfilling.value = true
      bfRounds = Math.max(1, bfRounds)   // 刷新后接力：完成后仍可自动续跑
      bfAuto = sameJobScope(d, context) && d.force === false
      bfProgress.value = {
        state: d.state, done: d.done || 0, total: d.total || 0,
        current_title: d.current_title || '', failed: d.failed || []
      }
      startPoll()
    }
  } catch (e) { /* 无后台任务时静默 */ }
}
let unsubLib = null
onMounted(async () => {
  try { await loadLibs(api) } catch (e) { /* 忽略 */ }
  if (disposed) return
  unsubLib = onLibChange(() => {
    scopeGeneration++
    suggestSeq++
    suggestController?.abort()
    resetBackfill()
    suggest.value = []
    topups.value = []
    coverage.value = null
    sgMsg.value = ''
    creating.value = false
    createOpen.value = false
    name.value = ''
    accepting.value = false
    acceptingSeries.value = null
    topping.value = false
    toppingCollection.value = null
    load()
    loadSuggest()
  })
  await Promise.all([load(), loadSuggest(), resumeBackfill()])
})
onUnmounted(() => {
  disposed = true
  scopeGeneration++
  loadSeq++
  suggestSeq++
  suggestController?.abort()
  resetBackfill()
  if (unsubLib) { try { unsubLib() } catch (e) { /* 忽略 */ } unsubLib = null }
})
</script>
<style scoped>
.bar { flex-wrap: wrap; align-items: center; padding: var(--jz-gap-m) 0; }
.collection-search { max-width: 640px; padding: 0; margin: 0 0 var(--jz-gap-xl); }
.collection-search input, .collection-create input { flex: 1 1 180px; min-width: 0; }
.collection-create { padding: var(--jz-gap-l); border: 1px solid var(--jz-border); border-radius: var(--jz-radius-m); background: var(--jz-surface); }
.collections-heading { display: flex; align-items: baseline; gap: var(--jz-gap-s); margin: var(--jz-gap-2xl) 0 var(--jz-gap-l); }
.sec-h { margin: 0; font-size: 1.125rem; font-weight: 600; }
.collections-heading > span { color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.collections-grid { grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); }
.collection-card { color: inherit; text-decoration: none; min-width: 0; }
.collection-card .poster-wrap { aspect-ratio: 2 / 3; border: 1px solid var(--jz-border); }
.cover-empty { height: 100%; aspect-ratio: 2/3; display: flex; align-items: center; justify-content: center; color: var(--jz-text-faint); background: var(--jz-surface-2); }
.collection-card-body { padding-top: var(--jz-gap-m); }
.collection-card-body h3 { font-size: var(--jz-font-l); font-weight: 600; line-height: 1.5; margin: 0 0 4px; overflow-wrap: anywhere; }
.collection-card-body > span { color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.collection-section { margin-top: var(--jz-gap-3xl); padding-top: var(--jz-gap-xl); border-top: 1px solid var(--jz-border); }
.collection-section summary { cursor: pointer; font-size: var(--jz-font-m); font-weight: 500; line-height: 1.6; }
.collection-section h3 { font-size: var(--jz-font-m); }
.collection-action-grid { grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); }
.tp-card { padding: var(--jz-gap-l); border: 1px solid var(--jz-border); }
.t.sub, .fhint { color: var(--jz-text-dim); font-size: var(--jz-font-s); line-height: 1.6; }
.progress-wrap { display: grid; gap: var(--jz-gap-xs); margin-block: var(--jz-gap-m); }
.progress { height: 6px; border-radius: var(--jz-radius-pill); background: var(--jz-surface-3); overflow: hidden; }
.progress .fill { height: 100%; background: var(--jz-accent); }
.fail-list { color: var(--jz-warn); }
button { white-space: nowrap; min-height: var(--jz-control-current); }
a { color: var(--jz-link); }
@media (max-width: 700px) {
  .collection-search { margin-bottom: var(--jz-gap-m); }
  .collections-heading { margin-block: var(--jz-gap-l) var(--jz-gap-m); }
  .collections-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .collection-card-body h3 { font-size: var(--jz-font-m); }
  .collection-section { margin-top: var(--jz-gap-2xl); padding-top: var(--jz-gap-l); }
}
</style>
