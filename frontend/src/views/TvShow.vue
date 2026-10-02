<template>
  <div v-if="show" class="tv-page media-detail show-detail">
    <div class="hero media-hero">
      <MediaBackdrop :src="backdropSrc" />
      <div class="topbar hero-backbar"><router-link :to="browseReturn('/tv', currentMediaId())" class="back-link">‹ 剧集</router-link></div>
      <div class="hero-inner">
        <img v-if="show.poster_path" class="hero-poster" :src="posterSrc"
          :alt="show.title" />
        <div v-else class="hero-poster hero-no-poster" aria-hidden="true">{{ (show.title || '?').slice(0, 1) }}</div>
        <div class="hero-body">
          <div class="hero-heading">
            <h1>{{ show.title }}<span v-if="show.year" class="dim"> ({{ show.year }})</span></h1>
            <HeroRatings :tmdb="show.tmdb_rating" />
            <div class="meta">
              <span v-if="show.status">{{ statusText(show.status) }}</span>
              <span v-if="show.first_air_date">{{ show.first_air_date }}</span>
              <span v-if="show.region">{{ show.region }}</span>
              <span v-if="show.episode_run_time">{{ show.episode_run_time }} 分钟/集</span>
              <span v-if="show.genres && show.genres.length">{{ show.genres.join(' / ') }}</span>
              <span v-if="show.media_name">媒体库：{{ show.media_name }}<template
                v-if="show.library_name"> / {{ show.library_name }}</template></span>
            </div>
          </div>
          <div class="acts">
            <button v-if="nextEp" class="primary" :disabled="!nextEp.exists" @click="play(nextEp)">
              <PlayerIcon name="play" :size="20" /> {{ nextEp.progress ? '继续观看' : '播放下一集' }} {{ epNo(nextEp) }}
            </button>
            <button v-if="Number(show.episode_count) > 0" :disabled="busy" @click="toggleShowWatched">
              {{ allWatched ? '标记整剧未看' : '标记整剧已看' }}
            </button>
            <ActionMenu>
              <button :disabled="busy" @click="nameDraft = show.title; renameOpen = !renameOpen">修改剧名</button>
              <button :disabled="busy" @click="refreshMeta">更新剧集资料</button>
              <button :disabled="busy" @click="bindingsOpen = true">归属与季号</button>
              <button @click="matchOpen = !matchOpen">{{ matchOpen ? '收起匹配' : '重新匹配剧集' }}</button>
              <button :disabled="busy" @click="openOrganize">{{ organizing ? '正在发现新增分集…' : '整理剧集目录' }}</button>
              <button :disabled="verifying" @click="verifyExists">{{ verifying ? '检查中…' : '检查文件是否可用' }}</button>
            </ActionMenu>
            <span class="dim">{{ show.watched_count }}/{{ show.episode_count }} 已看</span>
            <span v-if="busy" class="dim">{{ organizing ? '正在检查本剧目录…' : '处理中…' }}</span>
          </div>
          <MediaOverview :text="show.overview || ''" />
          <EmptyState v-if="loadError" state="error" title="剧集刷新失败" :text="loadError" retry @retry="load()" />
          <p v-if="msg" class="page-feedback" role="status">{{ msg }}</p>
          <div v-if="(!show.tmdb_id && !show.match_source) || show.needs_review" class="review-notice">
            <span>{{ !show.tmdb_id && !show.match_source ? '剧集资料尚未匹配' : '请确认剧集是否匹配正确' }}</span>
            <button v-if="show.tmdb_id || show.match_source" :disabled="busy" @click="confirmMatch">匹配正确</button>
            <button :aria-expanded="matchOpen" @click="matchOpen = !matchOpen">{{ !show.tmdb_id && !show.match_source ? '匹配资料' : '重新匹配' }}</button>
          </div>
          <form v-if="renameOpen" class="inline-edit" @submit.prevent="renameShow">
            <label>剧名 <input v-model="nameDraft" required aria-label="剧名" /></label>
            <button :disabled="busy || !nameDraft.trim()">保存剧名</button>
            <button type="button" @click="renameOpen = false">取消</button>
          </form>
          <div v-if="matchOpen" class="match card-block">
            <p class="hint">只匹配其中一季或合并多个目录，可使用 <button @click="bindingsOpen = true">归属与季号</button>。</p>
            <div class="bar match-bar">
              <input v-model="mq" placeholder="输入剧名" aria-label="搜索剧集匹配" @keyup.enter="doSearch" />
              <button :disabled="searching" @click="doSearch">搜索</button>
            </div>
            <div v-for="r in results" :key="r.tmdb_id || r.source + ':' + r.source_id" class="mrow">
              <span v-if="r.source && r.source !== 'tmdb'" class="src-badge">{{ srcLabel(r.source) }}</span>
              <span class="mname">{{ r.title }}<span v-if="r.original_title && r.original_title !== r.title" class="dim"> / {{ r.original_title }}</span></span>
              <span class="dim">{{ r.year || '—' }}</span>
              <button v-if="r.tmdb_id" @click="doMatch(r.tmdb_id)">匹配</button>
              <button v-else-if="isExternal(r)" @click="doMatchExternal(r)">绑定外源</button>
            </div>
            <div v-if="searched && !results.length" class="dim">没有找到相关剧集，请尝试其他名称。</div>
            <AiMatchSuggestions kind="tv" :item-id="show.id" :disabled="busy || searching" :already-matched="!!show.tmdb_id || !!show.match_source" @select="selectAiMatch" />
          </div>
        </div>
      </div>
    </div>
    <section v-if="show.seasons && show.seasons.length" class="card-block season-sec">
      <h3>选择剧季 <span class="dim">{{ show.seasons.length }}</span></h3>
      <div class="season-grid">
        <div v-for="s in show.seasons" :key="s.season" class="season-card"
          role="link" tabindex="0" @keydown.enter.self="openSeason(s.season)" @click="openSeason(s.season)">
          <div class="season-poster">
            <img v-if="s.poster_path" :src="posterUrl(s.poster_path, posterVer || undefined)" loading="lazy"
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
    <EmptyState v-if="!show.seasons?.length" state="empty" title="暂时没有剧季" text="扫描本剧所在的视频库后，已入库的剧季会显示在这里。" />
    <CastWall :cast="castList" :original-language="show.original_language || ''" />
    <SimilarRow :items="similar" title="相关节目" subtitle="按电视网 / 类型 / 主创 / 主演推荐"
      @open="openShow" />
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
  <EmptyState v-else :state="loadError ? 'error' : 'loading'" :title="loadError ? '剧集加载失败' : '正在加载剧集'"
    :text="loadError || '请稍候…'" :retry="!!loadError" @retry="load()">
    <router-link v-if="loadError" to="/tv">返回剧集列表</router-link>
  </EmptyState>
  <TvBindingsDialog v-if="bindingsOpen && show" :library-id="Number(show.library_id)" :show-id="Number(show.id)"
    @close="bindingsOpen = false" @changed="onBindingChanged" />
  <TvOrganizeDialog v-if="orgHint" :key="orgHint.show_id" :initial-plan="orgHint"
    :title="show?.title || orgHint.title" :year="show?.year || ''"
    @close="orgHint = null" @settings="goOrgSettings" @finished="onOrganized" />
  <PlayerModal v-if="playing" :key="playing.kind + ':' + playing.id"
    :version-id="playing.id" :title="playing.label" :kind="playing.kind"
    @close="playing = null" @watched="onWatched" @ended="onEnded" />
