<template>
  <div v-if="show" class="tv-page">
    <div class="hero" :style="heroStyle">
      <div class="hero-inner">
        <img v-if="show.poster_path" class="hero-poster" :src="posterUrl(show.poster_path)"
          :alt="show.title" />
        <div v-else class="hero-poster hero-no-poster">{{ (show.title || '?').slice(0, 1) }}</div>
        <div class="hero-body">
          <h2>{{ show.title }}<span v-if="show.year" class="dim"> ({{ show.year }})</span></h2>
          <div class="meta">
            <span v-if="show.status">{{ statusText(show.status) }}</span>
            <span v-if="show.first_air_date">{{ show.first_air_date }}</span>
            <span v-if="show.region">{{ show.region }}</span>
            <span v-if="show.episode_run_time">{{ show.episode_run_time }} 分钟/集</span>
            <span v-if="show.genres && show.genres.length">{{ show.genres.join(' / ') }}</span>
            <span v-if="show.tmdb_rating">★ {{ Number(show.tmdb_rating).toFixed(1) }}</span>
            <span v-if="show.tmdb_id" class="dim">TMDB {{ show.tmdb_id }}</span>
          </div>
          <p v-if="show.overview" class="ov">{{ show.overview }}</p>
          <p v-else class="ov dim">暂无简介（可「刷新元数据」或手动匹配）</p>
          <div class="acts">
            <button v-if="nextEp" class="primary" :disabled="!nextEp.exists" @click="play(nextEp)">
              ▶ {{ nextEp.progress ? '继续观看' : '播放下一集' }} {{ epNo(nextEp) }}
            </button>
            <button v-if="show.episodes.length" @click="toggleShowWatched">
              {{ allWatched ? '标记整剧未看' : '标记整剧已看' }}
            </button>
            <button v-if="show.needs_review" @click="confirmMatch">确认匹配</button>
            <button :disabled="busy" @click="renameShow">改名</button>
            <button :disabled="busy" @click="refreshMeta">刷新元数据</button>
            <button @click="matchOpen = !matchOpen">{{ matchOpen ? '收起匹配' : '手动匹配' }}</button>
            <span class="dim">{{ show.watched_count }}/{{ show.episode_count }} 已看</span>
            <span v-if="busy" class="dim">处理中…</span>
          </div>
          <div v-if="matchOpen" class="match card-block">
            <div class="bar match-bar">
              <input v-model="mq" placeholder="TMDB 搜剧名（可用英文原名）" @keyup.enter="doSearch" />
              <button :disabled="searching" @click="doSearch">搜索</button>
            </div>
            <div v-for="r in results" :key="r.tmdb_id" class="mrow">
              <span class="mname">{{ r.title }}<span v-if="r.original_title && r.original_title !== r.title" class="dim"> / {{ r.original_title }}</span></span>
              <span class="dim">{{ r.year || '—' }}</span>
              <button @click="doMatch(r.tmdb_id)">匹配</button>
            </div>
            <div v-if="searched && !results.length" class="dim">没有结果</div>
          </div>
        </div>
      </div>
    </div>
    <div class="bar seasons">
      <button v-for="s in show.seasons" :key="s.season" class="chip"
        :class="{ on: season === s.season }" @click="season = s.season">
        <img v-if="s.poster_path" :src="posterUrl(s.poster_path)" class="chip-poster" alt="" />
        {{ seasonLabel(s.season) }}（{{ seasonStat(s.season).distinct }}<template
          v-if="seasonStat(s.season).versions > 1"> · {{ seasonStat(s.season).versions }} 版本</template>）
      </button>
    </div>
    <table class="ep-table">
      <template v-for="g in seasonGroups" :key="'v' + g.version">
        <tr v-if="multiVersion" class="ver-sep">
          <td colspan="4">
            <span class="ver-line"></span>版本 {{ g.version }} · {{ g.episodes.length }} 集<span class="ver-line"></span>
          </td>
        </tr>
        <tr v-for="e in g.episodes" :key="e.id" :class="{ seen: e.watched }">
          <td class="ep-still">
            <img v-if="e.still_path" :src="stillUrl(e)" loading="lazy" alt=""
              @error="e.still_path = ''" />
            <span v-else class="dim">—</span>
          </td>
          <td class="ep-no">
            {{ epNo(e) }}<span v-if="multiVersion" class="ver-badge">V{{ g.version }}</span>
          </td>
          <td class="ep-title">
            <div>
              {{ e.title || '（未匹配集名）' }}
              <span v-if="e.needs_review" class="review-badge">未匹配集号</span>
              <span v-if="e.local_only" class="local-badge">本地集</span>
            </div>
            <div class="dim small">
              {{ e.air_date || '' }}
              <span v-if="e.runtime"> · {{ e.runtime }} 分钟</span>
              <span v-if="e.overview"> · {{ e.overview.slice(0, 90) }}</span>
            </div>
            <div v-if="e.progress && !e.watched" class="pbar">
              <div :style="{ width: Math.round((e.progress.percent || 0) * 100) + '%' }"></div>
            </div>
          </td>
          <td class="ep-act">
            <button v-if="e.exists" @click="play(e)">{{ e.progress ? '续播' : '播放' }}</button>
            <span v-else class="dim">文件缺失</span>
            <button v-if="e.needs_review" class="mini" @click="openEpisodePicker(e)">指定 TMDB 集</button>
            <button class="mini" @click="toggleEpWatched(e)">{{ e.watched ? '取消已看' : '标已看' }}</button>
          </td>
        </tr>
      </template>
    </table>
    <div v-if="pickEp" class="match card-block">
      <div class="bar match-bar">
        <span>为 <b>{{ epNo(pickEp) }}</b> 指定 TMDB 集（本地集号不变，只取元数据）</span>
        <input v-model.number="pickSeason" type="number" min="0" style="width: 72px" />
        <button :disabled="searching" @click="loadCandidates">查询该季</button>
        <button @click="pickEp = null">取消</button>
      </div>
      <div class="bar match-bar">
        <span class="dim">TMDB 确实没有这一集（如特别篇/合拍片）：</span>
        <input v-model="localTitle" placeholder="本地集名（可选）" style="width: 220px" />
        <button @click="confirmLocal">确认无对应集</button>
      </div>
      <div v-for="c in candidates" :key="c.tmdb_episode_id" class="mrow">
        <span class="mname">S{{ pad(c.season) }}E{{ pad(c.episode) }} · {{ c.title }}</span>
        <span class="dim">{{ c.air_date }}</span>
        <button @click="bindEpisode(c)">绑定</button>
      </div>
      <div v-if="searchedCand && !candidates.length" class="dim">该季没有候选</div>
    </div>
    <section v-if="movies.length" class="card-block extras">
      <h3>剧场版 <span class="dim">{{ movies.length }}</span></h3>
      <div class="ex-row">
        <div v-for="x in movies" :key="x.id" class="ex-card">
          <div class="ex-name" :title="baseName(x.file_path)">{{ baseName(x.file_path) }}</div>
          <div class="dim small">{{ x.label }}</div>
          <button v-if="x.exists" class="mini" @click="playExtra(x)">播放</button>
          <span v-else class="dim small">文件缺失</span>
        </div>
      </div>
    </section>
    <section v-if="features.length" class="card-block extras">
      <h3>花絮 <span class="dim">{{ features.length }}</span></h3>
      <div class="ex-row">
        <div v-for="x in features" :key="x.id" class="ex-card">
          <div class="ex-name" :title="baseName(x.file_path)">{{ baseName(x.file_path) }}</div>
          <div class="dim small">{{ x.label }}</div>
          <button v-if="x.exists" class="mini" @click="playExtra(x)">播放</button>
          <span v-else class="dim small">文件缺失</span>
        </div>
      </div>
    </section>
  </div>
  <div v-else class="bar">{{ msg || '加载中…' }}</div>
  <PlayerModal v-if="playing" :key="playing.kind + ':' + playing.id"
    :version-id="playing.id" :title="playing.label" :kind="playing.kind"
    @close="playing = null" @watched="onWatched" @ended="onEnded" />
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, posterUrl } from '../api.js'
import { episodeVersion, groupEpisodesByVersion, seasonStats } from '../episodeVersions.js'
import PlayerModal from '../components/PlayerModal.vue'

