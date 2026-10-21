<template>
  <div v-if="ep" class="tv-page media-detail episode-detail">
    <nav class="crumbs" aria-label="当前位置">
      <router-link to="/tv">剧集</router-link><span class="dim"> / </span>
      <router-link :to="'/tv/' + ep.show_id">{{ ep.show_title }}</router-link>
      <span class="dim"> / </span>
      <router-link :to="`/tv/${ep.show_id}/s/${ep.season}`">{{ ep.season_name || seasonLabel(ep.season) }}</router-link>
      <span class="dim"> / </span>
      <span>{{ epNo(ep) }}</span>
    </nav>
    <div class="hero media-hero">
      <MediaBackdrop :src="ep.show_backdrop_path ? posterUrl(ep.show_backdrop_path) : (ep.still_path ? stillUrl(ep) : '')" />
      <div class="hero-inner">
        <img v-if="ep.still_path" class="hero-still" :src="stillUrl(ep)" alt=""
          @error="ep.still_path = ''" />
        <ArtworkPlaceholder v-else class="hero-still hero-no-still" kind="poster" :label="ep.title || ep.show_title" />
        <div class="hero-body">
          <div class="hero-heading">
            <p class="eyebrow">{{ ep.show_title }} · {{ epNo(ep) }}</p>
            <h1>{{ ep.title || epNo(ep) }}</h1>
            <HeroRatings :tmdb="ep.tmdb_rating" />
            <div class="meta">
              <span v-if="ep.air_date">{{ ep.air_date }}</span>
              <span v-if="ep.runtime">{{ ep.runtime }} 分钟</span>
              <span v-if="ep.watched" class="seen-tag"><AppIcon name="check" :size="14" />已看</span>
              <span v-if="ep.needs_review" class="review-badge">未匹配集号</span>
              <span v-if="ep.local_only" class="local-badge">本地集</span>
            </div>
          </div>
          <div class="acts">
            <JzButton v-if="ep.exists" class="primary" @click="play" type="button" variant="primary">
              <PlayerIcon name="play" :size="20" /> {{ ep.progress ? '继续播放' : '播放' }}
            </JzButton>
            <span v-else class="dim">文件缺失</span>
            <JzButton @click="toggleWatched" type="button">{{ ep.watched ? '标记未看' : '标记已看' }}</JzButton>
            <ActionMenu v-if="!isChildren">
              <JzButton @click="pickOpen = !pickOpen" type="button" variant="ghost" icon="match">{{ pickOpen ? '收起集号匹配' : '修正集号匹配' }}</JzButton>
            </ActionMenu>
          </div>
          <MediaOverview :text="ep.overview || ''" />
          <nav v-if="prevEp || nextEp" class="episode-navigation" aria-label="切换剧集">
            <JzButton v-if="prevEp" @click="goEpisode(prevEp)" type="button" icon="back">上一集 {{ epNo(prevEp) }}</JzButton>
            <JzButton v-if="nextEp" @click="goEpisode(nextEp)" type="button" icon="forward">下一集 {{ epNo(nextEp) }}</JzButton>
          </nav>
          <EmptyState v-if="loadError" state="error" title="分集刷新失败" :text="loadError" retry @retry="load" />
          <p v-if="msg" class="page-feedback" role="status">{{ msg }}</p>
          <div v-if="!isChildren && ep.needs_review && !pickOpen" class="review-notice">
            <span>此集尚未匹配到集名和简介</span><JzButton @click="pickOpen = true" type="button" icon="match">匹配集号</JzButton>
          </div>
          <div v-if="pickOpen" class="match card-block">
            <div class="bar match-bar">
              <span>为 <b>{{ epNo(ep) }}</b> 指定 TMDB 集（本地集号不变，只取元数据）</span>
              <label>季号 <input v-model.number="pickSeason" type="number" min="0" style="width: 72px" /></label>
              <JzButton :disabled="searching || matching" @click="loadCandidates" type="button">查询该季</JzButton>
            </div>
            <div class="bar match-bar">
              <span class="dim">TMDB 确实没有这一集：</span>
              <input v-model="localTitle" aria-label="本地集名" placeholder="本地集名（可选）" style="width: 220px" />
              <JzButton :disabled="matching" @click="confirmLocal" type="button">确认无对应集</JzButton>
            </div>
            <div v-for="c in candidates" :key="c.tmdb_episode_id" class="mrow">
              <span class="mname">S{{ pad(c.season) }}E{{ pad(c.episode) }} · {{ c.title }}</span>
              <span class="dim">{{ c.air_date }}</span>
              <JzButton :disabled="matching" @click="bindEpisode(c)" type="button" icon="match">匹配此集</JzButton>
            </div>
            <div v-if="searchedCand && !candidates.length" class="dim">该季没有候选</div>
          </div>
        </div>
      </div>
    </div>
    <CrewRow :directors="directors" />
    <CastWall :cast="ep.cast || []" :original-language="ep.original_language || ''"
      :subtitle="ep.cast_source === 'season' ? '本季' : ep.cast_source === 'aggregate' ? '全剧' : ''" />
  </div>
  <EmptyState v-else :state="loadError ? 'error' : 'loading'" :title="loadError ? '分集加载失败' : '正在加载分集'"
    :text="loadError || '请稍候…'" :retry="!!loadError" @retry="load">
    <router-link v-if="loadError" to="/tv">返回剧集列表</router-link>
  </EmptyState>
  <PlayerModal v-if="playing" :key="'episode:' + playing.id"
    :version-id="playing.id" :title="playing.label" kind="episode"
    @close="playing = null" @watched="onWatched" @ended="onEnded" @version-change="playing = $event" />
