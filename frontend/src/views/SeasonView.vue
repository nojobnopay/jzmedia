<template>
  <div v-if="s" class="tv-page media-detail season-detail">
    <nav class="crumbs" aria-label="当前位置">
      <router-link to="/tv">剧集</router-link><span class="dim"> / </span>
      <router-link :to="'/tv/' + s.show_id">{{ s.show_title }}</router-link>
      <span class="dim"> / </span>
      <span>{{ s.name || seasonLabel(s.season) }}</span>
    </nav>
    <div class="hero media-hero">
      <MediaBackdrop :src="s.show_backdrop_path ? posterUrl(s.show_backdrop_path) : ''" />
      <div class="hero-inner">
        <img v-if="seasonPoster && !posterFailed" class="hero-poster" :src="seasonPoster"
          :alt="s.name || seasonLabel(s.season)" @error="posterFailed = true" />
        <ArtworkPlaceholder v-else class="hero-poster hero-no-poster" kind="poster" :label="seasonLabel(s.season)" />
        <div class="hero-body">
          <div class="hero-heading">
            <h1>{{ s.show_title }}<span v-if="s.show_year" class="dim"> ({{ s.show_year }})</span></h1>
            <div class="meta">
              <span>{{ s.name || seasonLabel(s.season) }}</span>
              <span v-if="s.air_date">{{ s.air_date }}</span>
              <span v-if="collectionSeason">{{ collectionCountText(collectionSeason) }}</span>
              <span v-if="collectionSeason">{{ airingLabel(collectionSeason) }}</span>
              <span v-if="hasLocalFiles">本页 {{ s.distinct_count ?? s.episode_count }} 集 · {{ (s.versions || []).length || 1 }} 版本</span>
              <span v-if="hasLocalFiles">本页 {{ s.watched_count }}/{{ s.episode_count }} 已看</span>
            </div>
          </div>
          <div v-if="hasLocalFiles" class="acts">
            <JzButton v-if="s.next_episode" class="primary" :disabled="!s.next_episode.exists"
              @click="play(s.next_episode)" type="button" variant="primary">
              <PlayerIcon name="play" :size="20" /> {{ s.next_episode.progress ? '继续观看' : '播放本季' }} {{ epNo(s.next_episode) }}
            </JzButton>
            <JzButton v-if="eps.length" :disabled="busy" @click="toggleSeasonWatched" type="button">
              {{ seasonDone ? '标记本季全部版本未看' : '标记本季全部版本已看' }}
            </JzButton>
            <ActionMenu>
              <JzButton :disabled="verifying" @click="verifyExists" type="button" variant="ghost" icon="eye">{{ verifying ? '检查中…' : '检查文件是否可用' }}</JzButton>
            </ActionMenu>
            <span v-if="busy" class="dim">处理中…</span>
          </div>
          <p v-if="!hasLocalFiles" class="season-collection-note">当前视频库尚未收藏本季，可查看分集资料或其他视频库中的文件。</p>
          <p v-if="collectionExplanation(collectionSeason)" class="season-collection-note collection-explanation">{{ collectionExplanation(collectionSeason) }}</p>
          <div v-if="otherSeasonSources.length" class="season-source-links">
            <router-link v-for="source in otherSeasonSources" :key="sourceSeasonPath(source)" :to="sourceSeasonPath(source)">在 {{ source.library_name || '其他视频库' }} 中查看 · {{ seasonLabel(source.season) }}</router-link>
          </div>
          <MediaOverview :text="s.overview || collectionSeason?.overview || catalog?.overview || ''" />
          <p v-if="msg" class="page-feedback" role="status">{{ msg }}</p>
        </div>
      </div>
    </div>
    <div class="season-list-heading"><h2 class="section-heading">{{ seasonTab === 'local' ? '选择剧集' : '全部分集' }} <span v-if="seasonTab === 'local'">{{ s.distinct_count ?? s.episode_count }} 集</span></h2>
      <label v-if="seasonTab === 'local' && (s.versions || []).length > 1">播放版本 <select v-model="selectedVersion" @change="load">
        <option value="">全部版本</option><option v-for="v in s.versions" :key="v.version" :value="String(v.version)">V{{ v.version }} · {{ v.distinct }} 集</option>
      </select></label></div>
    <div class="season-list-views" role="group" aria-label="分集显示范围">
      <JzButton :aria-pressed="seasonTab === 'local'" @click="seasonTab = 'local'">已收藏</JzButton>
      <JzButton :aria-pressed="seasonTab === 'catalog'" @click="showCatalog">全部分集</JzButton>
    </div>
    <TvSeasonCatalog v-if="seasonTab === 'catalog'" :data="catalog" :loading="catalogLoading" :error="catalogError" @retry="loadCatalog(true)" />
    <template v-else>
    <p class="season-collection-note">本页只播放和标记当前剧集记录中的文件；其他视频库的收藏可从上方来源入口查看。</p>
    <EmptyState v-if="loadError" state="error" title="剧季加载失败" :text="loadError" retry @retry="retryLoad" />
    <EmptyState v-else-if="loading" state="loading" title="正在加载分集" text="请稍候…" />
    <EmptyState v-else-if="!eps.length" :state="selectedVersion ? 'no-results' : 'empty'"
      :title="selectedVersion ? '这个版本还没有分集' : '本季还没有分集'" text="可选择其他版本，或扫描视频库更新分集列表。" />
    <div class="grid ep-grid">
      <div v-for="e in eps" :key="e.id" class="card ep-card" role="link" tabindex="0" @keydown.enter.self="openEpisode(e.id)" @click="openEpisode(e.id)">
        <div class="still-wrap">
          <img v-if="e.still_path" :src="stillUrl(e)" loading="lazy" alt=""
            @error="e.still_path = ''" />
          <div v-else class="still-none" aria-hidden="true">{{ epNo(e) }}</div>
          <button v-if="e.exists" class="poster-play"
            :aria-label="'播放 ' + epNo(e)" :title="'播放 ' + epNo(e)"
            @click.stop="play(e)"><PlayerIcon name="play" :size="24" /></button>
          <span v-if="e.watched" class="ep-done"><AppIcon name="check" :size="14" />已看</span>
          <span v-else-if="e.progress" class="ep-left">{{ fmtRemaining(e.progress.remaining_sec) }}</span>
          <div v-if="!e.watched && e.progress" class="ep-bar" aria-hidden="true">
            <div class="ep-bar-in" :style="{ width: progressWidth(e.progress) }"></div>
          </div>
        </div>
        <div class="t">
          <div class="episode-title">
            <span class="ep-no">{{ epNo(e) }}</span><span v-if="episodeVersion(e) > 1" class="ver-badge">V{{ episodeVersion(e) }}</span>
            {{ e.title || '（未匹配集名）' }}
            <span v-if="e.needs_review" class="review-badge">未匹配</span>
            <span v-if="e.local_only" class="local-badge">本地集</span>
          </div>
          <span v-if="e.air_date" class="meta episode-date">{{ e.air_date }}</span>
          <span v-if="e.overview" class="meta episode-summary"> {{ e.overview.slice(0, 60) }}</span>
        </div>
      </div>
    </div>
    <div ref="sentinel" class="more-sentinel" aria-hidden="true"></div>
    <div v-if="hasMore" class="bar more-bar">
      <JzButton :disabled="loadingMore" @click="loadMore" type="button" icon="more">
        {{ loadingMore ? '加载中…' : `加载更多（${eps.length}/${s.total || s.episode_count}）` }}
      </JzButton>
    </div>
    <div v-else-if="eps.length" class="bar dim small">已加载全部 {{ eps.length }} 个文件</div>
    </template>
    <CastWall :cast="s.cast || []" :original-language="s.original_language || ''"
      :subtitle="s.cast_source === 'season' ? seasonLabel(s.season) : '全剧'" />
  </div>
  <EmptyState v-else :state="loadError ? 'error' : 'loading'" :title="loadError ? '剧季加载失败' : '正在加载剧季'"
    :text="loadError || '请稍候…'" :retry="!!loadError" @retry="load">
    <router-link v-if="loadError" to="/tv">返回剧集列表</router-link>
  </EmptyState>
  <PlayerModal v-if="playing" :key="'episode:' + playing.id"
    :version-id="playing.id" :title="playing.label" kind="episode"
    @close="playing = null" @watched="onWatched" @ended="onEnded" />
