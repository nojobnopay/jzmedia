<template>
  <div v-if="ep" class="tv-page">
    <div class="crumbs">
      <router-link :to="'/tv/' + ep.show_id">{{ ep.show_title }}</router-link>
      <span class="dim"> / </span>
      <router-link :to="`/tv/${ep.show_id}/s/${ep.season}`">{{ ep.season_name || seasonLabel(ep.season) }}</router-link>
      <span class="dim"> / </span>
      <span>{{ epNo(ep) }}</span>
    </div>
    <div class="hero">
      <div class="hero-inner">
        <img v-if="ep.still_path" class="hero-still" :src="stillUrl(ep)" alt=""
          @error="ep.still_path = ''" />
        <div class="hero-body">
          <h2>{{ epNo(ep) }}<span v-if="ep.title"> · {{ ep.title }}</span></h2>
          <HeroRatings :tmdb="ep.tmdb_rating" />
          <div class="meta">
            <span v-if="ep.air_date">{{ ep.air_date }}</span>
            <span v-if="ep.runtime">{{ ep.runtime }} 分钟</span>
            <span v-if="ep.watched" class="seen-tag">✓已看</span>
            <span v-if="ep.needs_review" class="review-badge">未匹配集号</span>
            <span v-if="ep.local_only" class="local-badge">本地集</span>
          </div>
          <p v-if="ep.overview" class="overview">{{ ep.overview }}</p>
          <p v-else class="empty">暂无简介</p>
          <div class="acts">
            <button v-if="ep.exists" class="primary" @click="play">
              ▶ {{ ep.progress ? '继续播放' : '播放' }}
            </button>
            <span v-else class="dim">文件缺失</span>
            <button @click="toggleWatched">{{ ep.watched ? '取消已看' : '标已看' }}</button>
            <button @click="pickOpen = !pickOpen">{{ pickOpen ? '收起' : '指定 TMDB 集' }}</button>
            <button v-if="prevEp" @click="goEpisode(prevEp.id)">‹ 上一集 {{ epNo(prevEp) }}</button>
            <button v-if="nextEp" @click="goEpisode(nextEp.id)">下一集 {{ epNo(nextEp) }} ›</button>
          </div>
          <div v-if="pickOpen" class="match card-block">
            <div class="bar match-bar">
              <span>为 <b>{{ epNo(ep) }}</b> 指定 TMDB 集（本地集号不变，只取元数据）</span>
              <input v-model.number="pickSeason" type="number" min="0" style="width: 72px" />
              <button :disabled="searching" @click="loadCandidates">查询该季</button>
            </div>
            <div class="bar match-bar">
              <span class="dim">TMDB 确实没有这一集：</span>
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
        </div>
      </div>
    </div>
    <CrewRow :directors="directors" />
    <CastWall :cast="ep.cast || []" :original-language="ep.original_language || ''"
      :subtitle="ep.cast_source === 'season' ? '本季' : ep.cast_source === 'aggregate' ? '全剧' : ''" />
  </div>
  <div v-else class="bar">{{ msg || '加载中…' }}</div>
  <PlayerModal v-if="playing" :key="'episode:' + playing.id"
    :version-id="playing.id" :title="playing.label" kind="episode"
    @close="playing = null" @watched="onWatched" @ended="onEnded" />
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api.js'
import { episodeVersion } from '../episodeVersions.js'
import PlayerModal from '../components/PlayerModal.vue'
import CastWall from '../components/CastWall.vue'
import CrewRow from '../components/CrewRow.vue'
import HeroRatings from '../components/HeroRatings.vue'

const route = useRoute()
const router = useRouter()
const ep = ref(null)
const siblings = ref([])
const msg = ref('')
const playing = ref(null)
const searching = ref(false)
const pickOpen = ref(false)
const pickSeason = ref(1)
const localTitle = ref('')
const candidates = ref([])
const searchedCand = ref(false)

const directors = computed(() => ep.value?.directors || [])
const prevEp = computed(() => {
  const i = siblings.value.findIndex(e => Number(e.id) === Number(ep.value?.id))
  return i > 0 ? siblings.value[i - 1] : null
})
const nextEp = computed(() => {
  const i = siblings.value.findIndex(e => Number(e.id) === Number(ep.value?.id))
  return i >= 0 && i < siblings.value.length - 1 ? siblings.value[i + 1] : null
})