</template>

<script setup>
import EmptyState from '../components/EmptyState.vue'
import AiMatchSuggestions from '../components/AiMatchSuggestions.vue'
import { isAiExternalCandidate } from '../aiMatch.js'
import { followingPlayback } from '../episodePlayback.js'
import { currentMediaId, loadLibs, switchMedia } from '../libraries.js'
import { browseReturn } from '../browseHistory.js'

import PlayerIcon from '../components/PlayerIcon.vue'

import MediaBackdrop from '../components/MediaBackdrop.vue'
import MediaOverview from '../components/MediaOverview.vue'
import ActionMenu from '../components/ActionMenu.vue'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, posterUrl } from '../api.js'
import { episodeVersion, seasonStats } from '../episodeVersions.js'
import TvOrganizeDialog from '../components/TvOrganizeDialog.vue'
import TvBindingsDialog from '../components/TvBindingsDialog.vue'
import { hintNeeds, hintReasonText } from '../tvOrganizePlans.js'
import PlayerModal from '../components/PlayerModal.vue'
import CastWall from '../components/CastWall.vue'
import HeroRatings from '../components/HeroRatings.vue'
import SimilarRow from '../components/SimilarRow.vue'

const route = useRoute()
const router = useRouter()
const show = ref(null)
const loadError = ref('')
let loadSeq = 0
const msg = ref('')
const playing = ref(null)
const busy = ref(false)
const organizing = ref(false)
const matchOpen = ref(false)
const renameOpen = ref(false)
const nameDraft = ref('')
const mq = ref('')
const results = ref([])
const searching = ref(false)
const searched = ref(false)
const similar = ref([])
const verifying = ref(false)
// 海报版本（换绑/重刮后原地覆盖同 URL，带版本破浏览器缓存；对标电影 posterVer）
const posterVer = ref(0)
// 预览与两次确认由独立对话框管理；此处负责入口与整理后的页面刷新。
const orgHint = ref(null)
const bindingsOpen = ref(false)