</template>

<script setup>
import ArtworkPlaceholder from '../components/ArtworkPlaceholder.vue'

import AppIcon from '../components/AppIcon.vue'

import JzButton from '../components/JzButton.vue'

import EmptyState from '../components/EmptyState.vue'
import { followingPlayback } from '../episodePlayback.js'
import PlayerIcon from '../components/PlayerIcon.vue'

import MediaBackdrop from '../components/MediaBackdrop.vue'
import MediaOverview from '../components/MediaOverview.vue'
import ActionMenu from '../components/ActionMenu.vue'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, posterUrl } from '../api.js'
import { episodeVersion } from '../episodeVersions.js'
import { fmtRemaining } from '../format.js'
import { progressWidth } from '../recentPlayed.js'
import PlayerModal from '../components/PlayerModal.vue'
import CastWall from '../components/CastWall.vue'
import TvSeasonCatalog from '../components/TvSeasonCatalog.vue'
import { useTvCollection } from '../useTvCollection.js'
import { airingLabel, collectionCountText, collectionExplanation, sourceSeasonPath, tvSeasonLabel, uniqueSeasonSources } from '../tvCollection.js'

const PAGE = 100  // 与后端季分页 limit 默认对齐

const route = useRoute()
const router = useRouter()
const s = ref(null)
const { data: collection } = useTvCollection(() => ({ id: Number(route.params.showId) }), api)
const seasonTab = ref('local')
let tabInitialized = false
const catalog = ref(null), catalogLoading = ref(false), catalogError = ref('')
const collectionSeason = computed(() => {
  const snapshot = collection.value?.seasons?.find(value => Number(value.season) === Number(route.params.season)) || null
  const detail = catalog.value
  if (detail && Number(detail.show_id) === Number(route.params.showId) && Number(detail.season) === Number(route.params.season)
      && (!collection.value?.tmdb_id || Number(detail.tmdb_id) === Number(collection.value.tmdb_id))) return { ...snapshot, ...detail }
  return snapshot
})
let catalogGeneration = 0, catalogController = null, catalogPromise = null
let disposed = false
const posterFailed = ref(false)
const hasLocalFiles = computed(() => Number(s.value?.episode_count) > 0)
const seasonPoster = computed(() => {
  const path = s.value?.poster_path || collectionSeason.value?.poster_path || catalog.value?.poster_path
  return path ? posterUrl(path) : collectionSeason.value?.poster_url || catalog.value?.poster_url || ''
})
const otherSeasonSources = computed(() => uniqueSeasonSources([
  ...(collectionSeason.value?.sources || []), ...(catalog.value?.sources || []),
]).filter(source => Number(source.show_id) !== Number(route.params.showId) || Number(source.season) !== Number(route.params.season)))
const loading = ref(true)
const loadError = ref('')
const loadErrorMore = ref(false)
const eps = ref([])  // 按显示范围懒加载的集（s.episodes 只含首屏，不再全量）
const hasMore = ref(false)
const loadingMore = ref(false)
const msg = ref('')
const playing = ref(null)
const busy = ref(false)
const selectedVersion = ref('')
let loadGeneration = 0
const verifyMode = ref('0')
const verifying = ref(false)
const sentinel = ref(null)
let observer = null