const route = useRoute()
const show = ref(null)
const msg = ref('')
const season = ref(null)
const playing = ref(null)
const busy = ref(false)
const matchOpen = ref(false)
const mq = ref('')
const results = ref([])
const searching = ref(false)
const searched = ref(false)
const pickEp = ref(null)
const pickSeason = ref(1)
const localTitle = ref('')
const candidates = ref([])
const searchedCand = ref(false)

const movies = computed(() => (show.value?.extras || []).filter(x => x.kind === 'movie'))
const features = computed(() => (show.value?.extras || []).filter(x => x.kind !== 'movie'))

const seasonEps = computed(() =>
  (show.value?.episodes || []).filter(e => Number(e.season) === Number(season.value)))
// 多版本分组：V1 全部在前、V2 在后（单版本时与原来的扁平列表一致）
const seasonGroups = computed(() => groupEpisodesByVersion(seasonEps.value))
const multiVersion = computed(() => seasonGroups.value.length > 1)
function seasonStat (sn) {
  return seasonStats((show.value?.episodes || [])
    .filter(e => Number(e.season) === Number(sn)))
}
const allWatched = computed(() => {
  const eps = show.value?.episodes || []
  return eps.length > 0 && eps.every(e => Number(e.watched))
})
const nextEp = computed(() => {
  const n = show.value?.next_episode
  if (n) return n
  return (show.value?.episodes || []).find(e => !Number(e.watched)) || null
})
const heroStyle = computed(() => {
  const b = show.value?.backdrop_path
  return b ? { backgroundImage: `linear-gradient(90deg, rgba(10,10,10,.92) 0%, rgba(10,10,10,.55) 60%, rgba(10,10,10,.85) 100%), url(${posterUrl(b)})` } : {}
})

