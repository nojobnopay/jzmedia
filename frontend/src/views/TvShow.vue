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
            <button v-if="Number(show.episode_count) > 0" @click="toggleShowWatched">
              {{ allWatched ? '标记整剧未看' : '标记整剧已看' }}
            </button>
            <button v-if="show.needs_review" @click="confirmMatch">确认匹配</button>
            <button :disabled="busy" @click="renameShow">改名</button>
            <button :disabled="busy" @click="refreshMeta">刷新元数据</button>
            <button @click="matchOpen = !matchOpen">{{ matchOpen ? '收起匹配' : '手动匹配' }}</button>
            <button :disabled="verifying" @click="verifyExists" title="触网核验花絮/下一集存在性（默认只信本地）">
              {{ verifying ? '校验中…' : '校验存在性' }}
            </button>
            <span v-if="show.stale" class="dim small" title="本地态可能过期，点“校验存在性”触网核验">本地态</span>
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
    <section v-if="castList.length" class="card-block cast-sec">
      <h3>演职员 <span class="dim">{{ castList.length }}</span></h3>
      <div class="cast-wall">
          <div v-for="p in castList" :key="p.id || p.name" class="cast-card"
          :class="{ clickable: !!Number(p.id) }" @click="goPerson(p)"
          :title="p.character ? `${p.name} 饰 ${p.character}` : p.name">
          <img v-if="p.profile_path" :src="castAvatarUrl(p.profile_path)" loading="lazy"
            class="cast-avatar" :alt="p.name || '演员'" @error="p.profile_path = ''" />
          <div v-else class="avatar-fallback" aria-hidden="true">{{ (p.name || '?').slice(0, 1) }}</div>
          <div class="cast-name">{{ p.name }}</div>
          <div v-if="showCharacter && p.character" class="cast-char">{{ p.character }}</div>
        </div>
      </div>
    </section>
    <section v-if="show.seasons && show.seasons.length" class="card-block season-sec">
      <h3>剧季 <span class="dim">{{ show.seasons.length }}</span></h3>
      <div class="season-grid">
        <div v-for="s in show.seasons" :key="s.season" class="season-card"
          @click="openSeason(s.season)">
          <div class="season-poster">
            <img v-if="s.poster_path" :src="posterUrl(s.poster_path)" loading="lazy"
              :alt="seasonLabel(s.season)" />
            <div v-else class="season-no-poster" aria-hidden="true">{{ seasonLabel(s.season).slice(0, 1) }}</div>
            <span v-if="seasonProgress(s.season).done" class="season-done">✓已看</span>
          </div>
          <div class="season-name">{{ s.name || seasonLabel(s.season) }}</div>
          <div class="dim small">{{ seasonStat(s.season).distinct }} 集<template
            v-if="seasonStat(s.season).versions > 1"> · {{ seasonStat(s.season).versions }} 版本</template>
            · {{ seasonProgress(s.season).watched }}/{{ seasonProgress(s.season).total }} 已看</div>
          <div v-if="seasonContinue(s.season)" class="season-ct">{{ seasonContinue(s.season) }}</div>
        </div>
      </div>
    </section>
    <section v-if="similar.length" class="card-block similar-sec">
      <h3>相关节目</h3>
      <div class="sim-row">
        <div v-for="m in similar" :key="m.id" class="sim-card" @click="openShow(m.id)"
          :title="m.reason || m.title">
          <img v-if="m.poster_path" :src="posterUrl(m.poster_path)" loading="lazy" :alt="m.title" />
          <div v-else class="sim-no-poster" aria-hidden="true">{{ (m.title || '?').slice(0, 1) }}</div>
          <div class="sim-name">{{ m.title }}</div>
          <div v-if="m.reason" class="dim small">{{ m.reason }}</div>
        </div>
      </div>
    </section>
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
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, castAvatarUrl, posterUrl } from '../api.js'
import { episodeVersion, seasonStats } from '../episodeVersions.js'
import PlayerModal from '../components/PlayerModal.vue'

const route = useRoute()
const router = useRouter()
const show = ref(null)
const msg = ref('')
const playing = ref(null)
const busy = ref(false)
const matchOpen = ref(false)
const mq = ref('')
const results = ref([])
const searching = ref(false)
const searched = ref(false)
const similar = ref([])
const verifying = ref(false)