const seasonDone = computed(() => {
  // 表头计数恒为全季（不受分页影响）
  const total = Number(s.value?.episode_count) || 0
  const watched = Number(s.value?.watched_count) || 0
  if (total > 0) return watched >= total
  const list = eps.value || []
  return list.length > 0 && list.every(e => Number(e.watched))
})
function pad (n) { return String(n).padStart(2, '0') }
function seasonLabel (n) { return tvSeasonLabel(n) }
function epNo (e) {
  const base = `S${pad(e.season)}E${pad(e.episode)}`
  const end = Number(e.episode_end) > 0 ? `-E${pad(e.episode_end)}` : ''
  return base + end
}
function stillUrl (e) { return `/api/tv/episodes/${e.id}/still` }
function openEpisode (id) {
  router.push(`/tv/${s.value.show_id}/s/${s.value.season}/e/${id}`)
}
function playLabel (e) {
  const ver = episodeVersion(e)
  return `${s.value.show_title} ${epNo(e)}${e.title ? ' · ' + e.title : ''}`
    + (ver > 1 ? `（V${ver}）` : '')
}
function play (e) {
  playing.value = { id: e.id, label: playLabel(e) }
}
function seasonUrl (offset) {
  const v = verifyMode.value && verifyMode.value !== '0'
    ? '&verify=' + encodeURIComponent(verifyMode.value) : ''
  return `/api/tv/shows/${route.params.showId}/seasons/${route.params.season}`
    + `?offset=${offset}&limit=${PAGE}${v}${selectedVersion.value ? '&version=' + selectedVersion.value : ''}`
}
async function load () {
  const generation = ++loadGeneration
  if (s.value && (String(s.value.show_id) !== String(route.params.showId) || String(s.value.season) !== String(route.params.season))) { s.value = null; eps.value = [] }
  loading.value = true
  loadError.value = ''
  loadErrorMore.value = false
  disconnectObserver()
  msg.value = ''
  loadingMore.value = false
  try {
    let d
    try { d = await api(seasonUrl(0)) }
    catch (e) {
      if (!/^404\b/.test(e.message) || generation !== loadGeneration) throw e
      // A newly announced season may exist only in the airing cache. It remains
      // a read-only catalog, without creating fake local episode records.
      const [show, official] = await Promise.all([
        api(`/api/tv/shows/${route.params.showId}`), loadCatalog(),
      ])
      if (generation !== loadGeneration) return
      const metadata = official || collectionSeason.value || show.seasons?.find(value => Number(value.season) === Number(route.params.season))
      if (!metadata) throw e
      d = { show_id: show.id, show_title: show.title, show_year: show.year,
        show_backdrop_path: show.backdrop_path, original_language: show.original_language,
        season: Number(route.params.season), name: metadata.name, overview: metadata.overview,
        air_date: metadata.air_date, poster_path: metadata.poster_path,
        episode_count: 0, distinct_count: 0, watched_count: 0,
        episodes: [], versions: [], next_episode: null, total: 0, has_more: false, cast: [] }
    }
    if (generation !== loadGeneration || disposed) return
    s.value = d
    eps.value = d.episodes || []
    hasMore.value = !!d.has_more
    if (!tabInitialized) {
      tabInitialized = true
      seasonTab.value = Number(d.episode_count) > 0 ? 'local' : 'catalog'
    }
    if (seasonTab.value === 'catalog') loadCatalog()
    else observeSentinel()
  } catch (e) {
    if (generation === loadGeneration) loadError.value = e.message
  } finally {
    if (generation === loadGeneration) loading.value = false
  }
}
function showCatalog() { seasonTab.value = 'catalog'; loadCatalog() }
function loadCatalog(force = false) {
  if (disposed) return Promise.resolve(null)
  if (catalogPromise && !force) return catalogPromise
  if (catalog.value && !force) return Promise.resolve(catalog.value)
  if (collection.value && !collection.value.tmdb_id) {
    catalogError.value = '该剧尚未匹配 TMDB，暂时没有官方分集资料。'
    return Promise.resolve(null)
  }
  const showId = String(route.params.showId), season = String(route.params.season)
  const generation = ++catalogGeneration
  catalogController?.abort()
  const controller = new AbortController()
  catalogController = controller
  catalogLoading.value = true
  catalogError.value = ''
  const current = () => !disposed && generation === catalogGeneration && showId === String(route.params.showId) && season === String(route.params.season)
  catalogPromise = (async () => {
    try {
      const data = await api(`/api/tv/shows/${showId}/seasons/${season}/catalog`, { signal: controller.signal })
      if (!current()) return null
      if (Number(data?.show_id) !== Number(showId) || Number(data?.season) !== Number(season)) {
        throw new Error('分集资料与当前剧季不一致，请重新读取。')
      }
      if (data?.tmdb_id && collection.value?.tmdb_id && Number(data.tmdb_id) !== Number(collection.value.tmdb_id)) {
        throw new Error('剧集匹配已变化，请重新读取分集资料。')
      }
      if (!Array.isArray(data?.items)) throw new Error('暂时没有可用的分集资料。')
      catalog.value = data
      return data
    } catch (e) { if (current()) catalogError.value = e.message }
    finally { if (current()) { catalogLoading.value = false; catalogPromise = null } }
    return null
  })()
  return catalogPromise
}
function retryLoad () { return loadErrorMore.value ? loadMore() : load() }
async function loadMore () {
  if (loading.value || loadingMore.value || !hasMore.value || !s.value) return
  const generation = loadGeneration
  loadingMore.value = true
  loadError.value = ''
  try {
    const d = await api(seasonUrl(eps.value.length))
    if (generation !== loadGeneration) return
    eps.value = eps.value.concat(d.episodes || [])
    hasMore.value = !!d.has_more
    // 表头计数恒为全季：用最新头更新（集行只追加）
    if (d.watched_count !== undefined) s.value.watched_count = d.watched_count
    if (d.episode_count !== undefined) s.value.episode_count = d.episode_count
  } catch (e) {
    if (generation === loadGeneration) { loadError.value = e.message; loadErrorMore.value = true }
  } finally {
    if (generation === loadGeneration) loadingMore.value = false
  }
}
function observeSentinel () {
  disconnectObserver()
  if (seasonTab.value !== 'local' || typeof IntersectionObserver === 'undefined') return
  observer = new IntersectionObserver((entries) => {
    if (entries.some(en => en.isIntersecting)) loadMore()
  }, { rootMargin: '600px' })
  if (sentinel.value) observer.observe(sentinel.value)
}
function disconnectObserver () {
  if (observer) { observer.disconnect(); observer = null }
}
async function verifyExists () {
  verifying.value = true
  try {
    verifyMode.value = '1'
    await load()
  } catch (e) { msg.value = '校验失败：' + e.message } finally { verifying.value = false }
}
async function toggleSeasonWatched () {
  busy.value = true
  try {
    await api(`/api/tv/shows/${s.value.show_id}/seasons/${s.value.season}/watched`, {
      method: 'POST', body: JSON.stringify({ watched: !seasonDone.value }) })
    await load()
  } catch (e) { msg.value = '操作失败：' + e.message } finally { busy.value = false }
}
async function markWatched (episodeId, watched) {
  try {
    await api(`/api/tv/episodes/${episodeId}/watched`, {
      method: 'POST', body: JSON.stringify({ watched }) })
  } catch (e) { msg.value = '操作失败：' + e.message }
}
async function onWatched () {
  const cur = playing.value
  if (!cur) return
  await markWatched(cur.id, true)
  await load()
}
async function onEnded () {
  const cur = playing.value
  if (!cur || cur.kind && cur.kind !== 'episode') return
  try {
    const next = await followingPlayback(cur, s.value.show_title)
    if (playing.value === cur) playing.value = next
  } catch (e) { msg.value = '自动连播失败：' + e.message }
}