function pad (n) { return String(n).padStart(2, '0') }
function seasonLabel (n) { return Number(n) === 0 ? '特典' : `第 ${n} 季` }
function epNo (e) {
  const base = `S${pad(e.season)}E${pad(e.episode)}`
  const end = Number(e.episode_end) > 0 ? `-E${pad(e.episode_end)}` : ''
  const abs = e.absolute_number ? ` · 绝对 ${e.absolute_number}` : ''
  return base + end + abs
}
function stillUrl (e) { return `/api/tv/episodes/${e.id}/still` }
function statusText (s) {
  if (s === 'Continuing' || s === 'Returning Series' || s === 'In Production') return '连载中'
  if (s === 'Ended' || s === 'Canceled' || s === 'Cancelled') return '已完结'
  return s
}
function play (e) {
  const ver = episodeVersion(e)
  playing.value = {
    id: e.id,
    kind: 'episode',
    label: `${show.value.title} ${epNo(e)}${e.title ? ' · ' + e.title : ''}`
      + (ver > 1 ? `（V${ver}）` : ''),
  }
}
function baseName (p) { return String(p || '').split('/').pop() }
function playExtra (x) {
  playing.value = {
    id: x.id,
    kind: 'extra',
    label: `${show.value.title} · ${x.label} · ${baseName(x.file_path)}`,
  }
}
async function openEpisodePicker (e) {
  pickEp.value = e
  pickSeason.value = Number(e.season) || 1
  localTitle.value = e.title || ''
  candidates.value = []
  searchedCand.value = false
  await loadCandidates()
}
async function loadCandidates () {
  searching.value = true
  searchedCand.value = false
  try {
    const d = await api(`/api/tv/shows/${show.value.id}/tmdb-episodes?season=${pickSeason.value}`)
    candidates.value = d.items || []
    searchedCand.value = true
  } catch (e) { msg.value = '候选查询失败：' + e.message } finally { searching.value = false }
}
async function bindEpisode (c) {
  if (!pickEp.value) return
  try {
    await api(`/api/tv/episodes/${pickEp.value.id}/match-episode`, {
      method: 'POST',
      body: JSON.stringify({ tmdb_episode_id: c.tmdb_episode_id, season: c.season }),
    })
    pickEp.value = null
    candidates.value = []
    await load()
  } catch (e) { msg.value = '绑定失败：' + e.message }
}
async function confirmLocal () {
  if (!pickEp.value) return
  try {
    await api(`/api/tv/episodes/${pickEp.value.id}/confirm-local`, {
      method: 'POST',
      body: JSON.stringify({ title: localTitle.value || null }),
    })
    pickEp.value = null
    candidates.value = []
    await load()
  } catch (e) { msg.value = '操作失败：' + e.message }
}

