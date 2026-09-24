<template>
  <div class="bar">
    <div class="q-wrap">
      <input v-model="q" placeholder="搜剧名 / 演员" autocomplete="off"
        @input="onQInput" @compositionstart="composing = true" @compositionend="onCompositionEnd"
        @keyup.enter="onSearchEnter" @keydown.down.prevent="suggestMove(1)"
        @keydown.up.prevent="suggestMove(-1)" @keydown.esc.stop="closeSuggest"
        @blur="onQBlur" />
      <ul v-if="suggestOpen" class="suggest">
        <li v-if="suggestNoMatch" class="s-empty">无匹配</li>
        <template v-if="suggestItems.length">
          <li class="s-head">剧集</li>
          <li v-for="(s, i) in suggestItems" :key="'s' + s.id"
            :class="{ on: suggestIdx === i }"
            @mousedown.prevent="pickShow(s)" @mouseenter="suggestIdx = i">
            <span class="s-title">{{ s.title }}</span>
            <span v-if="s.year" class="s-year">({{ s.year }})</span>
          </li>
        </template>
        <template v-if="suggestPersons.length">
          <li class="s-head">演员（按人名搜剧）</li>
          <li v-for="(p, j) in suggestPersons" :key="'p' + p.name"
            :class="{ on: suggestIdx === suggestItems.length + j }"
            @mousedown.prevent="pickPerson(p)"
            @mouseenter="suggestIdx = suggestItems.length + j">
            <span class="s-title">{{ p.name }}</span>
            <span class="s-year">{{ p.count }} 部</span>
          </li>
        </template>
      </ul>
    </div>
    <button @click="applyAndLoad">搜索</button>
    <button @click="clearAll">全部</button>
  </div>
  <div v-if="msg" class="bar">{{ msg }}</div>

  <div class="filters" v-if="hasFacets">
    <div class="frow">
      <span class="flabel">类型</span>
      <span v-for="g in facets.genres" :key="g.value"
        :class="['chip', { on: sel.genres.includes(g.value) }]"
        @click="toggle('genres', g.value)">{{ g.value }} {{ g.count }}</span>
    </div>
    <div class="frow">
      <span class="flabel">产地</span>
      <span v-for="r in facets.regions" :key="r.value"
        :class="['chip', { on: sel.regions.includes(r.value) }]"
        @click="toggle('regions', r.value)">{{ r.value }} {{ r.count }}</span>
      <span class="fhint">facet内OR、跨维度AND；选了具体国家时大区自动让位；计数为「全库口径」，不随筛选变化</span>
    </div>
    <div class="frow" v-if="facets.countries.length">
      <span class="flabel">国家/地区</span>
      <span v-for="c in facets.countries" :key="c.code || 'unknown'"
        :class="['chip', { on: sel.countries.includes(c.code || '未知') }]"
        @click="toggle('countries', c.code || '未知')">{{ c.name }} {{ c.count }}</span>
    </div>
    <div class="frow">
      <span class="flabel">年代</span>
      <span v-for="d in facets.decades" :key="d.value"
        :class="['chip', { on: sel.decades.includes(String(d.value)) }]"
        @click="toggle('decades', String(d.value))">{{ d.value }}s {{ d.count }}</span>
      <select v-model="yearPick" @change="pickYear">
        <option value="">年份…</option>
        <option v-for="y in facets.years" :key="y.value" :value="y.value">{{ y.value }} ({{ y.count }})</option>
      </select>
      <span v-for="y in sel.years" :key="y" class="chip on" @click="toggle('years', y)">{{ y }} ×</span>
      <span v-if="sel.decades.length" class="fhint">年代与年份叠加为AND（如2020s＋2025＝2025）</span>
    </div>
    <div class="frow" v-if="facets.tags.length">
      <span class="flabel">标签</span>
      <span v-for="t in facets.tags" :key="t.value"
        :class="['chip', 'tag', { on: sel.tags.includes(t.value) }]"
        @click="toggle('tags', t.value)">{{ t.value }} {{ t.count }}</span>
      <span class="fhint">标签多选为AND（逐个收窄）</span>
    </div>
    <div class="frow" v-if="facets.status.length">
      <span class="flabel">状态</span>
      <span v-for="st in facets.status" :key="st.value"
        :class="['chip', { on: sel.status.includes(st.value) }]"
        @click="toggle('status', st.value)">{{ statusLabel(st.value) }} {{ st.count }}</span>
    </div>
    <div class="frow">
      <span class="flabel">评分</span>
      <select v-model="sel.ratingSource" @change="applyAndLoad">
        <option value="tmdb">TMDB</option>
        <option value="custom">自评</option>
      </select>
      <span v-for="s in [9, 8, 7, 6]" :key="s"
        :class="['chip', { on: sel.rating === s, off: ratingCount(s) === 0 }]"
        @click="pickRating(s)">{{ s }}分以上 {{ ratingCount(s) }}</span>
      <span class="fhint">单选；未评分的不计入</span>
    </div>
    <div class="frow">
      <span class="flabel">观看</span>
      <span :class="['chip', { on: sel.watched === 1 }]" @click="pickWatched(1)">已看完 {{ watchedCounts.watched }}</span>
      <span :class="['chip', { on: sel.watched === 0 }]" @click="pickWatched(0)">未看完 {{ watchedCounts.unwatched }}</span>
      <span class="fhint">整剧口径：全部集标已看才算看完</span>
    </div>
    <div class="frow" v-if="activeCount">
      <span class="fhint">已选 {{ activeCount }} 项 · 已显示 {{ items.length }} 部<span v-if="hasMore">（还有更多）</span></span>
      <button @click="clearFilters">清空筛选</button>
    </div>
  </div>

  <ContinueWatchingRow v-if="showContinue" ref="cwRef" kind="tv" :media-library-id="curMediaId"
    @open="openShow" @resume="resume" />

  <div v-if="items.length" class="wall-head">
    <h3>全部剧集 <span class="wall-count">{{ wallCountText }}</span></h3>
    <div class="wall-sort">
      <span class="flabel">排序</span>
      <span v-for="s in WALL_SORTS" :key="s.key"
        :class="['chip', { on: sort.key === s.key }]"
        :title="s.key === 'rating' ? '按上面「评分」来源的分数排序' : `按${s.label}排序`"
        @click="pickSort(s.key)">{{ s.label }}<template v-if="sort.key === s.key"> {{ sort.order === 'asc' ? '↑' : '↓' }}</template></span>
      <span class="fhint">默认按入库时间；搜索时仍按所选排序</span>
    </div>
  </div>

  <EmptyState v-if="!items.length && firstLoaded && !loadError"
    :text="(q.trim() || activeCount) ? '没有符合条件的剧集。' : '当前媒体库还没有剧集。在设置页「媒体库」对应媒体库下添加一个「剧集」类型的视频库并扫描，再到「库工具 → 剧集刮削」补元数据即可。'">
    <button v-if="q.trim() || activeCount" @click="clearAll">清空回到全部</button>
  </EmptyState>
  <EmptyState v-if="loadError" :text="loadError" />

  <div class="grid">
    <div v-for="s in items" :key="s.id" class="card show-card" @click="openShow(s.id)">
      <div class="poster-wrap">
        <img v-if="s.poster_path" :src="posterUrl(s.poster_path)" loading="lazy"
          :alt="s.title || '剧集'" />
        <div v-else class="no-poster" aria-hidden="true">{{ (s.title || '?').slice(0, 1) }}</div>
        <span v-if="s.watched_count" class="seen">{{ s.watched_count }}/{{ s.episode_count }}</span>
        <span v-if="s.needs_review" class="review">待确认</span>
        <ScoreBadge :score="s.tmdb_rating" source="tmdb" />
      </div>
      <div class="t">{{ s.title }}<span v-if="s.year" class="yr"> ({{ s.year }})</span></div>
      <div class="t fhint">
        {{ s.season_count }} 季 · {{ s.episode_count }} 集
        <span v-if="s.status"> · {{ statusText(s.status) }}</span>
      </div>
    </div>
  </div>

  <div ref="loadSentinel" class="load-more">
    <button v-if="hasMore" @click="loadMore" :disabled="loadingMore">{{ loadingMore ? '加载中…' : '加载更多' }}</button>
    <span v-else-if="items.length" class="fhint">已全部加载（{{ items.length }} 部）</span>
  </div>

  <PlayerModal v-if="playing" :key="'episode:' + playing.id" :version-id="playing.id" :title="playing.label"
    kind="episode" @close="playing = null" @watched="onWatched" @ended="onWatched" />
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, posterUrl } from '../api.js'
import { currentMediaId, mediaParam, switchLib, switchMedia,
         loadLibs, onLibChange } from '../libraries.js'
