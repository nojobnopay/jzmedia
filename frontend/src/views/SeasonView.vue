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
        <img v-if="s.poster_path" class="hero-poster" :src="posterUrl(s.poster_path)"
          :alt="s.name || seasonLabel(s.season)" />
        <div v-else class="hero-poster hero-no-poster">{{ seasonLabel(s.season).slice(0, 1) }}</div>
        <div class="hero-body">
          <h1>{{ s.show_title }}<span v-if="s.show_year" class="dim"> ({{ s.show_year }})</span></h1>
          <div class="meta">
            <span>{{ s.name || seasonLabel(s.season) }}</span>
            <span v-if="s.air_date">{{ s.air_date }}</span>
            <span>{{ s.distinct_count ?? s.episode_count }} 集 · {{ (s.versions || []).length || 1 }} 版本</span>
            <span>{{ s.watched_count }}/{{ s.episode_count }} 已看</span>
          </div>
          <MediaOverview :text="s.overview || ''" />
          <div class="acts">
            <button v-if="s.next_episode" class="primary" :disabled="!s.next_episode.exists"
              @click="play(s.next_episode)">
              <PlayerIcon name="play" :size="20" /> {{ s.next_episode.progress ? '继续观看' : '播放本季' }} {{ epNo(s.next_episode) }}
            </button>
            <button v-if="eps.length" :disabled="busy" @click="toggleSeasonWatched">
              {{ seasonDone ? '标记本季全部版本未看' : '标记本季全部版本已看' }}
            </button>
            <ActionMenu>
              <button :disabled="verifying" @click="verifyExists">{{ verifying ? '检查中…' : '检查文件是否可用' }}</button>
            </ActionMenu>
            <span v-if="busy" class="dim">处理中…</span>
          </div>
          <p v-if="msg" class="page-feedback" role="status">{{ msg }}</p>
        </div>
      </div>
    </div>
    <div class="season-list-heading"><h2 class="section-heading">选择剧集 <span>{{ s.distinct_count ?? s.episode_count }} 集</span></h2>
      <label v-if="(s.versions || []).length > 1">播放版本 <select v-model="selectedVersion" @change="load">
        <option value="">全部版本</option><option v-for="v in s.versions" :key="v.version" :value="String(v.version)">V{{ v.version }} · {{ v.distinct }} 集</option>
      </select></label></div>
    <div class="grid ep-grid">
      <div v-for="e in eps" :key="e.id" class="card ep-card" role="link" tabindex="0" @keydown.enter.self="openEpisode(e.id)" @click="openEpisode(e.id)">
        <div class="still-wrap">
          <img v-if="e.still_path" :src="stillUrl(e)" loading="lazy" alt=""
            @error="e.still_path = ''" />
          <div v-else class="still-none" aria-hidden="true">{{ epNo(e) }}</div>
          <button v-if="e.exists" class="poster-play"
            :aria-label="'播放 ' + epNo(e)" :title="'播放 ' + epNo(e)"
            @click.stop="play(e)"><PlayerIcon name="play" :size="24" /></button>
          <span v-if="e.watched" class="ep-done">✓已看</span>
          <span v-else-if="e.progress" class="ep-left">{{ fmtRemaining(e.progress.remaining_sec) }}</span>
          <div v-if="!e.watched && e.progress" class="ep-bar" aria-hidden="true">
            <div class="ep-bar-in" :style="{ width: progressWidth(e.progress) }"></div>
          </div>
        </div>
        <div class="t">
          <span class="ep-no">{{ epNo(e) }}</span><span v-if="episodeVersion(e) > 1" class="ver-badge">V{{ episodeVersion(e) }}</span>
          {{ e.title || '（未匹配集名）' }}
          <span v-if="e.needs_review" class="review-badge">未匹配</span>
          <span v-if="e.local_only" class="local-badge">本地集</span>
          <br /><span v-if="e.air_date" class="meta">{{ e.air_date }}</span>
          <span v-if="e.overview" class="meta"> {{ e.overview.slice(0, 60) }}</span>
        </div>
      </div>
    </div>
    <div ref="sentinel" class="more-sentinel" aria-hidden="true"></div>
    <div v-if="hasMore" class="bar more-bar">
      <button :disabled="loadingMore" @click="loadMore">
        {{ loadingMore ? '加载中…' : `加载更多（${eps.length}/${s.total || s.episode_count}）` }}
      </button>
    </div>
    <div v-else-if="eps.length" class="bar dim small">已加载全部 {{ eps.length }} 个文件</div>
    <CastWall :cast="s.cast || []" :original-language="s.original_language || ''"
      :subtitle="s.cast_source === 'season' ? seasonLabel(s.season) : '全剧'" />
  </div>
  <div v-else class="bar">{{ msg || '加载中…' }}</div>
  <PlayerModal v-if="playing" :key="'episode:' + playing.id"
    :version-id="playing.id" :title="playing.label" kind="episode"
    @close="playing = null" @watched="onWatched" @ended="onEnded" />