function pad (n) { return String(n).padStart(2, '0') }
function seasonLabel (n) { return Number(n) === 0 ? '特典' : `第 ${n} 季` }
function epNo (e) {
  const base = `S${pad(e.season)}E${pad(e.episode)}`
  const end = Number(e.episode_end) > 0 ? `-E${pad(e.episode_end)}` : ''
  return base + end
}
function stillUrl (e) { return `/api/tv/episodes/${e.id}/still` }
function playLabel (e) {
  const ver = episodeVersion(e)
  return `${e.show_title} ${epNo(e)}${e.title ? ' · ' + e.title : ''}`
    + (ver > 1 ? `（V${ver}）` : '')
}
function play () {
  playing.value = { id: ep.value.id, label: playLabel(ep.value) }
}
function goEpisode (id) {
  router.push(`/tv/${ep.value.show_id}/s/${ep.value.season}/e/${id}`)
}
async function load () {
  msg.value = ''
  try {
    ep.value = await api(`/api/tv/episodes/${route.params.epId}`)
    pickSeason.value = Number(ep.value.season) || 1
    try {
      // 上下集导航：季接口已分页（limit 上限 500），逐页找当前集所在页
      //（大季才多请求，全本地 DB 开销小；失败不挡集详情）
      const base = `/api/tv/shows/${ep.value.show_id}/seasons/${ep.value.season}`
      let offset = 0
      const step = 500
      siblings.value = []
      let prevTail = null
      for (;;) {
        const d = await api(`${base}?offset=${offset}&limit=${step}`)
        const list = d.episodes || []
        const i = list.findIndex(e => Number(e.id) === Number(ep.value?.id))
        if (i >= 0) {
          // 含上一页尾，保证处在分页边界时上一集可用；下一集跨页时缺失可接受
          //（季页按范围懒加载，集详情只保证本页内导航）
          siblings.value = (i === 0 && prevTail ? [prevTail] : []).concat(list)
          break
        }
        if (!d.has_more || !list.length || offset > 10000) { siblings.value = list; break }
        prevTail = list[list.length - 1]
        offset += step
      }
    } catch (e) { siblings.value = [] }  // 上下集导航失败不挡集详情
  } catch (e) {
    msg.value = '加载失败：' + e.message
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
  searching.value = true
  searchedCand.value = false
  try {
    const d = await api(`/api/tv/shows/${ep.value.show_id}/tmdb-episodes?season=${pickSeason.value}`)
    candidates.value = d.items || []
    searchedCand.value = true
  } catch (e) { msg.value = '候选查询失败：' + e.message } finally { searching.value = false }
}
async function bindEpisode (c) {
  try {
    await api(`/api/tv/episodes/${ep.value.id}/match-episode`, {
      method: 'POST',
      body: JSON.stringify({ tmdb_episode_id: c.tmdb_episode_id, season: c.season }),
    })
    pickOpen.value = false
    candidates.value = []
    await load()
  } catch (e) { msg.value = '绑定失败：' + e.message }
}
async function confirmLocal () {
  try {
    await api(`/api/tv/episodes/${ep.value.id}/confirm-local`, {
      method: 'POST',
      body: JSON.stringify({ title: localTitle.value || null }),
    })
    pickOpen.value = false
    candidates.value = []
    await load()
  } catch (e) { msg.value = '操作失败：' + e.message }
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
  // 本季连播：标已看后播下一集（取自季列表，季终则停）
  const cur = playing.value
  await load()
  const nxt = nextEp.value
  if (cur && nxt && nxt.exists) {
    playing.value = { id: nxt.id, label: playLabel({ ...nxt, show_title: ep.value.show_title }) }
  } else {
    playing.value = null
  }
}

onMounted(load)
watch(() => route.params.epId, load)
</script>

<style scoped>
.tv-page { padding-bottom: 24px; }
.crumbs { padding: 12px 12px 0; font-size: 0.875rem; color: #aaa; }
.crumbs a { color: #9ecfff; text-decoration: none; }
.hero-inner { display: flex; gap: 18px; padding: 18px 16px; align-items: flex-start; }
.hero-still { width: min(420px, 100%); aspect-ratio: 16/9; object-fit: cover; border-radius: 8px; box-shadow: 0 6px 20px rgba(0,0,0,.6); flex: 0 0 auto; background: #222; }
.hero-body { min-width: 0; }
.hero-body h2 { margin: 0 0 6px; font-size: 1.5rem; }
.meta { display: flex; flex-wrap: wrap; gap: 10px; color: #aaa; font-size: 0.8125rem; margin-bottom: 8px; }
/* 简介与空态走 App.vue 全局 .overview/.empty 单源（与电影/剧详情同形态） */
.overview { max-width: 900px; }
.acts { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.acts .primary { background: #e50914; border-color: #e50914; color: #fff; }
.match { margin-top: 10px; max-width: 720px; }
.match-bar { padding: 0; gap: 6px; }
.match-bar input { flex: 1; }
.mrow { display: flex; align-items: center; gap: 10px; padding: 6px 0; border-bottom: 1px solid #2c2c2c; font-size: 0.875rem; }
.mrow .mname { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* 演职员/导演样式单源：CastWall.vue / CrewRow.vue */
.seen-tag { color: #7ed321; }
.review-badge { font-size: 0.75rem; padding: 1px 8px; border-radius: 999px; background: rgba(255, 179, 0, .16); color: #ffb300; border: 1px solid rgba(255, 179, 0, .4); }
.local-badge { font-size: 0.75rem; padding: 1px 8px; border-radius: 999px; background: rgba(120, 170, 255, .14); color: #7aaaff; border: 1px solid rgba(120, 170, 255, .4); }
.dim { color: #777; }
@media (max-width: 700px) {
  .hero-inner { flex-direction: column; align-items: flex-start; }
}
</style>