import { WALL_SORTS, loadWallSort, normalizeWallSort, saveWallSort,
         toggleWallSort } from '../wallSort.js'
import { buildTvParams, countTvActive, defaultTvSel, normalizeTvSel,
         statusLabel, statusText } from '../tvWall.js'
import ContinueWatchingRow from '../components/ContinueWatchingRow.vue'
import PlayerModal from '../components/PlayerModal.vue'
import ScoreBadge from '../components/ScoreBadge.vue'
import EmptyState from '../components/EmptyState.vue'

const route = useRoute()
const router = useRouter()

const q = ref('')
const suggestOpen = ref(false)
const suggestItems = ref([])
const suggestPersons = ref([])
const suggestNoMatch = ref(false)
const suggestIdx = ref(-1)
let suggestTimer = null
let suggestSeq = 0
let composing = false
const loadSentinel = ref(null)
let loadIO = null
const items = ref([])
const msg = ref('')
const loadError = ref('')
const PAGE = 60
const hasMore = ref(false)
const loadingMore = ref(false)
let loading = false
let loadSeq = 0
const facets = ref({ genres: [], regions: [], countries: [], years: [], decades: [],
  tags: [], status: [], watched: { watched: 0, unwatched: 0 },
  ratings: { tmdb: [], custom: [] } })