const movies = computed(() => (show.value?.extras || []).filter(x => x.kind === 'movie'))
const features = computed(() => (show.value?.extras || []).filter(x => x.kind !== 'movie'))
// 演职员：后端 show_detail 透出全剧聚合前 10（归一字段，见 cast.js）。
const castList = computed(() => show.value?.cast || [])
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
const backdropSrc = computed(() => show.value?.backdrop_path
  ? posterUrl(show.value.backdrop_path, posterVer.value || show.value.fetched_at || undefined) : '')
// 主海报版本化：同 tmdb 重复匹配/重刮会原地覆盖文件，URL 不变时靠 ?v= 取新图
const posterSrc = computed(() =>
  posterUrl(show.value?.poster_path, posterVer.value || show.value?.fetched_at || undefined))

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
async function load (verify = '0') {
  const requestedId = String(route.params.id)
  const seq = ++loadSeq
  const isCurrent = () => seq === loadSeq && String(route.params.id) === requestedId
  if (show.value && String(show.value.id) !== requestedId) show.value = null
  loadError.value = ''
  msg.value = ''
  similar.value = []
  try {
    const q = verify && verify !== '0' ? '?verify=' + encodeURIComponent(verify) : ''
    const detail = await api('/api/tv/shows/' + requestedId + q)
    if (!isCurrent()) return
    show.value = detail
    const mediaId = Number(detail.media_library_id)
    if (mediaId && mediaId !== currentMediaId()) {
      if (!switchMedia(mediaId)) {
        try {
          await loadLibs(api)
          if (isCurrent()) switchMedia(mediaId)
        } catch (e) { /* 详情内已有媒体库名称，列表刷新失败不阻断页面 */ }
      }
    }
    if (!isCurrent()) return
    try {
      const related = await api(`/api/tv/shows/${requestedId}/similar`)
      if (isCurrent()) similar.value = related.items || []
    } catch (e) {
      if (isCurrent()) similar.value = []
    }  // 相关节目失败不挡详情页
  } catch (e) {
    if (isCurrent()) loadError.value = e.message
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
  const t = nameDraft.value.trim()
  if (!t) return
  if (t === cur) { renameOpen.value = false; return }
  busy.value = true
  try {
    await api(`/api/tv/shows/${show.value.id}`, {
      method: 'PATCH', body: JSON.stringify({ title: t }) })
    renameOpen.value = false
    await load()
    msg.value = '剧名已保存'
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
    posterVer.value++
    await load()
    await waitForMedia()
    posterVer.value++
  } catch (e) { msg.value = '刷新失败：' + e.message } finally { busy.value = false }
}
// 匹配响应自带落盘状态：失败原因必须可见，不静默（与后端 media.artwork.reason 对齐）
function mediaNote (res) {
  if (!res) return ''
  if (res.offline) return '（离线绑定：用了本地缓存，未拉取最新季/集）'
  const art = (res.media && res.media.artwork) || {}
  if ((res.media && res.media.queued) || art.reason === 'queued') return 'NFO/海报后台写入中，稍后自动刷新'
  if (art.ok) return ''
  return {
    disabled: '（海报未落盘：该库未开启 NFO+海报）',
    read_only: '（海报未落盘：只读库）',
    no_backend: '（海报未落盘：存储不可用）',
    error: '（NFO/海报写入异常，详情见服务端日志）',
  }[art.reason] || ''
}
// 大剧后台落盘/慢链路时的补齐轮询：已有简介/海报立即返回，不空转
async function waitForMedia (tries = 5) {
  for (let i = 0; i < tries; i++) {
    const cur = show.value
    if (cur && cur.tmdb_id && (cur.poster_path || cur.overview)) return
    await new Promise((r) => setTimeout(r, 2000))
    try {
      const d = await api('/api/tv/shows/' + route.params.id)
      if (String(route.params.id) !== String(show.value?.id)) return
      show.value = d
      if (d.tmdb_id && (d.poster_path || d.overview)) return
    } catch (e) { /* 下一轮 */ }
  }
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
  if (busy.value) return
  busy.value = true
  try {
    const res = await api(`/api/tv/shows/${show.value.id}/match`, {
      method: 'POST', body: JSON.stringify({ tmdb_id: tmdbId }) })
    matchOpen.value = false
    results.value = []
    posterVer.value++
    await load()
    await waitForMedia()
    posterVer.value++
    const note = mediaNote(res)
    if (note) msg.value = '已匹配成功' + note
    await checkOrgHint()
  } catch (e) { msg.value = '匹配失败：' + e.message } finally { busy.value = false }
}
// 外部元数据绑定（P2.5）：无 TMDB Token 时用 Wikidata/TVmaze/Bangumi/NFO 落库
const EXTERNAL_SOURCES = ['wikidata', 'tvmaze', 'bgm', 'douban', 'nfo']
const SOURCE_LABELS = {
  wikidata: 'Wikidata', tvmaze: 'TVmaze', bgm: 'Bangumi',
  douban: '豆瓣', nfo: 'NFO', local: '本地', library: '本地库'
}
function srcLabel (s) { return SOURCE_LABELS[s] || s }
function isExternal (r) { return !r.tmdb_id && EXTERNAL_SOURCES.includes(r.source) }
async function doMatchExternal (r) {
  if (busy.value) return
  busy.value = true
  try {
    const res = await api(`/api/tv/shows/${show.value.id}/bind-external`, {
      method: 'POST', body: JSON.stringify({ source: r.source, source_id: r.source_id }) })
    matchOpen.value = false
    results.value = []
    posterVer.value++
    await load()
    posterVer.value++
    msg.value = '已绑定外源元数据（无 TMDB ID，后续可手动匹配 TMDB 升级）'
    if (res && res.episodes_filled) msg.value += `，回填 ${res.episodes_filled} 集`
  } catch (e) { msg.value = '绑定失败：' + e.message } finally { busy.value = false }
}
function selectAiMatch(candidate) {
  if (candidate.tmdb_id) return doMatch(candidate.tmdb_id)
  if (isAiExternalCandidate(candidate)) return doMatchExternal(candidate)
}
// 匹配成功后查单剧整理预览：有可执行计划或风险/手动项即弹窗（对标电影归档引导）
async function checkOrgHint () {
  orgHint.value = null
  if (!show.value || !show.value.tmdb_id) return
  try {
    const id = show.value.id
    const h = await api(`/api/tv/shows/${id}/organize-hint`)
    if (String(route.params.id) !== String(id)) return
    if (hintNeeds(h)) {
      orgHint.value = h
    }
  } catch (e) { /* 预览失败不挡详情页 */ }
}
// 详情页手动入口：先发现当前剧根的新文件，再基于最新分集清单生成整理预览。
async function openOrganize () {
  if (!show.value || busy.value) return
  busy.value = true
  organizing.value = true
  msg.value = '正在检查本剧目录中的新增分集…'
  try {
    const id = show.value.id
    const discovery = await api(`/api/tv/shows/${id}/discover`, { method: 'POST' })
    if (String(route.params.id) !== String(id)) return
    if (discovery.added) await load()
    const h = await api(`/api/tv/shows/${id}/organize-hint`)
    if (String(route.params.id) !== String(id)) return
    h.discovery = discovery
    if (hintNeeds(h) || (h.kept || []).length) {
      orgHint.value = h
      msg.value = ''
    } else {
      const notes = []
      if (discovery.added) notes.push(`已发现并加入 ${discovery.added} 个新分集`)
      if (discovery.unknown) notes.push(`另有 ${discovery.unknown} 个视频无法识别集号`)
      notes.push(hintReasonText(h) || '目录已规范，无需整理')
      msg.value = notes.join('；')
    }
  } catch (e) {
    msg.value = '检查剧集目录失败：' + e.message
  } finally {
    organizing.value = false
    busy.value = false
  }
}
async function onBindingChanged(result) {
  bindingsOpen.value = false
  if (result.show_id && Number(result.show_id) !== Number(route.params.id)) {
    await router.replace('/tv/' + result.show_id)
  } else {
    await load()
  }
}

async function onOrganized () {
  posterVer.value++
  await load()
  await verifyExists()
}
function goOrgSettings () {
  const q = { sec: 'sec-tvorganize' }
  if (show.value?.library_id != null) q.library = String(show.value.library_id)
  router.push({ path: '/settings', query: q })
}

async function onWatched () {
  const cur = playing.value
  if (!cur || cur.kind !== 'episode') return
  await markWatched(cur.id, true)
  await load()
}
async function onEnded () {
  const cur = playing.value
  if (!cur || cur.kind && cur.kind !== 'episode') return
  try {
    const next = await followingPlayback(cur, show.value.title)
    if (playing.value === cur) playing.value = next
  } catch (e) { msg.value = '自动连播失败：' + e.message }
}

onMounted(() => load())
onUnmounted(() => { loadSeq++ })
watch(() => route.params.id, () => { orgHint.value = null; bindingsOpen.value = false; load() })
</script>

<style scoped>



.topbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }

