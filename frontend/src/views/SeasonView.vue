<template>
  <div v-if="s" class="tv-page">
    <div class="crumbs">
      <router-link :to="'/tv/' + s.show_id">{{ s.show_title }}</router-link>
      <span class="dim"> / </span>
      <span>{{ s.name || seasonLabel(s.season) }}</span>
    </div>
    <div class="hero">
      <div class="hero-inner">
        <img v-if="s.poster_path" class="hero-poster" :src="posterUrl(s.poster_path)"
          :alt="s.name || seasonLabel(s.season)" />
        <div v-else class="hero-poster hero-no-poster">{{ seasonLabel(s.season).slice(0, 1) }}</div>
        <div class="hero-body">
          <h2>{{ s.show_title }}<span v-if="s.show_year" class="dim"> ({{ s.show_year }})</span></h2>
          <div class="meta">
            <span>{{ s.name || seasonLabel(s.season) }}</span>
            <span v-if="s.air_date">{{ s.air_date }}</span>
            <span>{{ s.episode_count }} 集</span>
            <span>{{ s.watched_count }}/{{ s.episode_count }} 已看</span>
          </div>
          <p v-if="s.overview" class="ov">{{ s.overview }}</p>
          <p v-else class="ov dim">本季暂无简介</p>
          <div class="acts">
            <button v-if="s.next_episode" class="primary" :disabled="!s.next_episode.exists"
              @click="play(s.next_episode)">
              ▶ {{ s.next_episode.progress ? '继续' : '播放本季' }} {{ epNo(s.next_episode) }}
            </button>
            <button v-if="s.episodes.length" @click="toggleSeasonWatched">
              {{ seasonDone ? '标记本季未看' : '标记本季已看' }}
            </button>
            <span v-if="busy" class="dim">处理中…</span>
          </div>
        </div>
      </div>
    </div>
    <section v-if="s.cast && s.cast.length" class="card-block cast-sec">
      <h3>演职员 <span class="dim">{{ s.cast.length }} · {{ s.cast_source === 'season' ? seasonLabel(s.season) : '全剧' }}</span></h3>
      <div class="cast-wall">
        <div v-for="p in s.cast" :key="p.id || p.name" class="cast-card"
          :title="p.character ? `${p.name} 饰 ${p.character}` : p.name">
          <div class="avatar-fallback" aria-hidden="true">{{ (p.name || '?').slice(0, 1) }}</div>
          <div class="cast-name">{{ p.name }}</div>
          <div v-if="showCharacter && p.character" class="cast-char">{{ p.character }}</div>
        </div>
      </div>
    </section>
    <div class="grid ep-grid">
      <div v-for="e in s.episodes" :key="e.id" class="card ep-card" @click="openEpisode(e.id)">
        <div class="still-wrap">
          <img v-if="e.still_path" :src="stillUrl(e)" loading="lazy" alt=""
            @error="e.still_path = ''" />
          <div v-else class="still-none" aria-hidden="true">{{ epNo(e) }}</div>
          <button v-if="e.exists && !selecting" class="poster-play"
            :aria-label="'播放 ' + epNo(e)" :title="'播放 ' + epNo(e)"
            @click.stop="play(e)">▶</button>
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
  </div>
  <div v-else class="bar">{{ msg || '加载中…' }}</div>
  <PlayerModal v-if="playing" :key="'episode:' + playing.id"
    :version-id="playing.id" :title="playing.label" kind="episode"
    @close="playing = null" @watched="onWatched" @ended="onEnded" />
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, posterUrl } from '../api.js'
import { episodeVersion } from '../episodeVersions.js'
import { fmtRemaining } from '../format.js'
import { progressWidth } from '../recentPlayed.js'
import PlayerModal from '../components/PlayerModal.vue'

const route = useRoute()
const router = useRouter()
const s = ref(null)
const msg = ref('')
const playing = ref(null)
const busy = ref(false)
const selecting = ref(false)  // 预留：与电影墙多选语义对齐时启用

const seasonDone = computed(() => {
  const eps = s.value?.episodes || []
  return eps.length > 0 && eps.every(e => Number(e.watched))
})
// 饰演角色仅英文原语言展示（与剧详情页同规则）
const showCharacter = computed(() =>
  String(s.value?.original_language || '').startsWith('en'))

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
async function load () {
  msg.value = ''
  try {
    s.value = await api(`/api/tv/shows/${route.params.showId}/seasons/${route.params.season}`)
  } catch (e) {
    msg.value = '加载失败：' + e.message
  }
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
  // 季内连播：重载后按本季 next 继续（跨季不串，与后端季内规则一致）
  await load()
  const nxt = s.value?.next_episode
  if (nxt && nxt.exists) play(nxt)
  else playing.value = null
}

onMounted(load)
watch(() => [route.params.showId, route.params.season], load)
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
.ov { max-width: 900px; color: #ccc; font-size: 0.875rem; line-height: 1.5; margin: 0 0 10px; }
.acts { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.acts .primary { background: #e50914; border-color: #e50914; color: #fff; }
.cast-sec { margin: 12px; }
.cast-sec h3 { margin: 0 0 10px; font-size: 1.0625rem; color: #ddd; }
.cast-wall { display: grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); gap: 10px; }
.cast-card { text-align: center; }
.avatar-fallback { width: 64px; height: 64px; margin: 0 auto 6px; border-radius: 50%; background: #2a2a2a; color: #888; font-size: 1.5rem; font-weight: bold; display: flex; align-items: center; justify-content: center; user-select: none; }
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
