<template>
  <div class="bar">
    <input v-model="q" placeholder="搜合集名" @keyup.enter="onSearchEnter" style="flex:1" />
    <button @click="load">搜索</button>
    <input v-model="name" placeholder="新建合集名，如 周星驰合集" style="flex:1" />
    <button @click="create" :disabled="!name.trim()">新建</button>
    <span>{{ msg }}</span>
  </div>
  <section v-if="topups.length" class="topup-sec">
    <h3>可补齐 <span class="fhint">已有合集的同系列新片，点一次收进合集</span></h3>
    <div class="grid">
      <div v-for="t in topups" :key="t.collection_id" class="card tp-card">
        <div class="t">《{{ t.name }}》有 {{ t.new_count }} 部新片</div>
        <div class="t sub">{{ t.new_members.map(m => m.title + (m.year ? ' ' + m.year : '')).join(' / ') }}</div>
        <div class="t">
          <button @click="topUp(t)" :disabled="topping">一键补齐</button>
          <button @click="$router.push('/c/' + t.collection_id)">看合集</button>
        </div>
      </div>
    </div>
  </section>
  <section v-if="suggest.length || coverage" class="suggest-sec">
    <h3>推荐合集 <span class="fhint">TMDB 系列·库内≥2部才推荐，接受后即从这里消失</span></h3>
    <div v-if="coverage && (coverage.unchecked || coverage.without_collection)" class="bar">
      <span class="fhint">{{ coverage.unchecked ?? coverage.without_collection }} 部待排查<span v-if="coverage.standalone"> · {{ coverage.standalone }} 部确认无系列（独立片，不再检查）</span>（仅补系列信息，不碰海报）</span>
      <button v-if="!bfRunning" @click="backfill(false)" :disabled="backfilling">补全系列信息</button>
      <button v-else @click="cancelBackfill">取消补全</button>
      <button v-if="!bfRunning && coverage.standalone" @click="backfill(true)" :disabled="backfilling" title="忽略已确认结论，全部重查一遍">全部重查</button>
      <span>{{ sgMsg }}</span>
    </div>
    <div v-if="bfRunning || bfProgress.total" class="progress-wrap">
      <div class="progress"><div class="fill" :style="{ width: bfPct + '%' }"></div></div>
      <div class="fhint">{{ bfProgress.done }}/{{ bfProgress.total }} · {{ bfProgress.current_title || bfStateText }}
        <span v-if="bfProgress.failed && bfProgress.failed.length"> · 失败 {{ bfProgress.failed.length }}</span>
      </div>
      <div v-if="bfProgress.failed && bfProgress.failed.length" class="fhint fail-list">
        <span v-for="f in bfProgress.failed.slice(0, 5)" :key="f.tmdb_id">{{ f.title || f.tmdb_id }}：{{ f.error }}；</span>
        <span v-if="bfProgress.failed.length > 5">等共 {{ bfProgress.failed.length }} 项</span>
      </div>
    </div>
    <div class="grid">
      <div v-for="s in suggest" :key="s.collection_tmdb_id" class="card sg-card">
        <div class="poster-wrap">
          <img v-if="s.cover" :src="posterUrl(s.cover)" loading="lazy" :alt="s.collection_name || '合集'" />
          <div v-else class="cover-empty">📁</div>
        </div>
        <div class="t">{{ s.collection_name }}（库内 {{ s.member_count }} 部）</div>
        <div class="t sub">{{ s.members.map(m => m.title + (m.year ? ' ' + m.year : '')).join(' / ') }}</div>
        <div class="t">
          <button @click="accept(s)" :disabled="accepting">接受</button>
          <button @click="dismiss(s)">忽略</button>
        </div>
      </div>
    </div>
  </section>
  <h3 class="sec-h">我的合集</h3>
  <div class="grid">
    <div v-for="c in items" :key="c.id" class="card" @click="$router.push('/c/' + c.id)">
      <div class="poster-wrap">
        <img v-if="c.cover" :src="posterUrl(c.cover)" loading="lazy" :alt="c.name || '合集'" />
        <div v-else class="cover-empty">📁</div>
      </div>
      <div class="t">{{ c.name }}（{{ c.member_count }} 部）</div>
    </div>
  </div>
  <div v-if="!items.length" class="bar">还没有合集：去海报墙多选影片后“新建合集”，或在详情页按 TMDB 系列一键建（如功夫熊猫）。</div>
</template>
<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { api, posterUrl } from '../api.js'

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
    const d = await api('/api/collections/suggest')
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
      method: 'POST', body: JSON.stringify({ limit: 50, force })
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
let pollTimer = null
let lastPollDone = -1
function startPoll() {
  stopPoll()
  pollTimer = setInterval(pollStatus, 2000)
  pollStatus()
}
function stopPoll() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
}
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
    const d = await api('/api/collections' + (q.value.trim() ? '?q=' + encodeURIComponent(q.value.trim()) : ''))
    items.value = d.items || []
  } catch (e) {
    msg.value = '加载失败：' + e.message
  }
}
function onSearchEnter(e) {
  if (e && (e.isComposing || e.keyCode === 229)) return
  load()
}
async function create() {
  const n = name.value.trim()
  if (!n) return
  msg.value = ''
  try {
    const d = await api('/api/collections', { method: 'POST', body: JSON.stringify({ name: n }) })
    name.value = ''
    items.value.unshift({ id: d.id, name: d.name, member_count: 0, cover: d.cover || '' })
  } catch (e) {
    msg.value = '创建失败：' + e.message
  }
}
onMounted(async () => {
  await load()
  await loadSuggest()
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
})
onUnmounted(() => { stopPoll() })
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