</template>

<script setup>
import { isChildren } from '../viewing.js'
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
import { api, posterUrl, scopedArtworkUrl } from '../api.js'
import { episodeVersion } from '../episodeVersions.js'
import PlayerModal from '../components/PlayerModal.vue'
import CastWall from '../components/CastWall.vue'
import CrewRow from '../components/CrewRow.vue'
import HeroRatings from '../components/HeroRatings.vue'

const route = useRoute()
const router = useRouter()
const ep = ref(null)
const loadError = ref('')
let loadSeq = 0
let episodeGeneration = 0
let candidateSeq = 0
let candidateController = null
let disposed = false
const msg = ref('')
const playing = ref(null)
const searching = ref(false)
const matching = ref(false)
const pickOpen = ref(false)
const pickSeason = ref(1)
const localTitle = ref('')
const candidates = ref([])
const searchedCand = ref(false)

function currentEpisode(id) {
  return !disposed && String(route.params.epId) === id && String(ep.value?.id) === id
}
function invalidateCandidates() {
  candidateSeq++
  candidateController?.abort()
  candidateController = null
  candidates.value = []
  searching.value = false
  searchedCand.value = false
}

const directors = computed(() => ep.value?.directors || [])
const prevEp = computed(() => ep.value?.previous_episode || null)
const nextEp = computed(() => ep.value?.next_episode || null)