async function load () {
  try {
    show.value = await api('/api/tv/shows/' + route.params.id)
    if (season.value == null) {
      season.value = show.value.seasons?.[0]?.season ?? 0
    }
    if (!show.value.tmdb_id || show.value.needs_review) matchOpen.value = true
  } catch (e) {
    msg.value = '加载失败：' + e.message
  }
}

async function markWatched (episodeId, watched) {
  try {
    await api(`/api/tv/episodes/${episodeId}/watched`, {
      method: 'POST', body: JSON.stringify({ watched }) })
  } catch (e) { msg.value = '操作失败：' + e.message }
}
async function toggleEpWatched (e) {
  await markWatched(e.id, !Number(e.watched))
  await load()
}
async function toggleShowWatched () {
  busy.value = true
  try {
    await api(`/api/tv/shows/${show.value.id}/watched`, {
      method: 'POST', body: JSON.stringify({ watched: !allWatched.value }) })
    await load()
  } catch (e) { msg.value = '操作失败：' + e.message } finally { busy.value = false }
}
async function renameShow () {
  const cur = show.value.title || ''
  const next = window.prompt('剧名（手工标题后刷新/重刮不会覆盖）', cur)
  if (next == null) return
  const t = next.trim()
  if (!t || t === cur) return
  busy.value = true
  try {
    await api(`/api/tv/shows/${show.value.id}`, {
      method: 'PATCH', body: JSON.stringify({ title: t }) })
    await load()
  } catch (e) { msg.value = '改名失败：' + e.message } finally { busy.value = false }
}
async function confirmMatch () {
  try {
    await api(`/api/tv/shows/${show.value.id}/confirm-match`, { method: 'POST' })
    await load()
  } catch (e) { msg.value = '操作失败：' + e.message }
}
async function refreshMeta () {
  busy.value = true
  try {
    await api(`/api/tv/shows/${show.value.id}/refresh`, { method: 'POST' })
    await load()
  } catch (e) { msg.value = '刷新失败：' + e.message } finally { busy.value = false }
}
async function doSearch () {
  const term = mq.value.trim()
  if (!term) return
  searching.value = true
  searched.value = false
  try {
    const d = await api('/api/tv/search?q=' + encodeURIComponent(term))
    results.value = d.items || []
    searched.value = true
  } catch (e) { msg.value = '搜索失败：' + e.message } finally { searching.value = false }
}
async function doMatch (tmdbId) {
  busy.value = true
  try {
    await api(`/api/tv/shows/${show.value.id}/match`, {
      method: 'POST', body: JSON.stringify({ tmdb_id: tmdbId }) })
    matchOpen.value = false
    results.value = []
    await load()
  } catch (e) { msg.value = '匹配失败：' + e.message } finally { busy.value = false }
}

async function onWatched () {
  const cur = playing.value
  if (!cur || cur.kind !== 'episode') return
  await markWatched(cur.id, true)
  await load()
}
async function onEnded () {
  const cur = playing.value
  if (!cur || cur.kind !== 'episode') return
  try {
    const d = await api(`/api/tv/episodes/${cur.id}/next`)
    if (d.next) {
      const dv = Number(d.next.version) || 1
      playing.value = {
        id: d.next.id,
        label: `${show.value.title} S${pad(d.next.season)}E${pad(d.next.episode)}${d.next.title ? ' · ' + d.next.title : ''}`
          + (dv > 1 ? `（V${dv}）` : ''),
      }
    }
  } catch (e) { /* 连播失败：停在结束画面，用户可关窗 */ }
}

onMounted(load)
</script>

