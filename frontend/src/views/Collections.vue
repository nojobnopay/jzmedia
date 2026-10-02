<template>
  <div class="browse-page collections-page">
    <header class="browse-heading"><h1>合集</h1><button :aria-expanded="createOpen" @click="createOpen = !createOpen">新建合集</button></header>
    <form class="bar collection-search" @submit.prevent="load"><input v-model="q" aria-label="搜索合集" placeholder="搜合集名" /><button>搜索</button></form>
    <form v-if="createOpen" class="bar collection-create" @submit.prevent="create"><input v-model="name" aria-label="新合集名称" placeholder="新建合集名，如 周星驰合集" /><button :disabled="!name.trim()">创建</button><button type="button" @click="createOpen = false">取消</button></form>
    <p v-if="msg" role="status">{{ msg }}</p>
    <h2 class="sec-h">我的合集</h2>
    <div class="grid">
      <router-link v-for="c in items" :key="c.id" class="card" :to="'/c/' + c.id">
        <div class="poster-wrap"><img v-if="c.cover" :src="posterUrl(c.cover)" loading="lazy" :alt="c.name || '合集'" /><div v-else class="cover-empty">📁</div></div>
        <div class="t">{{ c.name }}（{{ c.member_count }} 部）</div>
      </router-link>
    </div>
    <p v-if="!items.length">{{ q.trim() ? '没有符合条件的合集。' : '还没有合集，可以新建合集，或在海报墙多选影片后加入合集。' }}</p>
    <details v-if="suggest.length || topups.length" class="collection-section">
      <summary>推荐与可补齐合集 · {{ suggest.length + topups.length }}</summary>
      <section v-if="topups.length"><h3>可补齐</h3><div class="grid collection-action-grid">
        <div v-for="t in topups" :key="t.collection_id" class="card tp-card">
          <div class="t">《{{ t.name }}》有 {{ t.new_count }} 部新片</div><div class="t sub">{{ t.new_members.map(m => m.title).join(' / ') }}</div>
          <div class="bar"><button @click="topUp(t)" :disabled="topping">补齐合集</button><router-link :to="'/c/' + t.collection_id">查看合集</router-link></div>
        </div>
      </div></section>
      <section v-if="suggest.length"><div class="bar"><h3>推荐合集</h3><button @click="acceptAll" :disabled="accepting">全部接受（{{ suggest.length }}）</button></div>
        <div class="grid collection-action-grid"><div v-for="entry in suggest" :key="entry.collection_tmdb_id" class="card sg-card">
          <div class="poster-wrap"><img v-if="entry.cover" :src="posterUrl(entry.cover)" loading="lazy" :alt="entry.collection_name" /><div v-else class="cover-empty">📁</div></div>
          <div class="t">{{ entry.collection_name }}（库内 {{ entry.member_count }} 部）</div>
          <div class="t sub">{{ entry.members.map(m => m.title).join(' / ') }}</div>
          <div class="bar"><button @click="accept(entry)" :disabled="accepting">接受</button><button @click="dismiss(entry)">忽略</button></div>
        </div></div>
      </section>
    </details>
    <details class="collection-section" :open="bfRunning">
      <summary>合集维护<span v-if="bfRunning"> · 资料补全中</span></summary>
      <p v-if="coverage" class="fhint">{{ coverage.unchecked ?? coverage.without_collection }} 部待排查 · {{ coverage.standalone || 0 }} 部已确认无系列</p>
      <div class="bar"><button v-if="!bfRunning" @click="backfill(false)" :disabled="backfilling">补全系列信息</button><button v-else @click="cancelBackfill">取消补全</button><button v-if="!bfRunning && coverage?.standalone" @click="backfill(true)" :disabled="backfilling">全部重查</button></div>
      <p v-if="sgMsg" role="status">{{ sgMsg }}</p>
      <div v-if="bfRunning || bfProgress.total" class="progress-wrap"><div class="progress"><div class="fill" :style="{ width: bfPct + '%' }"></div></div><p>{{ bfProgress.done }}/{{ bfProgress.total }} · {{ bfProgress.current_title || bfStateText }}</p>
        <p v-for="failure in (bfProgress.failed || []).slice(0, 5)" :key="failure.tmdb_id" class="fail-list">{{ failure.title || failure.tmdb_id }}：{{ failure.error }}</p>
      </div>
    </details>
  </div>
</template>
<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { api, posterUrl } from '../api.js'
import { currentMediaId, mediaParam, loadLibs, onLibChange } from '../libraries.js'
import { usePolling } from '../usePolling.js'