function pad (n) { return String(n).padStart(2, '0') }
function seasonLabel (n) { return Number(n) === 0 ? '特典' : `第 ${n} 季` }
function epNo (e) {
  const base = `S${pad(e.season)}E${pad(e.episode)}`
  const end = Number(e.episode_end) > 0 ? `-E${pad(e.episode_end)}` : ''
  return base + end
}
function stillUrl (e) { return scopedArtworkUrl(`/api/tv/episodes/${e.id}/still`) }
function playLabel (e) {
  const ver = episodeVersion(e)
  return `${e.show_title} ${epNo(e)}${e.title ? ' · ' + e.title : ''}`
    + (ver > 1 ? `（V${ver}）` : '')
}
function play () {
  playing.value = { id: ep.value.id, label: playLabel(ep.value) }
}
function goEpisode (target) {
  router.push(`/tv/${ep.value.show_id}/s/${target.season}/e/${target.id}`)
}
async function load () {
  const seq = ++loadSeq
  const id = route.params.epId
  if (ep.value && String(ep.value.id) !== String(id)) ep.value = null
  loadError.value = ''
  msg.value = ''
  try {
    const data = await api(`/api/tv/episodes/${id}`)
    if (seq !== loadSeq) return
    ep.value = data
    pickSeason.value = Number(ep.value.season) || 0
  } catch (e) {
    if (seq === loadSeq) loadError.value = e.message
  }
}
async function markWatched (watched) {
  try {
    await api(`/api/tv/episodes/${ep.value.id}/watched`, {
      method: 'POST', body: JSON.stringify({ watched }) })
    await load()
  } catch (e) { msg.value = '操作失败：' + e.message }
}
function toggleWatched () { markWatched(!Number(ep.value.watched)) }
async function loadCandidates () {
  const id = String(ep.value?.id)
  if (!currentEpisode(id) || matching.value) return
  invalidateCandidates()
  const seq = candidateSeq
  const season = pickSeason.value
  const showId = ep.value.show_id
  candidateController = new AbortController()
  const signal = candidateController.signal
  const current = () => currentEpisode(id) && seq === candidateSeq
  searching.value = true
  searchedCand.value = false
  try {
    const d = await api(`/api/tv/shows/${showId}/tmdb-episodes?season=${season}`, { signal })
    if (!current()) return
    candidates.value = d.items || []
    searchedCand.value = true
  } catch (e) { if (current()) msg.value = '候选查询失败：' + e.message }
  finally { if (current()) searching.value = false }
}
async function bindEpisode (c) {
  const id = String(ep.value?.id)
  if (!currentEpisode(id) || !pickOpen.value || matching.value || !candidates.value.includes(c)) return
  const generation = episodeGeneration
  const current = () => currentEpisode(id) && generation === episodeGeneration
  matching.value = true
  try {
    await api(`/api/tv/episodes/${id}/match-episode`, {
      method: 'POST',
      body: JSON.stringify({ tmdb_episode_id: c.tmdb_episode_id, season: c.season }),
    })
    if (!current()) return
    pickOpen.value = false
    candidates.value = []
    await load()
  } catch (e) { if (current()) msg.value = '绑定失败：' + e.message }
  finally { if (current()) matching.value = false }
}
async function confirmLocal () {
  const id = String(ep.value?.id)
  if (!currentEpisode(id) || matching.value) return
  const generation = episodeGeneration
  const current = () => currentEpisode(id) && generation === episodeGeneration
  matching.value = true
  try {
    await api(`/api/tv/episodes/${id}/confirm-local`, {
      method: 'POST',
      body: JSON.stringify({ title: localTitle.value || null }),
    })
    if (!current()) return
    pickOpen.value = false
    candidates.value = []
    await load()
  } catch (e) { if (current()) msg.value = '操作失败：' + e.message }
  finally { if (current()) matching.value = false }
}
async function onWatched () {
  const cur = playing.value
  if (!cur) return
  try {
    await api(`/api/tv/episodes/${cur.id}/watched`, {
      method: 'POST', body: JSON.stringify({ watched: true }) })
  } catch (e) { /* 忽略 */ }
  await load()
}
async function onEnded () {
  const cur = playing.value
  if (!cur || cur.kind && cur.kind !== 'episode') return
  try {
    const next = await followingPlayback(cur, ep.value.show_title)
    if (playing.value === cur) playing.value = next
  } catch (e) { msg.value = '自动连播失败：' + e.message }
}

onMounted(load)
onUnmounted(() => { disposed = true; loadSeq++; invalidateCandidates() })
watch(pickSeason, invalidateCandidates, { flush: 'sync' })
watch(pickOpen, open => { if (!open) invalidateCandidates() }, { flush: 'sync' })
watch(() => route.params.epId, () => {
  episodeGeneration++
  invalidateCandidates()
  pickOpen.value = false
  localTitle.value = ''
  matching.value = false
  load()
}, { flush: 'sync' })
</script>

<style scoped>

/* 简介与空态走 App.vue 全局 .overview/.empty 单源（与电影/剧详情同形态） */
.overview { max-width: 900px; }

.match { margin-top: 10px; max-width: 720px; }
.match-bar { padding: 0; gap: 6px; }
.match-bar input { flex: 1; }
.mrow { display: flex; align-items: center; gap: 10px; padding: 6px 0; border-bottom: 1px solid var(--jz-border); font-size: 0.875rem; }
.mrow .mname { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* 演职员/导演样式单源：CastWall.vue / CrewRow.vue */
.seen-tag { color: var(--jz-green); }
.review-badge { font-size: 0.75rem; padding: 1px 8px; border-radius: 999px; background: var(--jz-warn-soft); color: var(--jz-warn); border: 1px solid var(--jz-warn-border); }
.local-badge { font-size: 0.75rem; padding: 1px 8px; border-radius: 999px; background: var(--jz-info-soft); color: var(--jz-blue-chip); border: 1px solid var(--jz-info-border); }
.dim { color: var(--jz-text-faint); }

</style>