const sel = ref(defaultTvSel())
const yearPick = ref('')

// 海报墙排序：与电影墙同习惯（localStorage 记忆 + URL ?sort=&order= 可分享）
const sort = ref(loadWallSort(localStorage))
const curMediaId = ref(currentMediaId())
const showContinue = computed(() => !activeCount.value && !q.value.trim())
const playing = ref(null)
const cwRef = ref(null)
const firstLoaded = ref(false)

function pickSort(key) {
  sort.value = toggleWallSort(sort.value, key)
  saveWallSort(localStorage, sort.value)
  applyAndLoad()
}

const watchedCounts = computed(() => facets.value.watched || { watched: 0, unwatched: 0 })

const wallCountText = computed(() => {
  if (q.value.trim() || activeCount.value) {
    return `筛选出 ${items.value.length}${hasMore.value ? '+' : ''} 部`
  }
  const w = watchedCounts.value
  const total = (w.watched || 0) + (w.unwatched || 0)
  return total ? `共 ${total} 部` : ''
})

function ratingCount(s) {
  const arr = (facets.value.ratings || {})[sel.value.ratingSource] || []
  const hit = arr.find(x => x.min === s)
  return hit ? hit.count : 0
}
function pickRating(s) {
  sel.value.rating = (sel.value.rating === s) ? null : s
  applyAndLoad()
}

const hasFacets = computed(() =>
  facets.value.genres.length || facets.value.regions.length ||
  facets.value.years.length || facets.value.status.length)
const activeCount = computed(() => countTvActive(sel.value))

function pickWatched(v) {
  sel.value.watched = (sel.value.watched === v) ? null : v
  applyAndLoad()
}

