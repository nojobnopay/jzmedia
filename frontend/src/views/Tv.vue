<template>
  <div class="browse-page">
  <header class="browse-heading">
    <h1>剧集</h1>
    <div class="browse-actions">
      <router-link :to="toolsLink" class="manage-link"><AppIcon name="organize" :size="18" />扫描与整理<AppIcon name="chevron-right" :size="16" /></router-link>
      <ActionMenu label="添加剧集"><JzButton @click="scanOpen = !scanOpen" type="button" variant="ghost" icon="scan">扫描新文件</JzButton><JzButton @click="upDlg = true" type="button" variant="ghost" icon="upload">上传文件</JzButton></ActionMenu>
    </div>
  </header>
  <BrowseToolbar id="tv-browse" v-model="q" label="搜索剧集" placeholder="搜剧名 / 演员"
    item-heading="剧集" :suggest-open="suggestOpen" :suggest-no-match="suggestNoMatch"
    :suggest-items="suggestItems" :suggest-persons="suggestPersons" :suggest-idx="suggestIdx"
    v-model:ai-open="aiSearchOpen"
    @input="onQInput" @compositionstart="composing = true" @compositionend="onCompositionEnd"
    @enter="onSearchEnter" @move="suggestMove" @close="closeSuggest" @blur="onQBlur"
    @pick-item="pickShow" @pick-person="pickPerson" @hover="suggestIdx = $event" @search="applyAndLoad">
    <template #filters>
      <BrowseFilters :model-value="sel" :facets="facets" kind="tv" v-model:open="filtersOpen"
        :scope-label="filterScope" :scope-key="curMediaId" :context-key="route.fullPath"
        :loading="facetsLoading" :error="facetsError" @retry="loadFacets"
        @open="closeSuggest" @apply="applyFilters" />
    </template>
  </BrowseToolbar>
  <div id="tv-browse-ai"><AiSearchPanel v-if="aiSearchOpen" kind="tv" :query="q" :media-library-id="curMediaId" @apply="applyAiSearch" @close="aiSearchOpen = false" /></div>
  <ScanAction v-if="scanOpen" kind="tv" @done="onAdded" />
  <UploadDialog v-if="upDlg" kind="tv" @close="upDlg = false" @done="onAdded" />
  <p v-if="msg" class="page-feedback" role="status">{{ msg }}</p>

  <BrowseFilterSummary :chips="selectedChips" :query="appliedQuery" fallback-id="tv-browse-input"
    @remove="removeFilter" @clear="clearFilters" @clear-query="clearQuery" />

  <ContinueWatchingRow v-if="showContinue" ref="cwRef" kind="tv" :media-library-id="curMediaId"
    @open="openShow" @resume="resume" />

  <BrowseResultsHeader v-if="items.length" :title="q.trim() || activeCount ? '筛选结果' : '全部剧集'"
    :count="wallCountText" :sort="sort" :options="WALL_SORTS" :rating-source="sel.ratingSource"
    @sort="pickSort" />

  <EmptyState v-if="loading && !items.length || !firstLoaded && !loadError" state="loading" title="正在加载剧集" text="请稍候…" />
  <EmptyState v-else-if="loadError" state="error" title="剧集加载失败" :text="loadError" retry @retry="retryLoad" />
  <EmptyState v-else-if="firstLoaded && !items.length" :state="q.trim() || activeCount ? 'no-results' : 'empty'"
    :title="q.trim() || activeCount ? '没有符合条件的剧集' : '当前媒体库还没有剧集'"
    :text="q.trim() || activeCount ? '试试其他剧名，或重置筛选条件。' : '扫描已有文件，或上传剧集开始观看。'">
    <JzButton v-if="q.trim() || activeCount" @click="clearAll" type="button" icon="filter">重置筛选</JzButton>
    <router-link v-else :to="toolsLink"><AppIcon name="organize" :size="18" />扫描与整理<AppIcon name="chevron-right" :size="16" /></router-link>
  </EmptyState>

  <div class="grid" :aria-busy="loading">
    <div v-for="s in items" :key="s.id" :data-browse-id="s.id" class="card show-card" role="link" tabindex="0" @keydown.enter.self="openShow(s.id)" @click="openShow(s.id)">
      <div class="poster-wrap">
        <img v-if="s.poster_path" :src="posterUrl(s.poster_path)" loading="lazy"
          :alt="s.title || '剧集'" />
        <ArtworkPlaceholder v-else class="no-poster" kind="poster" :label="s.title" />
        <span v-if="s.watched_count" class="seen">{{ s.watched_count }}/{{ s.episode_count }}</span>
        <span v-if="!s.tmdb_id" class="review">未匹配</span>
        <span v-else-if="s.needs_review" class="review">待确认</span>
        <ScoreBadge :score="s.tmdb_rating" source="tmdb" />
      </div>
      <div class="t"><span class="card-title" :title="s.title">{{ s.title }}<span v-if="s.year" class="yr"> ({{ s.year }})</span></span></div>
      <div class="t fhint card-meta">
        {{ s.season_count }} 季 · {{ s.episode_count }} 集
        <span v-if="s.status"> · {{ statusText(s.status) }}</span>
      </div>
    </div>
  </div>

  <div ref="loadSentinel" class="load-more">
    <JzButton v-if="hasMore" @click="loadMore" :disabled="loadingMore" type="button" icon="more">{{ loadingMore ? '加载中…' : '加载更多' }}</JzButton>
    <span v-else-if="items.length" class="fhint">已全部加载（{{ items.length }} 部）</span>
  </div>

  <PlayerModal v-if="playing" :key="'episode:' + playing.id" :version-id="playing.id" :title="playing.label"
    kind="episode" @close="playing = null" @watched="onWatched" @ended="onWatched" />
  </div>