<style scoped>
.tv-page { padding-bottom: 24px; }
.hero { background-size: cover; background-position: center 20%; border-bottom: 1px solid #2c2c2c; }
.hero-inner { display: flex; gap: 18px; padding: 18px 16px; align-items: flex-end; }
.hero-poster { width: 150px; aspect-ratio: 2/3; object-fit: cover; border-radius: 8px; box-shadow: 0 6px 20px rgba(0,0,0,.6); flex: 0 0 auto; }
.hero-no-poster { display: flex; align-items: center; justify-content: center; background: #242424; color: #555; font-size: 2.5rem; font-weight: bold; }
.hero-body { min-width: 0; }
.hero-body h2 { margin: 0 0 6px; font-size: 1.5rem; }
.meta { display: flex; flex-wrap: wrap; gap: 10px; color: #aaa; font-size: 0.8125rem; margin-bottom: 8px; }
.ov { max-width: 900px; color: #ccc; font-size: 0.875rem; line-height: 1.5; margin: 0 0 10px; }
.acts { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.acts .primary { background: #e50914; border-color: #e50914; color: #fff; }
.match { margin-top: 10px; max-width: 720px; }
.match-bar { padding: 0; gap: 6px; }
.match-bar input { flex: 1; }
.mrow { display: flex; align-items: center; gap: 10px; padding: 6px 0; border-bottom: 1px solid #2c2c2c; font-size: 0.875rem; }
.mrow .mname { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.seasons { flex-wrap: wrap; }
.chip { font-size: 0.8125rem; padding: 4px 10px; border: 1px solid #444; border-radius: 999px; cursor: pointer; background: #1c1c1c; display: inline-flex; align-items: center; gap: 6px; }
.chip.on { border-color: #e50914; color: #ff8a8a; }
.chip-poster { width: 18px; height: 27px; object-fit: cover; border-radius: 3px; }
.ep-table { width: 100%; border-collapse: collapse; font-size: 0.875rem; margin-top: 8px; }
.ep-table td { padding: 8px; border-bottom: 1px solid #2c2c2c; vertical-align: top; }
.ep-table tr.seen .ep-no, .ep-table tr.seen .ep-title > div:first-child { color: #777; }
.ep-still img { width: 120px; aspect-ratio: 16/9; object-fit: cover; border-radius: 4px; display: block; }
.ep-no { color: #9ecfff; white-space: nowrap; }
.ep-title { color: #ddd; min-width: 0; }
.ep-act { white-space: nowrap; text-align: right; }
.ep-act .mini { margin-left: 6px; font-size: 0.75rem; }
.review-badge { margin-left: 8px; font-size: 0.75rem; padding: 1px 8px; border-radius: 999px; background: rgba(255, 179, 0, .16); color: #ffb300; border: 1px solid rgba(255, 179, 0, .4); }
.local-badge { margin-left: 8px; font-size: 0.75rem; padding: 1px 8px; border-radius: 999px; background: rgba(120, 170, 255, .14); color: #7aaaff; border: 1px solid rgba(120, 170, 255, .4); }
.ver-badge { margin-left: 6px; font-size: 0.6875rem; padding: 0 5px; border-radius: 3px; color: #b98a00; border: 1px solid #6b5410; }
.ver-sep td { padding: 10px 8px 4px; color: #888; font-size: 0.8125rem; border-bottom: 1px solid #2c2c2c; text-align: center; }
.ver-sep .ver-line { display: inline-block; width: 40px; height: 1px; background: #3a3a3a; vertical-align: middle; margin: 0 8px; }
.extras { margin: 12px; }
.extras h3 { margin: 0 0 10px; font-size: 1.0625rem; color: #ddd; }
.ex-row { display: flex; gap: 10px; flex-wrap: wrap; }
.ex-card { width: 220px; padding: 8px 10px; background: #1f1f1f; border: 1px solid #333; border-radius: 8px; }
.ex-name { font-size: 0.875rem; color: #ddd; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-bottom: 4px; }
.pbar { height: 4px; background: #333; border-radius: 2px; margin-top: 6px; max-width: 420px; }
.pbar > div { height: 100%; background: #e50914; border-radius: 2px; }
.dim { color: #777; }
.small { font-size: 0.75rem; margin-top: 2px; }
@media (max-width: 700px) {
  .hero-inner { flex-direction: column; align-items: flex-start; }
  .ep-still { display: none; }
}
</style>