const movies = computed(() => (show.value?.extras || []).filter(x => x.kind === 'movie'))
const features = computed(() => (show.value?.extras || []).filter(x => x.kind !== 'movie'))
// 演职员：后端 show_detail 透出全剧聚合前 10（只做展示，不跳人物页——
// TV 人物未入库，站内无数据）。饰演角色仅英文原语言展示（与电影 Detail 同规则，
// CJK 剧的罗马音/英文角色名读作噪音）。
const castList = computed(() => show.value?.cast || [])
const showCharacter = computed(() =>
  String(show.value?.original_language || '').startsWith('en'))
function seasonEntry (sn) {
  return (show.value?.seasons || []).find(s => Number(s.season) === Number(sn)) || null
}
function seasonStat (sn) {
  // 本地优先：剧详情不再带全量 episodes，季卡聚合由后端 seasons[] 直接给出；
  // 兼容旧响应（include_episodes=1）时回退 episodes 计算。
  const e = seasonEntry(sn)
  if (e && (e.distinct || e.versions || e.total)) {
    return { distinct: Number(e.distinct) || Number(e.total) || 0,
             versions: Number(e.versions) || 1 }
  }
  return seasonStats((show.value?.episodes || [])
    .filter(x => Number(x.season) === Number(sn)))
}
function seasonEps (sn) {
  return (show.value?.episodes || []).filter(e => Number(e.season) === Number(sn))
}
function seasonProgress (sn) {
  const e = seasonEntry(sn)
  if (e && (e.total || e.watched_count)) {
    const total = Number(e.total) || 0
    const watched = Number(e.watched_count) || 0
    return { watched, total, done: total > 0 && watched >= total }
  }
  const eps = seasonEps(sn)
  const watched = eps.filter(x => Number(x.watched)).length
  return { watched, total: eps.length, done: eps.length > 0 && watched === eps.length }
}
function seasonContinue (sn) {
  const e = seasonEntry(sn)
  if (e && (e.total || e.has_partial !== undefined)) {
    if (e.done) return ''
    if (e.has_partial) return '继续观看'
    if (e.next_episode_num) return `从 E${pad(e.next_episode_num)} 开始`
    return ''
  }
  const eps = seasonEps(sn)
  if (!eps.length) return ''
  const partial = eps.find(x => x.progress && !Number(x.watched))
  if (partial) return `继续 ${epNo(partial)}`
  const next = eps.find(x => !Number(x.watched))
  if (next) return `从 ${epNo(next)} 开始`
  return ''
}
const allWatched = computed(() => {
  const total = Number(show.value?.episode_count) || 0
  const watched = Number(show.value?.watched_count) || 0
  if (total > 0) return watched >= total
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
function openSeason (sn) { router.push(`/tv/${show.value.id}/s/${sn}`) }
function openShow (id) { router.push('/tv/' + id) }
function goPerson (p) {
  // 同步跳转、零等待：建档收敛到人物页内部（404 承接 + 建档中提示），
  // 跳转本身不再 await，杜绝连点竞态（后 resolve 的请求顶掉页面）。
  const tid = Number(p && p.id)
  if (!Number.isFinite(tid) || tid <= 0) return
  router.push({ path: '/p/' + tid,
    query: { name: p.name || '', profile: p.profile_path || '' } })
}
async function load (verify = '0') {
  msg.value = ''
  similar.value = []
  try {
    const q = verify && verify !== '0' ? '?verify=' + encodeURIComponent(verify) : ''
    show.value = await api('/api/tv/shows/' + route.params.id + q)
    if (!show.value.tmdb_id || show.value.needs_review) matchOpen.value = true
    try {
      similar.value = (await api(`/api/tv/shows/${route.params.id}/similar`)).items || []
    } catch (e) { similar.value = [] }  // 相关节目失败不挡详情页
  } catch (e) {
    msg.value = '加载失败：' + e.message
  }
}

async function verifyExists () {
  verifying.value = true
  try {
    await load('1')
  } catch (e) { msg.value = '校验失败：' + e.message } finally { verifying.value = false }
}
async function markWatched (episodeId, watched) {
  try {
    await api(`/api/tv/episodes/${episodeId}/watched`, {
      method: 'POST', body: JSON.stringify({ watched }) })
  } catch (e) { msg.value = '操作失败：' + e.message }
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

onMounted(() => load())
watch(() => route.params.id, () => load())
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
.season-sec, .similar-sec { margin: 12px; }
.season-sec h3, .similar-sec h3 { margin: 0 0 10px; font-size: 1.0625rem; color: #ddd; }
.season-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; }
.season-card { background: #1f1f1f; border: 1px solid #333; border-radius: 8px; overflow: hidden; cursor: pointer; }
.season-card:hover { border-color: #e50914; }
.season-poster { position: relative; background: #222; }
.season-poster img { width: 100%; aspect-ratio: 2/3; object-fit: cover; display: block; }
.season-no-poster { width: 100%; aspect-ratio: 2/3; display: flex; align-items: center; justify-content: center; background: #242424; color: #555; font-size: 2rem; font-weight: bold; }
.season-done { position: absolute; top: 6px; left: 6px; font-size: 0.75rem; padding: 2px 8px; border-radius: 999px; background: rgba(0,0,0,.72); color: #7ed321; }
.season-name { padding: 8px 8px 0; font-size: 0.875rem; color: #ddd; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.season-ct { padding: 2px 8px 8px; font-size: 0.75rem; color: #9ecfff; }
.sim-row { display: flex; gap: 12px; overflow-x: auto; padding-bottom: 4px; }
.sim-card { flex: 0 0 120px; width: 120px; cursor: pointer; }
.sim-card img { width: 100%; aspect-ratio: 2/3; object-fit: cover; border-radius: 8px; display: block; }
.sim-no-poster { width: 100%; aspect-ratio: 2/3; display: flex; align-items: center; justify-content: center; background: #242424; color: #555; font-size: 2rem; font-weight: bold; border-radius: 8px; }
.sim-name { margin-top: 6px; font-size: 0.8125rem; color: #ddd; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.review-badge { margin-left: 8px; font-size: 0.75rem; padding: 1px 8px; border-radius: 999px; background: rgba(255, 179, 0, .16); color: #ffb300; border: 1px solid rgba(255, 179, 0, .4); }
.local-badge { margin-left: 8px; font-size: 0.75rem; padding: 1px 8px; border-radius: 999px; background: rgba(120, 170, 255, .14); color: #7aaaff; border: 1px solid rgba(120, 170, 255, .4); }
.ver-badge { margin-left: 6px; font-size: 0.6875rem; padding: 0 5px; border-radius: 3px; color: #b98a00; border: 1px solid #6b5410; }
.extras { margin: 12px; }
.extras h3 { margin: 0 0 10px; font-size: 1.0625rem; color: #ddd; }
.cast-sec { margin: 12px; }
.cast-sec h3 { margin: 0 0 10px; font-size: 1.0625rem; color: #ddd; }
.cast-wall { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; }
.cast-card { text-align: center; }
.avatar-fallback { width: 150px; height: 150px; margin: 0 auto 6px; border-radius: 50%; background: #2a2a2a; color: #888; font-size: 2.5rem; font-weight: bold; display: flex; align-items: center; justify-content: center; user-select: none; }
.cast-avatar { width: 150px; height: 150px; margin: 0 auto 6px; border-radius: 50%; object-fit: cover; object-position: center 20%; display: block; background: #2a2a2a; transition: transform .15s ease; }
.cast-card.clickable { cursor: pointer; }
.cast-card.clickable:hover .cast-avatar { transform: scale(1.06); }
.ex-row { display: flex; gap: 10px; flex-wrap: wrap; }
.ex-card { width: 220px; padding: 8px 10px; background: #1f1f1f; border: 1px solid #333; border-radius: 8px; }
.ex-name { font-size: 0.875rem; color: #ddd; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-bottom: 4px; }
.dim { color: #777; }
.small { font-size: 0.75rem; margin-top: 2px; }
@media (max-width: 700px) {
  .hero-inner { flex-direction: column; align-items: flex-start; }
}
</style>