onMounted(() => load())
onUnmounted(() => { disposed = true; loadGeneration++; catalogGeneration++; catalogController?.abort(); disconnectObserver() })
watch(() => [route.params.showId, route.params.season], () => {
  playing.value = null
  selectedVersion.value = ''; tabInitialized = false; seasonTab.value = 'local'; posterFailed.value = false
  catalogGeneration++; catalogController?.abort(); catalogPromise = null; catalog.value = null; catalogError.value = ''; catalogLoading.value = false
  load()
})
watch(seasonTab, value => { if (value === 'catalog') disconnectObserver(); else observeSentinel() }, { flush: 'post' })
watch(() => collection.value?.tmdb_id, (value, previous) => {
  if ((previous && value !== previous) || (value && catalog.value?.tmdb_id && Number(value) !== Number(catalog.value.tmdb_id))) {
    catalogGeneration++; catalogController?.abort(); catalogPromise = null; catalog.value = null; catalogError.value = ''; catalogLoading.value = false
    if (seasonTab.value === 'catalog') loadCatalog()
  }
})
watch(seasonPoster, () => { posterFailed.value = false })
</script>

<style scoped>

/* 简介与空态走 App.vue 全局 .overview/.empty 单源（与电影/剧详情同形态）；季无评分，不渲染 HeroRatings */