</template>

<script setup>
import ArtworkPlaceholder from '../components/ArtworkPlaceholder.vue'

import AppIcon from '../components/AppIcon.vue'

import JzButton from '../components/JzButton.vue'

import BrowseResultsHeader from '../components/BrowseResultsHeader.vue'
import BrowseToolbar from '../components/BrowseToolbar.vue'
import BrowseFilters from '../components/BrowseFilters.vue'
import BrowseFilterSummary from '../components/BrowseFilterSummary.vue'
import { defaultBrowseFilters, normalizeBrowseFilters, filterChips, countBrowseFilters, removeBrowseFilter } from '../browseFilters.js'
import AiSearchPanel from '../components/AiSearchPanel.vue'
import ActionMenu from '../components/ActionMenu.vue'
import ScanAction from '../components/ScanAction.vue'
import UploadDialog from '../components/UploadDialog.vue'

import { browseKey, saveBrowse, readBrowse, captureAnchor, restoreBrowsePosition } from '../browseHistory.js'

import { computed, onMounted, onUnmounted, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router'
import { api, posterUrl } from '../api.js'
import { currentMediaId, preferredVideoLibId, mediaParam, switchLib, switchMedia,
         loadLibs, onLibChange, listMediaLibs } from '../libraries.js'
import { WALL_SORTS, loadWallSort, normalizeWallSort, saveWallSort,
         toggleWallSort } from '../wallSort.js'
import { buildTvParams, statusText } from '../tvWall.js'
import ContinueWatchingRow from '../components/ContinueWatchingRow.vue'
import PlayerModal from '../components/PlayerModal.vue'
import ScoreBadge from '../components/ScoreBadge.vue'
import EmptyState from '../components/EmptyState.vue'

const scanOpen = ref(false)
const upDlg = ref(false)
async function onAdded() { await loadFacets(); await load() }
const filtersOpen = ref(false)
const aiSearchOpen = ref(false)
function applyAiSearch(value) {
  q.value = value.q
  sel.value = normalizeBrowseFilters(value.sel, 'tv')
  sort.value = value.sort
  saveWallSort(localStorage, sort.value)
  closeSuggest()
  filtersOpen.value = false
  aiSearchOpen.value = false
  applyAndLoad()
}
const toolsLink = computed(() => {
  const media = curMediaId.value
  const library = preferredVideoLibId('tv')
  return { path: '/settings', query: library ? { sec: 'sec-libtools', media, library } : { sec: 'sec-libraries', media } }
})
const route = useRoute()
const router = useRouter()
const appliedQuery = computed(() => String(route.query.q || ''))
const filterScope = computed(() => listMediaLibs().find(m => Number(m.id) === curMediaId.value)?.name || '当前媒体库')
const selectedChips = computed(() => filterChips(sel.value, facets.value, 'tv'))
function applyFilters(value) {
  sel.value = normalizeBrowseFilters(value, 'tv')
  closeSuggest()
  applyAndLoad()
}
function removeFilter(chip) { applyFilters(removeBrowseFilter(sel.value, chip, 'tv')) }
function clearQuery() {
  q.value = ''
  closeSuggest()
  applyAndLoad()
}

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
const loading = ref(false)
const loadErrorMore = ref(false)
let loadSeq = 0
let disposed = false
const emptyFacets = () => ({ genres: [], regions: [], countries: [], years: [], decades: [],
  tags: [], status: [], watched: { watched: 0, unwatched: 0 },
  ratings: { tmdb: [], custom: [] } })
const facets = ref(emptyFacets())
const facetsLoading = ref(false)
const facetsError = ref('')
let facetsSeq = 0
let facetsScope

const sel = ref(defaultBrowseFilters('tv'))

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

const activeCount = computed(() => countBrowseFilters(sel.value, 'tv'))

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
  sel.value = normalizeBrowseFilters({
    genres: s(route.query.genre),
    regions: s(route.query.region),
    countries: s(route.query.country),
    years: s(route.query.year),
    decades: s(route.query.decade),
    tags: s(route.query.tag),
    status: s(route.query.status),
    watched: wq != null && wq !== '' ? Number(wq) : null,
    rating: route.query.min_rating != null && route.query.min_rating !== '' ? Number(route.query.min_rating) : null,
    ratingSource: src,
  }, 'tv')
  curMediaId.value = currentMediaId()
}
async function load() {
  const seq = ++loadSeq
  loading.value = true
  loadingMore.value = false
  loadError.value = ''
  loadErrorMore.value = false
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
    if (seq === loadSeq) loading.value = false
  }
}
async function loadMore() {
  if (!hasMore.value || loading.value) return
  const seq = ++loadSeq
  loading.value = true
  loadingMore.value = true
  loadError.value = ''
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
    if (seq === loadSeq) { loadError.value = '加载更多失败：' + e.message; loadErrorMore.value = true }
  } finally {
    if (seq === loadSeq) { loading.value = false; loadingMore.value = false }
  }
}
function retryLoad() { return loadErrorMore.value ? loadMore() : load() }
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
  sel.value = defaultBrowseFilters('tv')
  if (!syncUrl()) await load()
}
async function clearFilters() {
  closeSuggest()
  sel.value = defaultBrowseFilters('tv')
  await applyAndLoad()
}
async function loadFacets() {
  const seq = ++facetsSeq
  const scope = currentMediaId()
  if (facetsScope !== scope) {
    facets.value = emptyFacets()
    facetsScope = scope
  }
  facetsLoading.value = true
  facetsError.value = ''
  const lq = mediaParam() != null ? ('?media_library=' + mediaParam()) : ''
  try {
    const data = await api('/api/tv/facets' + lq)
    if (!disposed && seq === facetsSeq) facets.value = data
  } catch (e) {
    if (!disposed && seq === facetsSeq) facetsError.value = '筛选选项加载失败：' + e.message
  } finally {
    if (!disposed && seq === facetsSeq) facetsLoading.value = false
  }
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

function historyKey() { return browseKey('/tv', currentMediaId(), buildTvParams({ q: q.value, sel: sel.value, sort: sort.value, mediaId: mediaParam() })) }
onBeforeRouteLeave(() => {
  if (!firstLoaded.value) return
  saveBrowse(historyKey(), { path: '/tv', mediaId: currentMediaId(), query: route.query,
    items: items.value, hasMore: hasMore.value, filtersOpen: false,
    anchor: captureAnchor(), scrollTop: window.scrollY })
})
async function restoreWall() {
  const snapshot = readBrowse(historyKey())
  if (snapshot) {
    items.value = snapshot.items
    hasMore.value = snapshot.hasMore
    filtersOpen.value = false
  } else await load()
  firstLoaded.value = true
  await nextTick()
  await cwRef.value?.ready()
  if (disposed || route.path !== '/tv') return
  await nextTick()
  await new Promise(resolve => requestAnimationFrame(resolve))
  restoreBrowsePosition(snapshot)
  if (snapshot) refreshSnapshot(snapshot)
}

// Restore immediately, then refresh the same number of pages so edits/deletions
// made in detail are visible without discarding the user's browsing position.
async function refreshSnapshot(snapshot) {
  const seq = ++loadSeq
  loading.value = true
  try {
    const fresh = []
    let more = false
    for (let offset = 0; offset < snapshot.items.length; offset += PAGE) {
      const d = await api('/api/tv/shows?' + buildTvParams({
        q: q.value, sel: sel.value, sort: sort.value, mediaId: mediaParam(), limit: PAGE, offset,
      }))
      if (seq !== loadSeq) return
      fresh.push(...(d.items || []))
      more = !!d.has_more
      if (!more || !d.items?.length) break
    }
    const position = { anchor: captureAnchor(), scrollTop: window.scrollY }
    items.value = fresh
    hasMore.value = more
    loadError.value = ''
    await nextTick()
    if (seq === loadSeq) restoreBrowsePosition(position)
  } catch (e) { if (seq === loadSeq) loadError.value = '刷新列表失败：' + e.message }
  finally { if (seq === loadSeq) loading.value = false }
}

let unsubLib = null
onMounted(async () => {
  try { await loadLibs(api) } catch (e) { /* 后端不可用时按单库旧行为 */ }
  if (disposed) return
  readUrl()
  const initial = Promise.all([loadFacets(), restoreWall()])
  if (disposed) return
  await initial
  if (disposed) return
  unsubLib = onLibChange(() => { if (route.path !== '/tv') return; curMediaId.value = currentMediaId(); loadFacets(); load() })
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
  disposed = true
  loadSeq++
  facetsSeq++
  clearTimeout(suggestTimer)
  if (unsubLib) { try { unsubLib() } catch (e) { /* 忽略 */ } unsubLib = null }
  if (loadIO) { try { loadIO.disconnect() } catch (e) { /* 忽略 */ } loadIO = null }
})
watch(() => route.query, () => {
  if (route.path !== '/tv') return
  readUrl()
  // Facets describe the whole media library, so applying filters need not reload them.
  if (facetsScope !== currentMediaId()) loadFacets()
  load()
})
</script>

<style scoped>
.show-card { cursor: pointer; }

.seen, .review {
  position: absolute; top: 6px; font-size: 0.75rem; padding: 2px 8px;
  border-radius: 999px; background: var(--jz-overlay); color: var(--jz-green);
}
.seen { left: 6px; }
.review { right: 6px; color: var(--jz-warn); }
.yr { color: var(--jz-text-dim); font-size: 0.75rem; }

.warn-text { color: var(--jz-warn); }
</style>