/* 简介与空态走 App.vue 全局 .overview/.empty 单源（与电影 Detail 同形态） */
.overview { max-width: 900px; }

.match { margin-top: 10px; max-width: 720px; }
.match-bar { padding: 0; gap: 6px; }
.match-bar input { flex: 1; }
.mrow { display: flex; align-items: center; gap: 10px; padding: 6px 0; border-bottom: 1px solid var(--jz-border); font-size: 0.875rem; }
.mrow .mname { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mrow .src-badge { padding: 1px 6px; border-radius: 3px; background: var(--jz-border); border: 1px solid var(--jz-border-strong); font-size: 0.75rem; color: var(--jz-text-dim); }

.season-sec h3 { margin: 0 0 10px; font-size: 1.0625rem; color: var(--jz-text); }
.season-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; }
.season-card { background: var(--jz-surface); border: 1px solid var(--jz-border); border-radius: 8px; overflow: hidden; cursor: pointer; }
.season-card:hover { border-color: var(--jz-accent); }
.season-poster { position: relative; background: var(--jz-surface-2); }
.season-poster img { width: 100%; aspect-ratio: 2/3; object-fit: cover; display: block; }
.season-no-poster { width: 100%; aspect-ratio: 2/3; display: flex; align-items: center; justify-content: center; background: var(--jz-surface-3); color: var(--jz-text-faint); font-size: 2rem; font-weight: bold; }
.season-done { position: absolute; top: 6px; left: 6px; font-size: 0.75rem; padding: 2px 8px; border-radius: 999px; background: var(--jz-overlay); color: var(--jz-green); }
.season-name { padding: 8px 8px 0; font-size: 0.875rem; color: var(--jz-text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.season-ct { padding: 2px 8px 8px; font-size: 0.75rem; color: var(--jz-link); }
/* 推荐行样式单源：SimilarRow.vue */
.review-badge { margin-left: 8px; font-size: 0.75rem; padding: 1px 8px; border-radius: 999px; background: var(--jz-warn-soft); color: var(--jz-warn); border: 1px solid var(--jz-warn-border); }
.local-badge { margin-left: 8px; font-size: 0.75rem; padding: 1px 8px; border-radius: 999px; background: var(--jz-info-soft); color: var(--jz-blue-chip); border: 1px solid var(--jz-info-border); }
.ver-badge { margin-left: 6px; font-size: 0.6875rem; padding: 0 5px; border-radius: 3px; color: var(--jz-warn); border: 1px solid var(--jz-warn-border); }
.extras { margin: 12px; }
.extras h3 { margin: 0 0 10px; font-size: 1.0625rem; color: var(--jz-text); }
/* 演职员样式单源：CastWall.vue（此处不再重复定义 cast-wall/cast-card） */
.ex-row { display: flex; gap: 10px; flex-wrap: wrap; }
.ex-card { width: 220px; padding: 8px 10px; background: var(--jz-surface); border: 1px solid var(--jz-border); border-radius: 8px; }
.ex-name { font-size: 0.875rem; color: var(--jz-text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-bottom: 4px; }
.dim { color: var(--jz-text-faint); }
.small { font-size: 0.75rem; margin-top: 2px; }

</style>