function toggle(key, v) {
  const a = sel.value[key]
  const i = a.indexOf(v)
  if (i >= 0) a.splice(i, 1)
  else a.push(v)
  applyAndLoad()
}
function pickYear() {
  if (yearPick.value && !sel.value.years.includes(String(yearPick.value))) {
    sel.value.years.push(String(yearPick.value))
  }
  yearPick.value = ''
  applyAndLoad()
}
function _qNorm(query) {
  const parts = []
  for (const k of Object.keys(query || {}).sort()) {
    const v = query[k]
    parts.push(k + '=' + (Array.isArray(v) ? v.join(',') : String(v ?? '')))
  }
  return parts.join('&')
}
function syncUrl() {
  const query = {}
  if (q.value.trim()) query.q = q.value.trim()
  if (sel.value.genres.length) query.genre = sel.value.genres.join(',')
  if (sel.value.regions.length) query.region = sel.value.regions.join(',')
  if (sel.value.countries.length) query.country = sel.value.countries.join(',')
  if (sel.value.years.length) query.year = sel.value.years.join(',')
  if (sel.value.decades.length) query.decade = sel.value.decades.join(',')
  if (sel.value.tags.length) query.tag = sel.value.tags.join(',')
  if (sel.value.status.length) query.status = sel.value.status.join(',')
  if (sel.value.watched != null) query.watched = String(sel.value.watched)
  if (sel.value.rating != null) query.min_rating = String(sel.value.rating)
  if (sel.value.rating != null || sort.value.key === 'rating') {
    if (sel.value.ratingSource !== 'tmdb') query.rating_source = sel.value.ratingSource
  }
  if (sort.value.key !== 'added' || sort.value.order !== 'desc') {
    query.sort = sort.value.key
    query.order = sort.value.order
  }
  const lp = mediaParam()
  if (lp != null) query.media = String(lp)
  const changed = _qNorm(query) !== _qNorm(route.query)
  if (changed) router.replace({ path: '/tv', query })
  return changed   // 变则交给 route.query watcher 加载；未变由调用方显式刷新
}
function readUrl() {
  const s = (v) => v ? String(v).split(',').map(x => x.trim()).filter(Boolean) : []
  const src = String(route.query.rating_source || 'tmdb')
  const wq = Array.isArray(route.query.watched) ? route.query.watched[0] : route.query.watched
  const qmedia = Array.isArray(route.query.media) ? route.query.media[0] : route.query.media
  if (qmedia != null && qmedia !== '' && Number(qmedia) !== currentMediaId()) {
    try { switchMedia(Number(qmedia)) } catch (e) { /* 未知媒体库忽略 */ }
  }
  const qlib = Array.isArray(route.query.lib) ? route.query.lib[0] : route.query.lib
  if (qmedia == null || qmedia === '') {
    if (qlib != null && qlib !== '') {
      try { switchLib(Number(qlib)) } catch (e) { /* 未知库忽略 */ }
    }
  }
  q.value = route.query.q || ''
  const qs = Array.isArray(route.query.sort) ? route.query.sort[0] : route.query.sort
  if (qs != null && qs !== '') {
    const qo = Array.isArray(route.query.order) ? route.query.order[0] : route.query.order
    sort.value = normalizeWallSort({ key: qs, order: qo })
  } else {
    sort.value = loadWallSort(localStorage)
  }
  sel.value = normalizeTvSel({
    genres: s(route.query.genre),
    regions: s(route.query.region),
    countries: s(route.query.country),
    years: s(route.query.year),
    decades: s(route.query.decade),
    tags: s(route.query.tag),
    status: s(route.query.status),
    watched: wq != null && wq !== '' ? Number(wq) : null,
    rating: route.query.min_rating != null && route.query.min_rating !== '' ? Number(route.query.min_rating) : null,
    ratingSource: ['tmdb', 'custom'].includes(src) ? src : 'tmdb',
  })
  curMediaId.value = currentMediaId()
}
async function load() {
  const seq = ++loadSeq
  loading = true
  msg.value = ''
  try {
    const d = await api('/api/tv/shows?' + buildTvParams({
      q: q.value, sel: sel.value, sort: sort.value,
      mediaId: mediaParam(), limit: PAGE, offset: 0,
    }))
    if (seq !== loadSeq) return          // 更新的筛选已接管，丢弃过期回包
    items.value = d.items || []
    hasMore.value = !!d.has_more
    loadError.value = ''
  } catch (e) {
    if (seq === loadSeq) loadError.value = '加载失败：' + e.message
  } finally {
    if (seq === loadSeq) loading = false
  }
}
async function loadMore() {
  if (!hasMore.value || loading) return
  const seq = ++loadSeq
  loading = true
  loadingMore.value = true
  try {
    const d = await api('/api/tv/shows?' + buildTvParams({
      q: q.value, sel: sel.value, sort: sort.value,
      mediaId: mediaParam(), limit: PAGE, offset: items.value.length,
    }))
    if (seq !== loadSeq) return
    const known = new Set(items.value.map(m => m.id))
    for (const m of (d.items || [])) {
      if (!known.has(m.id)) items.value.push(m)
    }
    hasMore.value = !!d.has_more
    loadError.value = ''
  } catch (e) {
    if (seq === loadSeq) loadError.value = '加载更多失败：' + e.message
  } finally {
    if (seq === loadSeq) { loading = false; loadingMore.value = false }
  }
}
async function applyAndLoad() {
  // URL 变了 → route.query watcher 统一加载；没变才显式刷新（与电影墙同逻辑，防双发）
  if (!syncUrl()) await load()
}
// 搜索联想：输入防抖拉本地库（剧名 + 演职员人名），↑↓选择 / Enter选中 / Esc关闭
function onQInput(e) {
  if (composing || (e && e.isComposing)) return
  scheduleSuggest()
}
function onCompositionEnd() {
  composing = false
  scheduleSuggest()
}
function scheduleSuggest() {
  clearTimeout(suggestTimer)
  const term = q.value.trim()
  if (!term) {
    closeSuggest()
    return
  }
  suggestTimer = setTimeout(fetchSuggest, 180)
}
async function fetchSuggest() {
  const term = q.value.trim()
  if (!term) return
  const seq = ++suggestSeq
  try {
    const d = await api('/api/tv/suggest?q=' + encodeURIComponent(term) + '&limit=8'
      + (mediaParam() != null ? '&media_library=' + mediaParam() : ''))
    if (seq !== suggestSeq) return
    suggestItems.value = (d && d.items) || []
    suggestPersons.value = (d && d.persons) || []
    suggestNoMatch.value = !suggestItems.value.length && !suggestPersons.value.length
    suggestIdx.value = -1
    suggestOpen.value = true
  } catch (e) {
    if (seq === suggestSeq) closeSuggest()
  }
}
function suggestRow(idx) {
  if (idx < 0) return null
  if (idx < suggestItems.value.length) return { kind: 'show', v: suggestItems.value[idx] }
  const j = idx - suggestItems.value.length
  if (j < suggestPersons.value.length) return { kind: 'person', v: suggestPersons.value[j] }
  return null
}
function suggestMove(d) {
  if (!suggestOpen.value) return
  const n = suggestItems.value.length + suggestPersons.value.length
  if (!n) return
  let i = suggestIdx.value + d
  if (i < 0) i = n - 1
  if (i >= n) i = 0
  suggestIdx.value = i
}
function pickRow(row) {
  clearTimeout(suggestTimer)
  suggestSeq++
  // 剧集无人物页：选剧/选演员都是回填搜索框按名过滤（与电影跳人物页不同）
  q.value = row.kind === 'show' ? row.v.title : row.v.name
  closeSuggest()
  applyAndLoad()
}
function pickShow(s) { pickRow({ kind: 'show', v: s }) }
function pickPerson(p) { pickRow({ kind: 'person', v: p }) }
function closeSuggest() {
  suggestOpen.value = false
  suggestItems.value = []
  suggestPersons.value = []
  suggestNoMatch.value = false
  suggestIdx.value = -1
}
function onQBlur() {
  setTimeout(closeSuggest, 120)
}
function onSearchEnter(e) {
  if (e && (e.isComposing || e.keyCode === 229)) return
  clearTimeout(suggestTimer)
  suggestSeq++
  const row = suggestOpen.value ? suggestRow(suggestIdx.value) : null
  if (row) {
    pickRow(row)
    return
  }
  closeSuggest()
  applyAndLoad()
}
async function clearAll() {
  q.value = ''
  sel.value = defaultTvSel()
  if (!syncUrl()) await load()
}
async function clearFilters() {
  sel.value = defaultTvSel()
  await applyAndLoad()
}
async function loadFacets() {
  const lq = mediaParam() != null ? ('?media_library=' + mediaParam()) : ''
  try { facets.value = await api('/api/tv/facets' + lq) } catch (e) { /* 库空时忽略 */ }
}
function openShow(id) { router.push('/tv/' + id) }
function resume(m) {
  const p = m && m.progress
  if (!p || !p.version_id) return
  playing.value = { id: p.version_id, label: `${m.title}${m.subtitle ? ' ' + m.subtitle : ''}` }
}
async function onWatched() {
  const id = playing.value && playing.value.id
  if (id) {
    try {
      await api(`/api/tv/episodes/${id}/watched`, {
        method: 'POST', body: JSON.stringify({ watched: true }) })
    } catch (e) { /* 静默：墙不因此报错 */ }
  }
  if (cwRef.value && cwRef.value.reload) cwRef.value.reload()
  await loadFacets()
  await load()
}