const createOpen = ref(false)
const q = ref('')
const name = ref('')
const items = ref([])
const msg = ref('')
const suggest = ref([])
const topups = ref([])
const coverage = ref(null)
const sgMsg = ref('')
const backfilling = ref(false)
const topping = ref(false)
const jobId = ref('')
const bfProgress = ref({ state: 'idle', done: 0, total: 0, current_title: '', failed: [] })
const accepting = ref(false)
const DISMISS_KEY = 'jzmedia.dismissedSeries'
const BF_MAX_ROUNDS = 4          // 自动续跑上限（评审 B5a-10/R06-D4：一轮 50 条，防死循环）
let bfRounds = 0                 // 当前补全链已发起的轮数
let bfAuto = false               // 当前链是否为「一键补全」（force=全部重查不自动续）

function dismissedIds() {
  try {
    return new Set(JSON.parse(localStorage.getItem(DISMISS_KEY) || '[]'))
  } catch (e) {
    return new Set()
  }
}

async function loadSuggest() {
  sgMsg.value = ''
  try {
    const d = await api('/api/collections/suggest'
      + (mediaParam() != null ? '?media_library=' + mediaParam() : ''))
    const gone = dismissedIds()
    suggest.value = (d.items || []).filter(s => !gone.has(s.collection_tmdb_id))
    topups.value = d.topups || []
    coverage.value = d.coverage || null
  } catch (e) { /* 无系列信息时静默 */ }
}
async function backfill(force = false) {
  backfilling.value = true
  if (force || bfRounds === 0) {
    bfRounds = 0
    bfAuto = !force
  }
  sgMsg.value = ''
  try {
    const d = await api('/api/collections/suggest/backfill', {
      method: 'POST', body: JSON.stringify({ limit: 50, force, media_library: mediaParam() })
    })
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
async function pollStatus() {
  try {
    const d = await api('/api/collections/suggest/backfill/status' +
      (jobId.value ? '?job_id=' + encodeURIComponent(jobId.value) : ''))
    bfProgress.value = {
      state: d.state || 'idle', done: d.done || 0, total: d.total || 0,
      current_title: d.current_title || '', failed: d.failed || []
    }
    if (d.job_id) jobId.value = d.job_id
    // 增量呈现：仅进度变化或收尾时刷新推荐（评审 B8/R06-D2：此前每 2s 一次全量聚合）
    const progressed = d.done !== lastPollDone
    lastPollDone = d.done
    if (progressed || d.state === 'done' || d.state === 'cancelled') {
      await loadSuggest()
    }
    if (d.state === 'done' || d.state === 'cancelled') {
      stopPoll()
      backfilling.value = false
      if (d.state === 'done' && bfAuto && bfRounds < BF_MAX_ROUNDS
          && ((coverage.value && coverage.value.unchecked) || 0) > 0) {
        // 还有未排查的片：自动续跑下一轮（最多 BF_MAX_ROUNDS 轮）
        sgMsg.value = `已完成 ${d.done}/${d.total}，继续补全剩余…`
        await backfill(false)
        return
      }
      bfRounds = 0
      bfAuto = false
      sgMsg.value = d.state === 'done'
        ? `补全完成 ${d.done}/${d.total}` + ((d.failed || []).length ? `，失败 ${(d.failed || []).length}` : '')
        : `已取消（${d.done}/${d.total}）`
    }
  } catch (e) { /* 轮询失败忽略，下次继续 */ }
}
async function cancelBackfill() {
  try {
    await api('/api/collections/suggest/backfill/cancel', {
      method: 'POST', body: JSON.stringify({ job_id: jobId.value })
    })
  } catch (e) { /* 忽略 */ }
  await pollStatus()
}
async function accept(s) {
  accepting.value = true
  sgMsg.value = ''
  try {
    await api('/api/collections/from-tmdb-series', {
      method: 'POST',
      body: JSON.stringify({ movie_id: s.members[0].id, name: s.collection_name })
    })
    await load()
    await loadSuggest()
  } catch (e) {
    sgMsg.value = '创建失败：' + e.message
  } finally {
    accepting.value = false
  }
}
// 批量接受当前库的全部系列推荐（逐条串行，避免并发写合集/成员）
async function acceptAll() {
  if (accepting.value || !suggest.value.length) return
  accepting.value = true
  sgMsg.value = ''
  const list = [...suggest.value]
  const failed = []
  let ok = 0
  for (const s of list) {
    try {
      await api('/api/collections/from-tmdb-series', {
        method: 'POST',
        body: JSON.stringify({ movie_id: s.members[0].id, name: s.collection_name })
      })
      ok += 1
      sgMsg.value = `批量建合集 ${ok}/${list.length}…`
    } catch (e) {
      failed.push(s.collection_name || String(s.collection_tmdb_id))
    }
  }
  accepting.value = false
  sgMsg.value = `已建 ${ok} 个合集`
    + (failed.length ? `，失败 ${failed.length}：${failed.slice(0, 3).join('、')}${failed.length > 3 ? ' 等' : ''}` : '')
  await load()
  await loadSuggest()
}
function dismiss(s) {
  try {
    const gone = dismissedIds()
    gone.add(s.collection_tmdb_id)
    localStorage.setItem(DISMISS_KEY, JSON.stringify([...gone]))
  } catch (e) { /* 忽略 */ }
  suggest.value = suggest.value.filter(x => x.collection_tmdb_id !== s.collection_tmdb_id)
}
async function topUp(t) {
  topping.value = true
  sgMsg.value = ''
  try {
    const d = await api(`/api/collections/${t.collection_id}/members/top-up`, { method: 'POST' })
    sgMsg.value = `已补齐 ${d.added} 部`
    await load()
    await loadSuggest()
  } catch (e) {
    sgMsg.value = '补齐失败：' + e.message
  } finally {
    topping.value = false
  }
}

async function load() {
  msg.value = ''
  try {
    const p = new URLSearchParams()
    if (q.value.trim()) p.set('q', q.value.trim())
    if (mediaParam() != null) p.set('media_library', String(mediaParam()))
    const qs = p.toString()
    const d = await api('/api/collections' + (qs ? '?' + qs : ''))
    items.value = d.items || []
  } catch (e) {
    msg.value = '加载失败：' + e.message
  }
}

async function create() {
  const n = name.value.trim()
  if (!n) return
  msg.value = ''
  try {
    const d = await api('/api/collections', {
      method: 'POST',
      body: JSON.stringify({ name: n, media_library_id: currentMediaId() })
    })
    name.value = ''
    createOpen.value = false
    items.value.unshift({ id: d.id, name: d.name, member_count: 0, cover: d.cover || '' })
  } catch (e) {
    msg.value = '创建失败：' + e.message
  }
}
async function resumeBackfill() {
  // 续跑：刷新页面后后台若还在补全，自动续上进度条
  try {
    const d = await api('/api/collections/suggest/backfill/status')
    if (d.state === 'running' && d.total) {
      jobId.value = d.job_id || ''
      backfilling.value = true
      bfRounds = Math.max(1, bfRounds)   // 刷新后接力：完成后仍可自动续跑
      bfAuto = true
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
  await Promise.all([load(), loadSuggest(), resumeBackfill()])
  unsubLib = onLibChange(() => { load(); loadSuggest() })
})
onUnmounted(() => {
  stopPoll()
  if (unsubLib) { try { unsubLib() } catch (e) { /* 忽略 */ } unsubLib = null }
})
</script>
<style scoped>
.cover-empty { aspect-ratio: 2/3; display: flex; align-items: center; justify-content: center; font-size: 2.5rem; background: #262626; }
.suggest-sec { border-bottom: 1px dashed #3a3a3a; margin-bottom: 8px; }
.suggest-sec h3, .sec-h { padding: 0 12px; font-size: 1rem; color: #ddd; }
.fhint { color: #777; font-size: 0.75rem; font-weight: normal; }
.sg-card { border: 1px dashed #6b5518; }
.topup-sec { border-bottom: 1px dashed #3a3a3a; margin-bottom: 8px; }
.topup-sec h3 { padding: 0 12px; font-size: 1rem; color: #ddd; }
.tp-card { border: 1px solid #2b4a6e; }
.t.sub { color: #888; font-size: 0.75rem; }
.progress-wrap { padding: 0 12px 8px; display: flex; flex-direction: column; gap: 4px; }
.progress { height: 8px; border-radius: 999px; background: #262626; overflow: hidden; }
.progress .fill { height: 100%; background: #e50914; border-radius: 999px; transition: width .4s; }
.fail-list { color: #e0a63c; }
</style>

<style scoped>
.bar { flex-wrap: wrap; align-items: center; padding: 12px 0; }
.collection-search input, .collection-create input { flex: 1 1 180px; min-width: 0; max-width: 560px; }
button { white-space: nowrap; min-height: 40px; }
.card { color: inherit; text-decoration: none; min-width: 0; }
.collection-section { margin-top: 24px; padding: 16px; border: 1px solid var(--jz-border); border-radius: 8px; }
summary { cursor: pointer; line-height: 1.5; }
.t, p { overflow-wrap: anywhere; }
a { color: var(--jz-link); }
</style>
