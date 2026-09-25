<template>
  <div v-if="show" class="tv-page">
    <div class="hero" :style="heroStyle">
      <div class="hero-inner">
        <img v-if="show.poster_path" class="hero-poster" :src="posterSrc"
          :alt="show.title" />
        <div v-else class="hero-poster hero-no-poster">{{ (show.title || '?').slice(0, 1) }}</div>
        <div class="hero-body">
          <div class="topbar"><button @click="$router.back()">‹ 返回</button></div>
          <h2>{{ show.title }}<span v-if="show.year" class="dim"> ({{ show.year }})</span></h2>
          <HeroRatings :tmdb="show.tmdb_rating" />
          <div class="meta">
            <span v-if="show.status">{{ statusText(show.status) }}</span>
            <span v-if="show.first_air_date">{{ show.first_air_date }}</span>
            <span v-if="show.region">{{ show.region }}</span>
            <span v-if="show.episode_run_time">{{ show.episode_run_time }} 分钟/集</span>
            <span v-if="show.genres && show.genres.length">{{ show.genres.join(' / ') }}</span>
            <span v-if="show.tmdb_id" class="dim">TMDB {{ show.tmdb_id }}</span>
          </div>
          <p v-if="show.overview" class="overview">{{ show.overview }}</p>
          <p v-else class="empty">暂无简介</p>
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
            <button :disabled="busy" @click="openOrganize">整理目录</button>
            <button :disabled="verifying" @click="verifyExists" title="触网核验花絮/下一集存在性（默认只信本地）">
              {{ verifying ? '校验中…' : '校验存在性' }}
            </button>
            <span v-if="show.stale" class="dim small" title="本地态可能过期，点“校验存在性”触网核验">本地态</span>
            <span class="dim">{{ show.watched_count }}/{{ show.episode_count }} 已看</span>
            <span v-if="busy" class="dim">处理中…</span>
          </div>
          <div v-if="matchOpen" class="match card-block">
            <div class="bar match-bar">
              <input v-model="mq" placeholder="搜剧名（TMDB / 无 key 外源）" @keyup.enter="doSearch" />
              <button :disabled="searching" @click="doSearch">搜索</button>
            </div>
            <div v-for="r in results" :key="r.tmdb_id || r.source + ':' + r.source_id" class="mrow">
              <span v-if="r.source && r.source !== 'tmdb'" class="src-badge">{{ srcLabel(r.source) }}</span>
              <span class="mname">{{ r.title }}<span v-if="r.original_title && r.original_title !== r.title" class="dim"> / {{ r.original_title }}</span></span>
              <span class="dim">{{ r.year || '—' }}</span>
              <button v-if="r.tmdb_id" @click="doMatch(r.tmdb_id)">匹配</button>
              <button v-else-if="isExternal(r)" @click="doMatchExternal(r)">绑定外源</button>
            </div>
            <div v-if="searched && !results.length" class="dim">没有结果（TMDB 不可用时会自动回退本地/外源）</div>
          </div>
        </div>
      </div>
    </div>
    <CastWall :cast="castList" :original-language="show.original_language || ''" />
    <section v-if="show.seasons && show.seasons.length" class="card-block season-sec">
      <h3>剧季 <span class="dim">{{ show.seasons.length }}</span></h3>
      <div class="season-grid">
        <div v-for="s in show.seasons" :key="s.season" class="season-card"
          @click="openSeason(s.season)">
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
  <div v-else class="bar">{{ msg || '加载中…' }}</div>
  <!-- 匹配成功后的单剧整理（对标电影归档弹窗）：预览 + 两段确认直接执行本剧 -->
  <div v-if="orgHint" class="dlg-mask" @click.self="cancelOrgDialog">
    <div ref="orgDlgRef" class="dlg org-dlg" role="dialog" aria-modal="true">
      <h3>已匹配成功，可规范化本剧</h3>
      <p class="hint">{{ hintReasonText(orgHint) }}</p>
      <div class="bar org-acts">
        <label v-for="k in ORG_ACTIONS" :key="k">
          <input type="checkbox" v-model="orgActs[k]" /> {{ ACTION_LABELS[k] }}
        </label>
        <button @click="previewOrg" :disabled="orgBusy === 'plan' || !orgEnabled.length">
          {{ orgBusy === 'plan' ? '预览中…' : '预览' }}
        </button>
      </div>
      <ul class="org-list">
        <li v-for="(g, i) in (orgHint.groups || [])" :key="i">{{ groupText(g) }}</li>
      </ul>
      <p v-if="(orgHint.dir_totals || []).length" class="hint">
        最终分布（正片）：{{ dirTotalsText(orgHint) }}
      </p>
      <p v-if="orgHint.absolute_risk" class="warn-text">
        绝对集号风险：该剧依赖绝对集号映射，Plex 的季/集拆分可能不同。
        <label><input type="checkbox" v-model="orgAllowAbs" /> 我确认按 TMDB 编号改名</label>
      </p>
      <div v-if="(orgHint.manual || []).length" class="manual-block">
        <div class="g-title warn-text">需手动处理（{{ orgHint.manual.length }}{{ orgHint.manual_more ? `+${orgHint.manual_more}` : '' }}）</div>
        <div v-for="(m2, i) in (orgHint.manual || []).slice(0, 8)" :key="i" class="g-sample" :title="m2.suggestion">{{ manualText(m2) }}</div>
      </div>
      <div v-if="(orgHint.kept || []).length" class="manual-block">
        <div class="g-title">保持原名（已确认本地集，不改名）</div>
        <div v-for="(k, i) in (orgHint.kept || []).slice(0, 8)" :key="'k' + i" class="g-sample">{{ basename(k) }}</div>
      </div>
      <p v-if="(orgHint.conflicts || []).length" class="warn-text">另有 {{ orgHint.conflicts.length }} 项冲突（目标已存在，不会自动覆盖）。</p>
      <p v-if="(orgHint.warnings || []).length" class="hint">{{ (orgHint.warnings || []).join('；') }}</p>
      <div class="bar">
        <button @click="runOrganize" :disabled="orgBusy === 'exec' || !orgEnabled.length" :class="{ danger: orgArm }">
          {{ orgBusy === 'exec' ? `整理中 ${orgDone}/${orgTotal}…` : (orgArm ? '确认执行' : '直接执行本剧') }}
        </button>
        <button @click="goOrgSettings" :disabled="orgBusy === 'exec'">去设置页整理</button>
        <button @click="cancelOrgDialog" :disabled="orgBusy === 'exec'">稍后</button>
        <span>{{ orgMsg }}</span>
      </div>
      <p v-if="orgArm" class="hint warn-text">将移动目录/改正片名（花絮文件名不动），执行后自动重写 NFO/海报，可撤销。再点一次执行。</p>
    </div>
  </div>
  <PlayerModal v-if="playing" :key="playing.kind + ':' + playing.id"
    :version-id="playing.id" :title="playing.label" :kind="playing.kind"
    @close="playing = null" @watched="onWatched" @ended="onEnded" />
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, posterUrl } from '../api.js'
import { episodeVersion, seasonStats } from '../episodeVersions.js'
import { useFocusTrap } from '../useFocusTrap.js'
import {
  ACTION_LABELS, basename, dirTotalsText, groupText, hintExecBody, hintNeeds,
  hintReasonText, manualText,
} from '../tvOrganizePlans.js'
import PlayerModal from '../components/PlayerModal.vue'
import CastWall from '../components/CastWall.vue'
import HeroRatings from '../components/HeroRatings.vue'
import SimilarRow from '../components/SimilarRow.vue'

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
// 海报版本（换绑/重刮后原地覆盖同 URL，带版本破浏览器缓存；对标电影 posterVer）
const posterVer = ref(0)
// 单剧整理（匹配成功后弹窗 + 两段确认直接执行本剧，对标电影归档引导）
const ORG_ACTIONS = ['root', 'seasondir', 'wrapper', 'season', 'specials', 'extras', 'rename']
const orgHint = ref(null)
const orgMsg = ref('')
const orgBusy = ref(null)
const orgActs = ref(Object.fromEntries(ORG_ACTIONS.map((k) => [k, true])))
const orgAllowAbs = ref(false)
const orgArm = ref(false)
const orgDone = ref(0)
const orgTotal = ref(0)
const orgDlgRef = ref(null)
let orgJob = ''
let orgTimer = null
const orgEnabled = computed(() => ORG_ACTIONS.filter((k) => orgActs.value[k]))

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
const heroStyle = computed(() => {
  const b = show.value?.backdrop_path
  if (!b) return {}
  const v = posterVer.value || show.value?.fetched_at || undefined
  return { backgroundImage: `linear-gradient(90deg, rgba(10,10,10,.92) 0%, rgba(10,10,10,.55) 60%, rgba(10,10,10,.85) 100%), url(${posterUrl(b, v)})` }
})
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
// 匹配成功后查单剧整理预览：有可执行计划或风险/手动项即弹窗（对标电影归档引导）
async function checkOrgHint () {
  orgHint.value = null
  orgMsg.value = ''
  orgArm.value = false
  if (!show.value || !show.value.tmdb_id) return
  try {
    const h = await api(`/api/tv/shows/${show.value.id}/organize-hint`)
    if (hintNeeds(h)) {
      orgHint.value = h
      orgAllowAbs.value = false
      for (const k of ORG_ACTIONS) orgActs.value[k] = true
    }
  } catch (e) { /* 预览失败不挡详情页 */ }
}
// 详情页手动入口：目录已规范时给行内提示，不弹窗打扰
async function openOrganize () {
  if (!show.value || busy.value) return
  busy.value = true
  orgMsg.value = ''
  try {
    const h = await api(`/api/tv/shows/${show.value.id}/organize-hint`)
    if (hintNeeds(h) || (h.kept || []).length) {
      orgHint.value = h
      orgAllowAbs.value = false
      for (const k of ORG_ACTIONS) orgActs.value[k] = true
    } else {
      msg.value = hintReasonText(h) || '目录已规范，无需整理'
    }
  } catch (e) { msg.value = '获取整理预览失败：' + e.message } finally { busy.value = false }
}
async function previewOrg () {
  if (!show.value || !orgEnabled.value.length) return
  orgBusy.value = 'plan'
  orgMsg.value = ''
  try {
    const qs = new URLSearchParams({ actions: orgEnabled.value.join(',') })
    if (orgAllowAbs.value) qs.set('allow_absolute', 'true')
    orgHint.value = await api(`/api/tv/shows/${show.value.id}/organize-hint?${qs}`)
  } catch (e) { orgMsg.value = '预览失败：' + e.message } finally { orgBusy.value = null }
}
// 直接执行本剧：复用 /api/jobs/tv-organize（审计/撤销链路不变），两段确认
async function runOrganize () {
  if (!orgEnabled.value.length || !orgHint.value) return
  if (!orgArm.value) {
    orgArm.value = true
    orgMsg.value = ''
    return
  }
  orgArm.value = false
  orgBusy.value = 'exec'
  orgMsg.value = ''
  orgDone.value = 0
  orgTotal.value = 0
  try {
    const body = hintExecBody(orgHint.value, orgEnabled.value, orgAllowAbs.value)
    const d = await api('/api/jobs/tv-organize', {
      method: 'POST', body: JSON.stringify(body),
    })
    orgJob = d.job_id
    clearInterval(orgTimer)
    orgTimer = setInterval(pollOrg, 1200)
  } catch (e) {
    orgMsg.value = '启动失败：' + e.message
    orgBusy.value = null
  }
}
async function pollOrg () {
  try {
    const j = await api('/api/jobs/tv-organize/' + orgJob)
    orgDone.value = j.done || 0
    orgTotal.value = j.total || 0
    if (j.state === 'done') {
      clearInterval(orgTimer); orgTimer = null; orgBusy.value = null
      const s = j.summary || {}
      const moved = (s.moved || 0) + (s.renamed || 0)
      orgMsg.value = `完成：移动/改名 ${moved}，跳过 ${s.skipped || 0}`
        + (s.failed ? `，失败 ${s.failed}` : '')
      orgHint.value = null
      posterVer.value++
      await load()
      await verifyExists()
    } else if (j.state === 'failed' || j.state === 'cancelled') {
      clearInterval(orgTimer); orgTimer = null; orgBusy.value = null
      orgMsg.value = j.error || j.state
    }
  } catch (e) { /* 下一轮 */ }
}
function cancelOrgDialog () {
  if (orgBusy.value === 'exec') return
  orgArm.value = false
  orgHint.value = null
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
// 整理弹窗焦点陷阱（与电影归档弹窗同模式）；切剧/关页时停掉整理轮询
useFocusTrap(computed(() => !!orgHint.value), orgDlgRef)
onUnmounted(() => { if (orgTimer) clearInterval(orgTimer) })
</script>

<style scoped>
.tv-page { padding-bottom: 24px; }
.hero { background-size: cover; background-position: center 20%; border-bottom: 1px solid #2c2c2c; }
.hero-inner { display: flex; gap: 18px; padding: 18px 16px; align-items: flex-end; }
.hero-poster { width: 150px; aspect-ratio: 2/3; object-fit: cover; border-radius: 8px; box-shadow: 0 6px 20px rgba(0,0,0,.6); flex: 0 0 auto; }
.hero-no-poster { display: flex; align-items: center; justify-content: center; background: #242424; color: #555; font-size: 2.5rem; font-weight: bold; }
.hero-body { min-width: 0; flex: 1; }
.hero-body h2 { margin: 0 0 6px; font-size: 1.5rem; }
.topbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.meta { display: flex; flex-wrap: wrap; gap: 10px; color: #aaa; font-size: 0.8125rem; margin-bottom: 8px; }
/* 简介与空态走 App.vue 全局 .overview/.empty 单源（与电影 Detail 同形态） */
.overview { max-width: 900px; }
.acts { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.acts .primary { background: #e50914; border-color: #e50914; color: #fff; }
.match { margin-top: 10px; max-width: 720px; }
.match-bar { padding: 0; gap: 6px; }
.match-bar input { flex: 1; }
.mrow { display: flex; align-items: center; gap: 10px; padding: 6px 0; border-bottom: 1px solid #2c2c2c; font-size: 0.875rem; }
.mrow .mname { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mrow .src-badge { padding: 1px 6px; border-radius: 3px; background: #333; border: 1px solid #444; font-size: 0.75rem; color: #bbb; }
.season-sec { margin: 12px; }
.season-sec h3 { margin: 0 0 10px; font-size: 1.0625rem; color: #ddd; }
.season-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; }
.season-card { background: #1f1f1f; border: 1px solid #333; border-radius: 8px; overflow: hidden; cursor: pointer; }
.season-card:hover { border-color: #e50914; }
.season-poster { position: relative; background: #222; }
.season-poster img { width: 100%; aspect-ratio: 2/3; object-fit: cover; display: block; }
.season-no-poster { width: 100%; aspect-ratio: 2/3; display: flex; align-items: center; justify-content: center; background: #242424; color: #555; font-size: 2rem; font-weight: bold; }
.season-done { position: absolute; top: 6px; left: 6px; font-size: 0.75rem; padding: 2px 8px; border-radius: 999px; background: rgba(0,0,0,.72); color: #7ed321; }
.season-name { padding: 8px 8px 0; font-size: 0.875rem; color: #ddd; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.season-ct { padding: 2px 8px 8px; font-size: 0.75rem; color: #9ecfff; }
/* 推荐行样式单源：SimilarRow.vue */
.review-badge { margin-left: 8px; font-size: 0.75rem; padding: 1px 8px; border-radius: 999px; background: rgba(255, 179, 0, .16); color: #ffb300; border: 1px solid rgba(255, 179, 0, .4); }
.local-badge { margin-left: 8px; font-size: 0.75rem; padding: 1px 8px; border-radius: 999px; background: rgba(120, 170, 255, .14); color: #7aaaff; border: 1px solid rgba(120, 170, 255, .4); }
.ver-badge { margin-left: 6px; font-size: 0.6875rem; padding: 0 5px; border-radius: 3px; color: #b98a00; border: 1px solid #6b5410; }
.extras { margin: 12px; }
.extras h3 { margin: 0 0 10px; font-size: 1.0625rem; color: #ddd; }
/* 演职员样式单源：CastWall.vue（此处不再重复定义 cast-wall/cast-card） */
.ex-row { display: flex; gap: 10px; flex-wrap: wrap; }
.ex-card { width: 220px; padding: 8px 10px; background: #1f1f1f; border: 1px solid #333; border-radius: 8px; }
.ex-name { font-size: 0.875rem; color: #ddd; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-bottom: 4px; }
.dim { color: #777; }
.small { font-size: 0.75rem; margin-top: 2px; }
/* 单剧整理弹窗（对标电影归档弹窗 .arch-dlg，自足 scoped） */
.dlg-mask { position: fixed; inset: 0; background: rgba(0,0,0,.6); display: flex; align-items: center; justify-content: center; z-index: 60; }
.dlg { background: #1c1c1c; border: 1px solid #3a3a3a; border-radius: 10px; padding: 16px 18px; max-width: 600px; width: calc(100% - 32px); max-height: 80vh; overflow: auto; }
.dlg h3 { margin: 0 0 8px; font-size: 1.0625rem; color: #ddd; }
.hint { color: #777; font-size: 0.8125rem; margin: 0 0 4px; }
.warn-text { color: #e0a63c; }
.org-acts { flex-wrap: wrap; padding: 8px 0; font-size: 0.8125rem; }
.org-list { margin: 8px 0; padding-left: 18px; color: #bbb; font-size: 0.8125rem; }
.manual-block { margin: 4px 0 8px; }
.g-title { color: #bbb; font-size: 0.8125rem; }
.g-sample { color: #8a8a8a; padding-left: 12px; font-size: 0.8125rem; }
button.danger { border-color: #e50914; color: #ff8a8a; }
@media (max-width: 700px) {
  .hero-inner { flex-direction: column; align-items: flex-start; }
}
</style>