/* 演职员样式单源：CastWall.vue */
.ep-grid { --poster-min: 220px; }
.ep-card { cursor: pointer; }
.still-wrap { position: relative; background: var(--jz-surface-2); }
.still-wrap img { width: 100%; aspect-ratio: 16/9; object-fit: cover; display: block; }
.still-none { width: 100%; aspect-ratio: 16/9; display: flex; align-items: center; justify-content: center; background: var(--jz-surface-3); color: var(--jz-text-faint); font-size: 0.875rem; }
.ep-done, .ep-left { position: absolute; top: 6px; left: 6px; font-size: 0.75rem; padding: 2px 8px; border-radius: 999px; background: var(--jz-overlay); }
.ep-done { color: var(--jz-green); }
.ep-left { color: var(--jz-text); }
.ep-bar { position: absolute; left: 0; right: 0; bottom: 0; height: 4px; background: var(--jz-overlay-soft); }
.ep-bar-in { height: 100%; background: var(--jz-accent); }
.ep-no { color: var(--jz-link); }

.review-badge { margin-left: 8px; font-size: 0.75rem; padding: 1px 8px; border-radius: 999px; background: var(--jz-warn-soft); color: var(--jz-warn); border: 1px solid var(--jz-warn-border); }
.local-badge { margin-left: 8px; font-size: 0.75rem; padding: 1px 8px; border-radius: 999px; background: var(--jz-info-soft); color: var(--jz-blue-chip); border: 1px solid var(--jz-info-border); }
.ver-badge { margin-left: 6px; font-size: 0.6875rem; padding: 0 5px; border-radius: 3px; color: var(--jz-warn); border: 1px solid var(--jz-warn-border); }
.dim { color: var(--jz-text-faint); }

</style>

<style scoped>
.season-list-heading { display: flex; flex-wrap: wrap; align-items: center; gap: 16px; justify-content: space-between; }
.season-list-heading label { display: flex; align-items: center; gap: 8px; }
.season-list-views { display: flex; gap: var(--jz-gap-s); margin: 0 0 var(--jz-gap-l); }
.season-collection-note { color: var(--jz-text-dim); font-size: var(--jz-font-s); line-height: 1.7; margin: var(--jz-gap-m) 0; }
.season-source-links { display: flex; flex-wrap: wrap; gap: var(--jz-gap-m); margin: var(--jz-gap-s) 0; }
.season-source-links a { min-height: var(--jz-touch-target); align-content: center; font-size: var(--jz-font-s); }
</style>