let unsubLib = null
onMounted(async () => {
  try { await loadLibs(api) } catch (e) { /* 后端不可用时按单库旧行为 */ }
  readUrl()
  await loadFacets()
  await load()
  firstLoaded.value = true
  unsubLib = onLibChange(() => { readUrl(); loadFacets(); load() })
  try {
    if (window.IntersectionObserver && loadSentinel.value) {
      loadIO = new IntersectionObserver((entries) => {
        if (entries.some(e => e.isIntersecting)) loadMore()
      }, { rootMargin: '600px 0px' })
      loadIO.observe(loadSentinel.value)
    }
  } catch (e) { /* 不支持则只用按钮 */ }
})
onUnmounted(() => {
  clearTimeout(suggestTimer)
  if (unsubLib) { try { unsubLib() } catch (e) { /* 忽略 */ } unsubLib = null }
  if (loadIO) { try { loadIO.disconnect() } catch (e) { /* 忽略 */ } loadIO = null }
})
watch(() => route.query, () => { readUrl(); load() })
</script>

<style scoped>
.show-card { cursor: pointer; }
.wall-head {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  margin: 16px 12px 0; padding-top: 12px; border-top: 1px solid #2e2e2e;
}
.wall-head h3 { margin: 0; font-size: 1.0625rem; color: #ddd; }
.wall-count { color: #777; font-size: 0.8125rem; font-weight: normal; margin-left: 4px; }
.wall-sort { margin-left: auto; display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.wall-sort .flabel { min-width: 0; }
.filters { padding: 0 12px; display: flex; flex-direction: column; gap: 6px; }
.frow { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.flabel { color: #888; font-size: 0.8125rem; min-width: 56px; }
.chip { font-size: 0.8125rem; padding: 4px 10px; border: 1px solid #444; border-radius: 999px; cursor: pointer; background: #1c1c1c; }
.chip.on { border-color: #e50914; color: #ff8a8a; }
.chip.tag { border-style: dashed; }
.chip.off { opacity: .45; }
.fhint { color: #777; font-size: 0.75rem; }
.q-wrap { position: relative; flex: 0 1 260px; }
.q-wrap input { width: 100%; box-sizing: border-box; }
.suggest {
  position: absolute; top: calc(100% + 4px); left: 0; right: 0; z-index: 60;
  list-style: none; margin: 0; padding: 4px 0; max-height: 320px; overflow: auto;
  background: #1c1c1c; border: 1px solid #444; border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0,0,0,.55);
}
.suggest li { padding: 6px 12px; cursor: pointer; display: flex; gap: 6px; align-items: baseline; }
.suggest li.on { background: #333; }
.suggest .s-head { color: #777; font-size: 0.75rem; padding: 6px 12px 2px; cursor: default; }
.suggest .s-title { color: #eee; }
.suggest .s-year { color: #888; font-size: 0.8125rem; }
.suggest .s-empty { color: #777; cursor: default; }
.no-poster { width: 100%; aspect-ratio: 2/3; display: flex; align-items: center; justify-content: center; background: #242424; color: #555; font-size: 2.5rem; font-weight: bold; user-select: none; }
.seen, .review {
  position: absolute; top: 6px; font-size: 0.75rem; padding: 2px 8px;
  border-radius: 999px; background: rgba(0,0,0,.72); color: #7ed321;
}
.seen { left: 6px; }
.review { right: 6px; color: #ffb300; }
.yr { color: #888; font-size: 0.75rem; }
.fhint { color: #777; font-size: 0.8125rem; }
.load-more { display: flex; align-items: center; justify-content: center; gap: 10px; padding: 10px 12px 22px; }
.warn-text { color: #e0a63c; }
</style>