</template>

<script setup>
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

const PAGE = 100  // 与后端季分页 limit 默认对齐

const route = useRoute()
const router = useRouter()
const s = ref(null)
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
function seasonLabel (n) { return Number(n) === 0 ? '特典' : `第 ${n} 季` }
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
  disconnectObserver()
  msg.value = ''
  loadingMore.value = false
  try {
    const d = await api(seasonUrl(0))
    if (generation !== loadGeneration) return
    s.value = d
    eps.value = d.episodes || []
    hasMore.value = !!d.has_more
    observeSentinel()
  } catch (e) {
    if (generation === loadGeneration) msg.value = '加载失败：' + e.message
  }
}
async function loadMore () {
  if (loadingMore.value || !hasMore.value || !s.value) return
  const generation = loadGeneration
  loadingMore.value = true
  try {
    const d = await api(seasonUrl(eps.value.length))
    if (generation !== loadGeneration) return
    eps.value = eps.value.concat(d.episodes || [])
    hasMore.value = !!d.has_more
    // 表头计数恒为全季：用最新头更新（集行只追加）
    if (d.watched_count !== undefined) s.value.watched_count = d.watched_count
    if (d.episode_count !== undefined) s.value.episode_count = d.episode_count
  } catch (e) {
    if (generation === loadGeneration) msg.value = '加载失败：' + e.message
  } finally {
    if (generation === loadGeneration) loadingMore.value = false
  }
}
function observeSentinel () {
  disconnectObserver()
  if (typeof IntersectionObserver === 'undefined') return
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
onUnmounted(() => { loadGeneration++; disconnectObserver() })
watch(() => [route.params.showId, route.params.season], () => { selectedVersion.value = ''; load() })
</script>

<style scoped>
.tv-page { padding-bottom: 24px; }
.crumbs { padding: 12px 12px 0; font-size: 0.875rem; color: #aaa; }
.crumbs a { color: #9ecfff; text-decoration: none; }
.hero-inner { display: flex; gap: 18px; padding: 18px 16px; align-items: flex-end; }
.hero-poster { width: 150px; aspect-ratio: 2/3; object-fit: cover; border-radius: 8px; box-shadow: 0 6px 20px rgba(0,0,0,.6); flex: 0 0 auto; }
.hero-no-poster { display: flex; align-items: center; justify-content: center; background: #242424; color: #555; font-size: 2.5rem; font-weight: bold; }
.hero-body { min-width: 0; }
.hero-body h2 { margin: 0 0 6px; font-size: 1.5rem; }
.meta { display: flex; flex-wrap: wrap; gap: 10px; color: #aaa; font-size: 0.8125rem; margin-bottom: 8px; }
/* 简介与空态走 App.vue 全局 .overview/.empty 单源（与电影/剧详情同形态）；季无评分，不渲染 HeroRatings */
.acts { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.acts .primary { background: #e50914; border-color: #e50914; color: #fff; }
/* 演职员样式单源：CastWall.vue */
.ep-grid { --poster-min: 220px; }
.ep-card { cursor: pointer; }
.still-wrap { position: relative; background: #222; }
.still-wrap img { width: 100%; aspect-ratio: 16/9; object-fit: cover; display: block; }
.still-none { width: 100%; aspect-ratio: 16/9; display: flex; align-items: center; justify-content: center; background: #242424; color: #555; font-size: 0.875rem; }
.ep-done, .ep-left { position: absolute; top: 6px; left: 6px; font-size: 0.75rem; padding: 2px 8px; border-radius: 999px; background: rgba(0,0,0,.72); }
.ep-done { color: #7ed321; }
.ep-left { color: #ddd; }
.ep-bar { position: absolute; left: 0; right: 0; bottom: 0; height: 4px; background: rgba(0,0,0,.55); }
.ep-bar-in { height: 100%; background: #e50914; }
.ep-no { color: #9ecfff; }
.meta { color: #888; font-size: 0.75rem; }
.review-badge { margin-left: 8px; font-size: 0.75rem; padding: 1px 8px; border-radius: 999px; background: rgba(255, 179, 0, .16); color: #ffb300; border: 1px solid rgba(255, 179, 0, .4); }
.local-badge { margin-left: 8px; font-size: 0.75rem; padding: 1px 8px; border-radius: 999px; background: rgba(120, 170, 255, .14); color: #7aaaff; border: 1px solid rgba(120, 170, 255, .4); }
.ver-badge { margin-left: 6px; font-size: 0.6875rem; padding: 0 5px; border-radius: 3px; color: #b98a00; border: 1px solid #6b5410; }
.dim { color: #777; }
@media (max-width: 700px) {
  .hero-inner { flex-direction: column; align-items: flex-start; }
}
</style>

<style scoped>
.season-list-heading { display: flex; flex-wrap: wrap; align-items: center; gap: 16px; justify-content: space-between; }
.season-list-heading label { display: flex; align-items: center; gap: 8px; }
</style>
