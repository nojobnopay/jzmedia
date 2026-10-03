<template>
  <div class="detail media-detail movie-detail" v-if="m">
    <div class="hero media-hero">
      <MediaBackdrop :src="m.tmdb_id ? `/api/movies/${m.id}/backdrop?tmdb=${m.tmdb_id}` : ''" />
      <div class="hero-inner">
        <div class="topbar">
          <router-link :to="browseReturn('/', currentMediaId())" class="back-link"><AppIcon name="back" :size="16" />电影</router-link>
          <span class="top-right">
            <span v-if="savedFlash" class="saved-flash">已保存</span>
            <span v-if="regionNote" class="saved-flash" style="color:var(--jz-warn)">{{ regionNote }}</span>
            <span v-if="metaMsg" class="saved-flash" :style="metaErr ? 'color:var(--jz-warn)' : ''">{{ metaMsg }}</span>
          </span>
        </div>
        <div class="hero-main">
          <img v-if="m.poster_path" :src="posterSrc" class="poster zoomable" :alt="(m.title || '海报') + ' 海报'" title="查看大图" tabindex="0" role="button" @keydown.enter="openPoster" @keydown.space.prevent="openPoster" @click="openPoster" />
          <div v-else class="poster poster-empty" aria-hidden="true">{{ (m.title || '?').slice(0, 1) }}</div>
          <div class="hero-info">
            <div class="hero-heading">
              <h1>{{ m.title }} <span v-if="m.year" class="year">({{ m.year }})</span></h1>
              <div v-if="m.edition || m.spec || m.watched" class="detail-badges"><span v-if="m.edition" class="edition-chip">{{ m.edition }}</span><span v-if="m.spec" class="edition-chip spec">{{ m.spec }}</span><span v-if="m.watched" class="watched-chip">✓ 已看</span></div>
              <HeroRatings :tmdb="m.tmdb_rating" :douban="m.douban_rating" :custom="m.custom_rating" />
              <p v-if="metaLine" class="meta-line">{{ metaLine }}</p>
            </div>
            <div class="play-row">
              <JzButton class="play-main" variant="primary" :disabled="heroBlocked" :title="heroBlockTip" @click="openHeroPlay"><PlayerIcon name="play" :size="20" /> {{ heroResume ? '继续观看' : '播放影片' }}</JzButton>

              <JzButton :loading="watchedBusy" @click="toggleWatched">{{ m.watched ? '标记未看' : '标记已看' }}</JzButton>
              <ActionMenu>
                <button @click="openEdit('edit')">编辑资料</button>
                <button @click="openEdit('match')">重新匹配</button>
                <button @click="refreshMovieInfo" :disabled="refreshBusy">{{ refreshBusy ? '更新中…' : '更新资料' }}</button>
                <button :disabled="metaBusy" title="选择需要修复的资料文件" @click="repairOpen = !repairOpen">修复资料文件</button>
                <button @click="collectionsOpen = !collectionsOpen">管理所属合集</button>
              </ActionMenu>
            </div>
            <div v-if="(m.versions || []).length > 1 || heroResume" class="play-options">
              <select v-if="(m.versions || []).length > 1" v-model.number="heroVid" class="ver-sel" aria-label="播放版本">
                <option v-for="v in m.versions" :key="v.id" :value="v.id" :disabled="!!verBlocked[v.id]">
                  {{ verLabel(v) }}{{ verBlocked[v.id] ? '（无效）' : (verFriendly(v.id) ? ' ★浏览器友好' : '') }}
                </option>
              </select>
              <span v-if="heroResume" class="resume-hint">{{ heroResume }}</span>
            </div>
            <MediaOverview :text="m.overview_display || ''" />
            <div v-if="(!m.tmdb_id && !m.match_source) || m.needs_review" class="review-notice">
              <span>{{ !m.tmdb_id && !m.match_source ? '影片资料尚未匹配' : '请确认影片是否匹配正确' }}</span>
              <button v-if="m.tmdb_id || m.match_source" :disabled="!!nrBusy" @click="confirmMatch">匹配正确</button>
              <button :aria-expanded="editing === 'match'" @click="openEdit('match')">{{ !m.tmdb_id && !m.match_source ? '匹配资料' : '重新匹配' }}</button>
            </div>
            <div v-if="mediaBadge || mediaUnplayable || noFfmpeg" class="media-row">
              <span v-if="mediaBadge" class="media-badge">{{ mediaBadge }}</span>
              <span v-if="mediaUnplayable" class="media-warn" :title="mediaError">无效文件，无法播放</span>
              <span v-if="noFfmpeg" class="media-warn" title="服务器缺 ffmpeg：转码/重封装不可用，直链与电视播放不受影响">转码不可用（缺 ffmpeg）</span>
            </div>
            <div v-else-if="mediaLoading" class="media-row"><span class="media-loading">媒体信息探测中…</span></div>
            <div v-if="repairOpen" class="bar" style="flex-wrap:wrap">
              <label>修复内容 <select v-model="repairMode" :disabled="metaBusy"><option value="both">NFO 与海报</option><option value="nfo">仅 NFO</option><option value="art">仅海报</option></select></label>
              <button @click="rebuildMeta" :disabled="metaBusy">{{ metaBusy ? '修复中…' : '确认修复本片资料文件' }}</button>
              <button @click="repairOpen = false" :disabled="metaBusy">收起</button>
            </div>
              <details class="playback-preparation" v-if="!heroBlocked && (!verFriendly(heroVid) || (heroRemux && heroRemoteLib))" >
                <summary>预缓存与转码</summary>
                <p class="fhint">提前准备影片可减少下次起播等待，缓存有效期为 24 小时。</p>
                <div class="pre-wrap"><label for="pre-quality">准备画质</label>
                <select id="pre-quality" v-model="preQuality" aria-label="预缓存与转码画质" :disabled="!!preJob" class="pre-sel"
                  :title="heroRemux && heroRemoteLib
                    ? '预缓存目标：远程库建议「原画」（无损 copy、几乎不耗 CPU；选 720p/1080p 会真转码）'
                    : '预转码目标：自动=按服务器能力（无硬件转码→720p，有硬件→1080p）'">
                  <option value="auto">自动</option>
                  <option value="1080p">1080p</option>
                  <option value="720p">720p</option>
                  <option value="source">原画</option>
                </select>
                <button class="pre-btn" :disabled="!!preJob"
                  :title="heroRemux && heroRemoteLib
                    ? '把整片缓存到服务器本地（远程库读盘慢）：完工后点播零 NAS 读取、秒开，24h 内有效'
                    : '夜间/闲时把本片转好存着，完工后点播即静态秒播'"
                  @click="startPrewarm">{{ preJob
                    ? (heroRemux && heroRemoteLib ? '预缓存中…' : '预转码中…')
                    : (heroRemux && heroRemoteLib ? '预缓存到服务器' : '开始预转码') }}</button>
              </div>
              </details>
            <p v-if="preMsg" class="page-feedback" role="status">{{ preMsg }}</p>
            <div v-if="(m.tags || []).length" class="tag-row">
              <span v-for="t in m.tags" :key="t" class="tag-chip">{{ t }}</span>
            </div>
            <div v-if="(m.collections || []).length" class="tag-row">
              <router-link v-for="c in m.collections" :key="c.id" class="col-chip" :to="'/c/' + c.id">{{ c.name }}</router-link>
            </div>
            <MovieCollectionsPanel v-if="collectionsOpen" :movie-id="m.id" :library-id="m.library_id"
              @changed="onEditChanged" @close="collectionsOpen = false" />
            <p v-if="refreshMsg" class="page-feedback" role="status">{{ refreshMsg }}</p>

          </div>
        </div>
      </div>
    </div>

    <div class="sections">
      <p v-if="loadErr" class="page-feedback" role="alert">{{ loadErr }}</p>
      <p v-if="msg" class="page-feedback" role="status">{{ msg }}</p>
      <MovieEditPanel v-if="editing" :key="editing + ':' + route.params.id" :mode="editing" :movie="m" :movie-id="Number(route.params.id)"
        @close="editing = ''" @saved="onEditSaved"
        @matched="onMatched" />
      <nav class="detail-tabs" aria-label="影片内容">
        <button :class="{ active: contentTab === 'info' }" :aria-pressed="contentTab === 'info'" @click="contentTab = 'info'">影片资料</button>
        <button :class="{ active: contentTab === 'files' }" :aria-pressed="contentTab === 'files'" @click="contentTab = 'files'">文件与版本 <span>{{ (m.versions || []).length }}</span></button>
      </nav>
            <div v-if="contentTab === 'info' && hint && hint.collection_tmdb_id" class="hint-row">
              TMDB 系列：{{ hint.collection_name }}（库内 {{ hint.in_library_count }} 部）
              <button v-if="!hint.already_collected" @click="createFromSeries">创建系列合集</button>
              <span v-else class="fhint">已收录</span>
              <span>{{ hintMsg }}</span>
            </div>
      <div v-show="contentTab === 'info'" class="body-grid" :class="{ 'facts-only': !actors.length && !directors.length }">
        <div v-if="actors.length || directors.length" class="main-col">

          <CrewRow :directors="directors" />
          <CastWall :cast="actors" :original-language="origLang" />

        </div>

        <aside class="side-col">
          <section class="card-block facts">
            <h3>影片信息</h3>
            <div v-if="m.original_title" class="fact"><span>原标题</span><span>{{ m.original_title }}</span></div>
            <div v-if="(m.genres || []).length" class="fact"><span>类型</span><span>{{ (m.genres || []).join(' / ') }}</span></div>
            <div v-if="m.region || originName" class="fact"><span>产地</span><span>{{ [m.region, originName].filter(Boolean).join(' · ') }}</span></div>
            <div v-if="m.year" class="fact"><span>年份</span><span>{{ m.year }}</span></div>
            <div v-if="fmtDate(m.added_at)" class="fact"><span>入库</span><span>{{ fmtDate(m.added_at) }}</span></div>
            <div v-if="originalMoved" class="fact"><span>原始文件</span><span class="fact-val" :title="m.original_file_path">{{ m.original_file_path }} <button @click="goRestore">去恢复</button></span></div>
            <div v-if="m.tmdb_id" class="fact"><span>链接</span><span><a :href="`https://www.themoviedb.org/movie/${m.tmdb_id}`" target="_blank" rel="noopener">TMDB</a><a v-if="m.imdb_id" :href="`https://www.imdb.com/title/${m.imdb_id}/`" target="_blank" rel="noopener">IMDb</a></span></div>
          </section>
        </aside>
      </div>

      <div v-show="contentTab === 'files'" class="file-sections">
          <MovieFileManager :movie-id="Number(route.params.id)" :movie="m" :side-files="sideFiles"
            :ver-blocked="verBlocked" :ver-err="verErr" :ver-method="verMethod" :ver-friendly="verFriendly"
            @play="openStream" @changed="onFilesChanged" />
          <MovieUploadPanel :movie-id="Number(route.params.id)" @uploaded="load" />
      </div>

      <SimilarRow v-if="contentTab === 'info'" :items="similar" title="库中类似" subtitle="按系列 / 影人 / 类型 / 标签推荐"
        @open="(id) => $router.push('/m/' + id)" />

    </div>

    <PlayerModal v-if="playVid" :key="'movie:' + playVid" ref="playerRef" :versionId="playVid" :title="playTitle"
      kind="movie" @close="closeStream" @watched="onPlayEnded" />

    <div v-if="posterDlg" class="dlg-mask" @click.self="closePoster">
      <div ref="posterDlgRef" class="dlg pv-dlg poster-dlg" role="dialog" aria-modal="true">
        <template v-if="!posterPick">
          <img :src="posterBig" class="pv-img poster-big" />
          <div class="bar"><span class="hint">{{ posterHi ? '高清原图' : '标清预览（原图加载中或不可用）' }}</span><a :href="posterBig" :download="baseName(posterBig)">下载</a><button @click="openPosterPicker">换海报</button><button @click="closePoster">关闭</button></div>
        </template>
        <template v-else>
          <div class="poster-pick-head">
            <b>选择海报</b>
            <span class="hint">来自 TMDB，按分辨率排序（当前选择高亮）</span>
          </div>
          <p v-if="posterErr" class="warn-text">{{ posterErr }}</p>
          <p v-if="posterLoading" class="hint">候选加载中…</p>
          <div v-else class="poster-grid">
            <button v-for="c in posterCands" :key="c.file_path" class="poster-cand"
              :class="{ cur: c.current }" :disabled="posterSaving"
              :title="`${c.width}×${c.height}${c.lang ? ' · ' + c.lang : ''}`"
              @click="choosePoster(c)">
              <img :src="c.thumb_url" loading="lazy" :alt="`${c.width}×${c.height}`" />
              <span class="poster-cand-meta">{{ c.width }}×{{ c.height }}</span>
            </button>
          </div>
          <div class="bar">
            <button :disabled="posterSaving" @click="posterPick = false">返回</button>
            <button @click="closePoster">关闭</button>
          </div>
        </template>
      </div>
    </div>
  </div>
  <!-- 重新匹配后的归档推荐（评审 B9 后续）：有推荐路径就弹窗，不让用户自己去设置页找 -->
  <div v-if="archHint" class="dlg-mask" @click.self="archHint = null">
    <div ref="archDlgRef" class="dlg arch-dlg" role="dialog" aria-modal="true">
      <h3>已匹配成功，可以归档了</h3>
      <p class="hint">检测到推荐的正式库路径，归档后可避免后续迁移：</p>
      <ul class="arch-list">
        <li v-for="(p, i2) in archHint.plans" :key="i2">
          <span class="arch-from">{{ p.from }}</span>
          <span class="arch-arrow">→</span>
          <span class="arch-to">{{ p.to }}</span>
        </li>
      </ul>
      <p v-if="(archHint.conflicts || []).length" class="warn-text">
        另有 {{ archHint.conflicts.length }} 项冲突（疑似错配），请先核对匹配再归档。
      </p>
      <div class="bar">
        <button @click="doArchive" :disabled="archApplying">{{ archApplying ? '归档中…' : '立即归档' }}</button>
        <button @click="archHint = null" :disabled="archApplying">稍后整理</button>
        <span>{{ archMsg }}</span>
      </div>
    </div>
  </div>
  <EmptyState v-if="!m" :state="loadErr ? 'error' : 'loading'" :title="loadErr ? '影片资料加载失败' : '正在加载影片'" :text="loadErr || '正在读取影片资料…'" :retry="!!loadErr" @retry="load" />
</template>
<script setup>
import { browseReturn } from '../browseHistory.js'

import PlayerIcon from '../components/PlayerIcon.vue'
import JzButton from '../components/JzButton.vue'
import AppIcon from '../components/AppIcon.vue'

import MediaBackdrop from '../components/MediaBackdrop.vue'
import MediaOverview from '../components/MediaOverview.vue'
import ActionMenu from '../components/ActionMenu.vue'
import { ref, computed, onMounted, onBeforeUnmount, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, posterUrl } from '../api.js'
import { usePolling } from '../usePolling.js'
import { getCaps } from '../caps.js'
import { isPlaybackComplete } from '../progress.js'
import HeroRatings from '../components/HeroRatings.vue'
import SimilarRow from '../components/SimilarRow.vue'
import EmptyState from '../components/EmptyState.vue'
import { fmtDate } from '../format.js'
import PlayerModal from '../components/PlayerModal.vue'
import CastWall from '../components/CastWall.vue'
import CrewRow from '../components/CrewRow.vue'
import MovieUploadPanel from '../components/MovieUploadPanel.vue'
import MovieFileManager from '../components/MovieFileManager.vue'
import MovieCollectionsPanel from '../components/MovieCollectionsPanel.vue'
import MovieEditPanel from '../components/MovieEditPanel.vue'
import { useFocusTrap } from '../useFocusTrap.js'
import { currentMediaId, isRemoteVideoLib } from '../libraries.js'

const route = useRoute()
const router = useRouter()
let disposed = false
let loadGeneration = 0
let mediaGeneration = 0
function currentMovieId() {
  const id = String(route.params.id || '')
  // The route updates before this component unmounts when returning to a wall.
  if (!/^[1-9]\d*$/.test(id) || !Number.isSafeInteger(Number(id))) return null
  if (route.path && !/^\/m\/[1-9]\d*\/?$/.test(route.path)) return null
  return id
}
function loadContext() { return { id: currentMovieId(), generation: loadGeneration } }
function currentLoad(context) {
  return !disposed && context.id != null && context.id === currentMovieId() && context.generation === loadGeneration
}
const m = ref(null)
const sideFiles = ref(null)
const msg = ref('')
const loadErr = ref('')
const hint = ref(null)
const hintMsg = ref('')
const posterDlgRef = ref(null)
const archDlgRef = ref(null)
const regionNote = ref('')
// 库中类似（Plex 式推荐）：后端纯本地相似度，失败静默不挡详情页
// 渲染与滚动收敛到 SimilarRow.vue 单源
const similar = ref([])
const archHint = ref(null)
const archApplying = ref(false)
const archMsg = ref('')
const editing = ref('')
const collectionsOpen = ref(false)
const refreshBusy = ref(false)
const refreshMsg = ref('')
const contentTab = ref('info')
const savedFlash = ref(false)
const watchedBusy = ref(false)
let flashTimer = null
const actors = computed(() => (m.value?.persons || []).filter(p => p.role === 'actor'))
const directors = computed(() => (m.value?.persons || []).filter(p => p.role === 'director'))
const origLang = computed(() => m.value?.original_language || '')
// 主产地中文名由后端下发（origin_country_name；评审 B5a-3/R10-D6），前端不再维护映射
const originName = computed(() => (m.value && m.value.origin_country_name) || '')
const metaLine = computed(() => {
  const parts = []
  if (m.value?.year) parts.push(m.value.year)
  const origin = [m.value?.region, originName.value].filter(Boolean).join('·')
  if (origin) parts.push(origin)
  const genres = (m.value?.genres || []).slice(0, 3).join('/')
  if (genres) parts.push(genres)
  return parts.join(' · ')
})
// 在线播放 P0：版本媒体徽章 + 断点提示（播放器弹窗在 P1–P4 接入）
const mediaInfo = ref(null)
const mediaLoading = ref(false)
const mediaError = ref('')
const noFfmpeg = ref(false)
const progressInfo = ref(null)
const mediaBadge = computed(() => {
  const mi = mediaInfo.value
  if (!mi || !mi.playable) return ''
  const parts = []
  if (mi.duration_text) parts.push(mi.duration_text)
  const h = Number(mi.height) || 0
  if (h >= 2000) parts.push('4K')
  else if (h >= 1000) parts.push('1080p')
  else if (h >= 650) parts.push('720p')
  else if (h > 0) parts.push(h + 'p')
  if (mi.vcodec) parts.push(String(mi.vcodec).toUpperCase())
  if (Number(mi.bit_depth) > 8) parts.push(mi.bit_depth + 'bit')
  if (Number(mi.dv_profile) > 0) parts.push('DV P' + mi.dv_profile)
  else if (mi.hdr) parts.push(String(mi.hdr).toUpperCase())
  const na = (mi.audio || []).length
  if (na > 1) parts.push(`音频${na}轨`)
  else if (na === 1 && mi.acodec) parts.push(String(mi.acodec).toUpperCase())
  const ns = (mi.subs || []).length
  if (ns) parts.push(`字幕${ns}`)
  return parts.join(' · ')
})
const mediaUnplayable = computed(() => mediaInfo.value && !mediaInfo.value.playable)
const resumeText = computed(() => {
  const p = progressInfo.value
  const pos = Number(p?.position) || 0
  if (!p || !(pos > 15)) return ''
  let dur = Number(p.duration) || 0
  // 旧版本用 HLS 增长清单时长写坏过存档：dur < position 视为不可信，回落探测总长
  if (dur > 0 && dur < pos) dur = Number(mediaInfo.value?.duration) || 0
  if (!dur) dur = Number(mediaInfo.value?.duration) || 0
  if (isPlaybackComplete(pos, dur)) return ''
  return `上次看到 ${p.position_text || ''}`
})
async function loadMedia(context = loadContext()) {
  if (!currentLoad(context)) return
  const generation = ++mediaGeneration
  const current = () => currentLoad(context) && generation === mediaGeneration
  mediaLoading.value = true
  mediaError.value = ''
  // P1：一次取齐全版本（媒体+四档决策+最优版），带客户端实测 caps（打分随能力变化）
  try {
    const caps = await getCaps()
    if (!current()) return
    const agg = await api('/api/stream/versions', {
      method: 'POST',
      body: JSON.stringify({ movie_id: Number(context.id), quality: 'auto', caps }),
    })
    if (!current()) return
    verList.value = agg.versions || []
    bestVid.value = agg.best_version_id || null
    const cur = verList.value.find(x => Number(x.version_id) === Number(context.id))
    mediaInfo.value = cur && cur.playable
      ? { playable: true, duration_text: cur.duration_text, duration: cur.duration,
          height: cur.height, vcodec: cur.vcodec, acodec: '',
          hdr: cur.hdr || '', dv_profile: cur.dv_profile || 0,
          bit_depth: cur.bit_depth || 0,
          audio: new Array(cur.audio_count).fill({}), subs: new Array(cur.sub_count).fill({}) }
      : (cur ? { playable: false, probe_error: cur.probe_error } : null)
    if (mediaInfo.value && !mediaInfo.value.playable) {
      mediaError.value = mediaInfo.value.probe_error || '无法识别媒体流'
    }
    for (const x of verList.value) {
      if (!x.playable || x.method === 'blocked') {
        verBlocked.value[x.version_id] = true
        verErr.value[x.version_id] = x.probe_error || '无法识别媒体流'
      }
      verMethod.value[x.version_id] = x.method
    }
    // hero 默认选中浏览器最优版（无效时回落当前行）
    heroVid.value = bestVid.value || Number(context.id)
  } catch (e) {
    if (!current()) return
    mediaInfo.value = null
    mediaError.value = String(e.message || e)
    heroVid.value = Number(context.id)
  } finally {
    if (current()) mediaLoading.value = false
  }
  if (!current()) return
  try {
    const p = await api(`/api/stream/progress?version_id=${heroVid.value}`)
    if (!current()) return
    progressInfo.value = (p && Number(p.position) > 0) ? p : null
  } catch (e) { if (current()) progressInfo.value = null }
}
// 在线播放 P1：版本选播弹窗（播放单位=版本行 id）+ 播完标已看（阈值逻辑 P3 进弹窗内）
const playVid = ref(null)
const playerRef = ref(null)   // PlayerModal 暴露 saveFinal：关窗后等最终断点落库再刷新
const playTitle = ref('')
const verBlocked = ref({})
const verErr = ref({})
const verMethod = ref({})
const verList = ref([])
const bestVid = ref(null)
// “浏览器友好”= direct/remux/audio_transcode（零/近零 CPU：仅换容器或只转音轨），视频重编版不打标
const verFriendly = (id) => ['direct', 'remux', 'audio_transcode'].includes(verMethod.value[id])
// P4 hero 主播放键：默认当前行，多版本可下拉切换（无效版本禁用）
const heroVid = ref(null)
const heroBlocked = computed(() => !!verBlocked.value[heroVid.value])
const heroBlockTip = computed(() => verErr.value[heroVid.value] || '')
// 预缓存入口（P1）：remux/audio_transcode 本不需要转码，但远程库读盘慢——整片缓存到服务端
// 本地后点播零 NAS 读取。direct 无需缓存；本地库读取快也不必（保持旧按钮仅给转码版）。
const heroRemux = computed(() => ['remux', 'audio_transcode'].includes(verMethod.value[heroVid.value]))
const heroRemoteLib = computed(() => {
  const v = (m.value?.versions || []).find(x => Number(x.id) === Number(heroVid.value))
  return isRemoteVideoLib(v?.library_id ?? m.value?.library_id)
})
const heroResume = computed(() => resumeText.value)
function baseName(p) {
  return String(p || '').split('/').pop()
}
function verLabel(v) {
  const extra = [v.edition, v.spec].filter(Boolean).join('·')
  return baseName(v.file_path) + (extra ? `（${extra}）` : '')
}
function openHeroPlay() {
  if (heroBlocked.value || !heroVid.value) return
  const v = (m.value?.versions || []).find(x => Number(x.id) === Number(heroVid.value))
  openStream(v || { id: heroVid.value, file_path: m.value?.file_path || '' })
}
function openStream(v) {
  playTitle.value = baseName(v.file_path)
  playVid.value = Number(v.id)
}
// 预转码：闲时把本片转完存静态，完工后点播秒播；只给需转码版显示
const preJob = ref(null)
const preQuality = ref('auto')
const preMsg = ref('')
const prePoll = usePolling(pollPrewarm, { interval: 5000 })
async function pollPrewarm() {
  if (!preJob.value) return
  try {
    const s = await api(`/api/stream/prewarm/${preJob.value}`)
    const exp = Number(s.expected) || 0
    const got = Number(s.segments) || 0
    if (s.status === 'done') {
      preMsg.value = '已就绪，点播即秒播'
      preJob.value = null
      prePoll.stop()
      loadMedia()
    } else if (s.status === 'failed') {
      preMsg.value = '失败：' + (s.error || '未知').slice(0, 80)
      preJob.value = null
      prePoll.stop()
    } else if (exp > 0) {
      preMsg.value = `转码中 ${Math.floor((got / exp) * 100)}%（${got}/${exp}）`
    }
  } catch (e) { /* 轮询失败下次继续 */ }
}
async function startPrewarm() {
  if (preJob.value || !heroVid.value) return
  preMsg.value = ''
  try {
    // 带上本机实测 caps（评审 B5a-7）：预转码产物键与在线播一致，完工后点播才能命中
    const caps = await getCaps()
    const r = await api('/api/stream/prewarm', {
      method: 'POST',
      body: JSON.stringify({ version_id: Number(heroVid.value), quality: preQuality.value, audio: 0, caps }),
    })
    preJob.value = r.job_id
    preMsg.value = '已开始，后台转码中…'
    prePoll.start()
  } catch (e) {
    preMsg.value = '启动失败：' + e.message
  }
}
// 重写元数据（2026-09）：换绑/刷新后 NAS 上 NFO/海报可能没落盘，这里按当前匹配单部重写
const repairOpen = ref(false)
const repairMode = ref('both')
const metaBusy = ref(false)
const metaMsg = ref('')
const metaErr = ref(false)
let metaJobId = ''
const metaPoll = usePolling(pollMeta, { interval: 1000 })
async function rebuildMeta() {
  if (metaBusy.value) return
  metaBusy.value = true
  metaMsg.value = ''
  metaErr.value = false
  try {
    const d = await api('/api/jobs/rebuild-meta', {
      method: 'POST',
      body: JSON.stringify({ ids: [Number(route.params.id)], dry_run: false, backdrops: false, nfo: repairMode.value !== 'art', artwork: repairMode.value !== 'nfo' }),
    })
    metaJobId = d.job_id || ''
    if (!metaJobId) {
      metaBusy.value = false
      metaErr.value = true
      metaMsg.value = '重写启动失败'
      return
    }
    metaMsg.value = '后台重写中…'
    metaPoll.start()
  } catch (e) {
    metaBusy.value = false
    metaErr.value = true
    metaMsg.value = '重写失败：' + e.message
  }
}
async function pollMeta() {
  if (!metaJobId) return
  try {
    const st = await api('/api/jobs/rebuild-meta/' + metaJobId)
    if (st.state === 'running') {
      metaMsg.value = `重写中 ${st.done}/${st.total}…`
      return
    }
    metaPoll.stop()
    metaJobId = ''
    metaBusy.value = false
    if (st.state === 'done') {
      const failed = (st.failed || []).length
      metaErr.value = failed > 0
      metaMsg.value = failed ? `完成，失败 ${failed} 部` : '已修复所选资料文件'
      await load()
    } else {
      metaErr.value = true
      metaMsg.value = '重写失败：' + (st.error || st.state || '未知')
    }
  } catch (e) { /* 轮询失败下次继续 */ }
}
function closeStream() {
  const vid = playVid.value
  // 关播：先同步取播放器的最终存档 Promise（此时组件未卸载、位置=关闭这一刻），
  // 再关窗；落库完成后再 GET 刷新「上次看到」。不走 saved 事件：组件卸载后 emit 会被 Vue 丢弃。
  const saving = (playerRef.value && playerRef.value.saveFinal)
    ? playerRef.value.saveFinal() : Promise.resolve()
  playVid.value = null
  Promise.resolve(saving).catch(() => { /* 忽略 */ }).then(() => refreshProgress(vid))
}
async function refreshProgress(vid) {
  // 刷新断点显示（仅当前 hero 版本；非 hero 版本本就不展示「上次看到」）
  if (!vid || Number(vid) !== Number(heroVid.value)) return
  try {
    const p = await api(`/api/stream/progress?version_id=${vid}`)
    progressInfo.value = (p && Number(p.position) > 0) ? p : null
  } catch (e) { /* 忽略 */ }
}
async function toggleWatched() {
  if (watchedBusy.value || !m.value) return
  watchedBusy.value = true
  msg.value = ''
  try {
    await api('/api/movies/batch', {
      method: 'POST', body: JSON.stringify({ ids: [m.value.id], ops: { watched: !m.value.watched } }),
    })
    m.value = await api('/api/movies/' + route.params.id)
    flashSaved()
  } catch (e) { msg.value = '观看状态保存失败：' + e.message }
  finally { watchedBusy.value = false }
}
async function onPlayEnded() {
  // 海报粒度：同片全版本同步标已看（批量接口一次完成，评审 B8/R05-D5）
  try {
    await api('/api/movies/batch', {
      method: 'POST',
      body: JSON.stringify({ ids: [Number(route.params.id)], ops: { watched: true } }),
    })
    m.value = await api('/api/movies/' + route.params.id)
    flashSaved()
  } catch (e) { /* 忽略 */ }
}

async function load() {
  const context = { id: currentMovieId(), generation: ++loadGeneration }
  if (!currentLoad(context)) return
  regionNote.value = ''
  loadErr.value = ''
  if (m.value && String(m.value.id) !== context.id) {
    m.value = null
    sideFiles.value = null
    hint.value = null
    mediaInfo.value = null
    progressInfo.value = null
    verBlocked.value = {}
    verErr.value = {}
    verMethod.value = {}
  }
  try {
    const movie = await api('/api/movies/' + context.id)
    if (!currentLoad(context)) return
    m.value = movie
  } catch (e) {
    if (!currentLoad(context)) return
    loadErr.value = '加载失败：' + e.message
    return
  }
  heroVid.value = Number(context.id)
  // The visible review notice opens matching on demand; loading metadata must
  // not replace the viewing page with an automatically expanded maintenance form.
  loadMedia(context)
  loadSimilar(context)
  try {
    const h = await api('/api/health')
    if (!currentLoad(context)) return
    noFfmpeg.value = !h.ffmpeg
  } catch (e) { /* 健康检查失败不挡详情页 */ }
  if (!currentLoad(context)) return
  await reloadFiles(context)
  if (!currentLoad(context)) return
  try {
    const result = await api('/api/movies/' + context.id + '/collection-hint')
    if (!currentLoad(context)) return
    hint.value = result
    if (!hint.value?.collection_tmdb_id) hint.value = null
  } catch (e) { if (currentLoad(context)) hint.value = null }
}
async function loadSimilar(context = loadContext()) {
  if (!currentLoad(context)) return
  similar.value = []
  try {
    const d = await api(`/api/movies/${context.id}/similar?limit=18`)
    // 路由已切走则丢弃过期回包（同组件切片）
    if (currentLoad(context)) similar.value = d.items || []
  } catch (e) { /* 推荐失败不挡详情页 */ }
}
async function reloadFiles(context = loadContext()) {
  if (!currentLoad(context)) return
  try {
    const files = await api('/api/movies/' + context.id + '/files')
    if (!currentLoad(context)) return
    sideFiles.value = files
  } catch (e) { if (currentLoad(context)) sideFiles.value = null }
  if (!currentLoad(context)) return
  try {
    const movie = await api('/api/movies/' + context.id)
    if (currentLoad(context)) m.value = movie
  } catch (e) { /* 忽略 */ }
}

const posterDlg = ref(false)
const posterBig = ref('')
const posterHi = ref(false)
let posterObjUrl = ''
// 换海报后原地覆盖文件（URL 不变）→ 用版本参数强制取新图；posterVer 用于同页即时刷新
const posterVer = ref(0)
const posterSrc = computed(() =>
  posterUrl(m.value?.poster_path, posterVer.value || m.value?.updated_at))
async function openPoster() {
  if (!m.value?.poster_path) return
  posterBig.value = posterSrc.value
  posterHi.value = false
  posterDlg.value = true
  try {
    const v = posterVer.value || m.value?.updated_at || ''
    const r = await fetch(`/api/movies/${route.params.id}/poster-orig?v=${encodeURIComponent(v)}`)
    if (!r.ok) return
    const b = await r.blob()
    if (posterObjUrl) URL.revokeObjectURL(posterObjUrl)
    posterObjUrl = URL.createObjectURL(b)
    if (posterDlg.value) {
      posterBig.value = posterObjUrl
      posterHi.value = true
    }
  } catch (e) { /* 保持本地图 */ }
}
// 换海报（TMDB 候选，后端代理缩略图；选定后持久化 override，刷新不回退）
const posterPick = ref(false)
const posterCands = ref([])
const posterLoading = ref(false)
const posterSaving = ref(false)
const posterErr = ref('')
async function openPosterPicker() {
  posterPick.value = true
  posterErr.value = ''
  if (posterCands.value.length) return
  posterLoading.value = true
  try {
    const d = await api(`/api/movies/${route.params.id}/posters`)
    posterCands.value = d.items || []
  } catch (e) {
    posterErr.value = '候选海报加载失败：' + e.message
  } finally {
    posterLoading.value = false
  }
}
async function choosePoster(c) {
  if (posterSaving.value) return
  posterSaving.value = true
  posterErr.value = ''
  try {
    await api(`/api/movies/${route.params.id}/poster`, {
      method: 'POST',
      body: JSON.stringify({ file_path: c.file_path })
    })
    posterPick.value = false
    posterCands.value = []
    posterVer.value++    // 版本号变化 → 头部/弹窗 URL 立刻指向新图
    await load()
    await openPoster()   // 重新拉取新原图
  } catch (e) {
    posterErr.value = '切换失败：' + e.message
  } finally {
    posterSaving.value = false
  }
}
function closePoster() {
  posterDlg.value = false
  posterPick.value = false
  posterCands.value = []
  posterErr.value = ''
  posterBig.value = ''
  posterHi.value = false
  if (posterObjUrl) {
    URL.revokeObjectURL(posterObjUrl)
    posterObjUrl = ''
  }
}
async function createFromSeries() {
  hintMsg.value = ''
  try {
    const d = await api('/api/collections/from-tmdb-series', {
      method: 'POST',
      body: JSON.stringify({ movie_id: Number(route.params.id) })
    })
    hintMsg.value = `已建「${d.name}」（${d.member_count} 部）`
    await load()
  } catch (e) {
    hintMsg.value = '创建失败：' + e.message
  }
}
function openEdit(mode) { editing.value = editing.value === mode ? '' : mode }
// 编辑面板回调（R05-Q4：子组件只发信号，重载/闪存/归档引导留在本页）
async function onFilesChanged() { await reloadFiles() }
const nrBusy = ref(false)
async function confirmMatch () {
  if (nrBusy.value) return
  nrBusy.value = true
  try {
    await api('/api/movies/' + route.params.id + '/confirm-match', { method: 'POST' })
    m.value = await api('/api/movies/' + route.params.id)
    flashSaved()
  } catch (e) {
    regionNote.value = '确认失败：' + e.message
  } finally {
    nrBusy.value = false
  }
}

async function onEditSaved() { editing.value = ''; flashSaved(); await load() }
async function onEditChanged() { flashSaved(); await load() }
async function refreshMovieInfo() {
  if (refreshBusy.value) return
  refreshBusy.value = true
  refreshMsg.value = '正在更新资料…'
  try {
    const r = await api('/api/movies/' + route.params.id + '/refresh', { method: 'POST' })
    refreshMsg.value = r.changed ? '影片资料已更新' : '远端资料无变化'
    if (r.forced) refreshMsg.value += '，已提交 NFO 与海报重写'
    await onRefreshed()
  } catch (e) { refreshMsg.value = '更新失败：' + e.message }
  finally { refreshBusy.value = false }
}
async function onRefreshed() { await load(); flashSaved(); await waitForMedia() }
async function onMatched({ oldRegion = '', background = {} } = {}) {
  await load()
  editing.value = ''
  checkArchiveHint()
  flashSaved()
  regionNote.value = (m.value?.region && m.value.region !== oldRegion)
    ? `产地变为${m.value.region}，文件仍在旧分区，请到设置页用“搬到顶层”修复。` : ''
  if (background.poster || background.avatars) await waitForMedia()
}
function flashSaved() {
  savedFlash.value = true
  if (flashTimer) clearTimeout(flashTimer)
  flashTimer = setTimeout(() => { savedFlash.value = false }, 3000)
}
const originalMoved = computed(() => {
  const o = (m.value?.original_file_path || '').trim()
  return !!o && o !== m.value?.file_path
})
function goRestore() {
  const query = { sec: 'sec-restore', ids: String(m.value.id) }
  if (m.value.library_id != null) query.library = String(m.value.library_id)
  router.push({ path: '/settings', query })
}
async function waitForMedia(tries = 10) {
  for (let i = 0; i < tries; i++) {
    await new Promise(r => setTimeout(r, 3000))
    try {
      const cur = await api('/api/movies/' + route.params.id)
      m.value = cur
      if (cur.poster_path) return
    } catch (e) { /* 忽略，继续轮询 */ }
  }
  await load()
}
async function checkArchiveHint() {
  archHint.value = null
  archMsg.value = ''
  try {
    const h = await api('/api/movies/' + route.params.id + '/organize-hint')
    if (h && h.needs) archHint.value = h
  } catch (e) { /* 忽略 */ }
}
async function doArchive() {
  const h = archHint.value
  if (!h || archApplying.value) return
  archApplying.value = true
  archMsg.value = ''
  try {
    const p = h.params || {}
    const body = p.mode === 'relocate'
      ? { mode: 'relocate', library_id: p.library_id,
          ids: [Number(route.params.id)], dry_run: false }
      : { mode: 'inplace', library_id: p.library_id,
          ids: [Number(route.params.id)], dry_run: false }
    const d = await api('/api/files/organize', { method: 'POST', body: JSON.stringify(body) })
    const rs = d.results || []
    const ok = rs.filter(r => r.status === 'moved').length
    archApplying.value = false
    archHint.value = null
    msg.value = `归档完成：${ok}/${rs.length} 项`
    await load()
  } catch (e) {
    archMsg.value = '归档失败：' + e.message
    archApplying.value = false
  }
}
function escPlayer(e) {
  // 预览弹窗（Esc 关闭）随 MovieFileManager；这里只管海报大图
  if (e.key !== 'Escape') return
  if (posterDlg.value) closePoster()
}
// 焦点陷阱需在 ref 全部声明后启用（watch immediate 会立即求值，避免 TDZ 崩整页）
useFocusTrap(computed(() => !!posterDlg.value), posterDlgRef)
useFocusTrap(computed(() => !!archHint.value), archDlgRef)

onMounted(() => {
  load()
  window.addEventListener('keydown', escPlayer)
})
onBeforeUnmount(() => { disposed = true; loadGeneration++ })
onUnmounted(() => {
  window.removeEventListener('keydown', escPlayer)
  // 上传中止/计时由 MovieUploadPanel 自身卸载时处理（评审 B8/R05-B5）
  if (flashTimer) clearTimeout(flashTimer)
  if (posterObjUrl) URL.revokeObjectURL(posterObjUrl)
})
watch(() => route.params.id, () => {
  if (!currentMovieId()) { loadGeneration++; return }
  contentTab.value = 'info'; editing.value = ''; collectionsOpen.value = false; repairOpen.value = false; refreshMsg.value = ''
  load()
})   // 同组件切片重载（评审 B8/R05-Q3）
</script>
<style scoped>
.top-right { display: flex; gap: 8px; align-items: center; }.saved-flash { color: var(--jz-success); font-size: 0.875rem; }.poster.zoomable { cursor: zoom-in; }.poster-big { max-height: 78vh; width: auto; max-width: 100%; margin: 0 auto; display: block; }.poster-dlg { text-align: center; }.poster-dlg .bar { justify-content: center; }.needs-review { color: var(--jz-danger); font-size: 0.875rem; border: 1px solid var(--jz-danger-border); border-radius: 999px; padding: 1px 4px 1px 10px; margin-left: 8px; vertical-align: middle; display: inline-flex; align-items: center; gap: 4px; }.needs-review .nr-btn { font-size: 0.75rem; padding: 1px 8px; border-radius: 999px; border: 1px solid var(--jz-danger-border); background: transparent; color: var(--jz-danger); cursor: pointer; }.needs-review .nr-btn.ok { border-color: var(--jz-success-border); color: var(--jz-success); }.needs-review .nr-btn:disabled { opacity: .6; cursor: wait; }.edition-chip { color: var(--jz-blue-chip); font-size: 0.875rem; border: 1px solid var(--jz-info-border); border-radius: 999px; padding: 1px 10px; margin-left: 8px; vertical-align: middle; }.edition-chip.spec { color: var(--jz-success); border-color: var(--jz-success-border); }.media-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0; }.media-badge { color: var(--jz-link); font-size: 0.875rem; border: 1px solid var(--jz-info-border); border-radius: 999px; padding: 1px 10px; }.media-warn { color: var(--jz-warn); font-size: 0.8125rem; border: 1px dashed var(--jz-warn-border); border-radius: 999px; padding: 1px 10px; }.media-loading { color: var(--jz-text-faint); font-size: 0.8125rem; }.resume-hint { color: var(--jz-text-dim); font-size: 0.8125rem; }.ver-sel { background: var(--jz-surface-3); color: var(--jz-text-dim); border: 1px solid var(--jz-border-strong); border-radius: 8px; padding: 6px 8px; max-width: 320px; }.pre-wrap { display: inline-flex; gap: 6px; align-items: center; }.pre-sel { background: var(--jz-surface-3); color: var(--jz-text-dim); border: 1px solid var(--jz-warn-border); border-radius: 8px; padding: 6px 8px; font-size: 0.8125rem; }.pre-btn { background: transparent; border: 1px dashed var(--jz-warn-border); color: var(--jz-warn); border-radius: 999px; padding: 6px 14px; cursor: pointer; font-size: 0.8125rem; }.pre-btn:disabled { opacity: 0.6; cursor: wait; }.src { color: var(--jz-text-faint); font-weight: normal; }.tag-row { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 4px; }.tag-chip { font-size: 0.8125rem; padding: 3px 12px; border-radius: 999px; border: 1px dashed var(--jz-border-strong); color: var(--jz-text-dim); }.col-chip { font-size: 0.8125rem; padding: 3px 12px; border-radius: 999px; border: 1px solid var(--jz-info-border); color: var(--jz-blue-chip); cursor: pointer; }.watched-chip { color: var(--jz-success); font-size: 0.875rem; border: 1px solid var(--jz-success-border); border-radius: 999px; padding: 1px 10px; margin-left: 8px; vertical-align: middle; }.hint-row { margin-top: 6px; color: var(--jz-text-dim); font-size: 0.875rem; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }.hint-row .fhint { color: var(--jz-text-faint); font-size: 0.75rem; }.crew { margin: 8px 0; font-size: 0.9375rem; }.role { color: var(--jz-text-faint); margin-right: 8px; font-size: 0.875rem; }.actor-chip { display: inline-block; padding: 5px 14px; margin: 2px 4px 2px 0; border-radius: 999px; background: var(--jz-surface-3); border: 1px solid var(--jz-border); cursor: pointer; font-size: 0.9375rem; }.actor-chip:hover { border-color: var(--jz-blue-chip); color: var(--jz-blue-chip); }.cast-wall { display: grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); gap: 12px; margin-top: 10px; }.cast-card { cursor: pointer; min-width: 0; }.cast-card img, .avatar-fallback { width: 100%; aspect-ratio: 3/4; object-fit: cover; border-radius: 8px; display: block; background: var(--jz-surface-3); }.avatar-fallback { display: flex; align-items: center; justify-content: center; font-size: 2rem; color: var(--jz-text-faint); border: 1px solid var(--jz-border); }.facts .fact { display: flex; gap: 10px; font-size: 0.875rem; margin: 8px 0; align-items: flex-start; }.facts .fact span:first-child { color: var(--jz-text-faint); min-width: 48px; flex-shrink: 0; }.facts .fact-val { min-width: 0; flex: 1; overflow-wrap: anywhere; word-break: break-word; line-height: 1.6; }.facts .fact-val button { flex-shrink: 0; margin-left: 6px; white-space: nowrap; }.facts a { color: var(--jz-blue-chip); margin-right: 10px; }.arch-dlg { max-width: 720px; }.arch-list { list-style: none; margin: 6px 0; padding: 0; display: flex; flex-direction: column; gap: 6px; max-height: 40vh; overflow: auto; }.arch-list li { display: flex; gap: 8px; align-items: center; background: var(--jz-surface-3); border: 1px solid var(--jz-border); border-radius: 8px; padding: 6px 10px; font-size: 0.8125rem; flex-wrap: wrap; }.arch-from { color: var(--jz-text-faint); overflow-wrap: anywhere; }.arch-arrow { color: var(--jz-blue-chip); }.arch-to { color: var(--jz-success); overflow-wrap: anywhere; }.dlg-mask { position: fixed; inset: 0; background: rgba(0,0,0,.66); display: flex; align-items: center; justify-content: center; z-index: 50; }.dlg { background: var(--jz-surface); border-radius: 10px; padding: 16px; min-width: 320px; max-width: 860px; width: calc(100vw - 48px); max-height: 88vh; overflow: auto; }.dlg h3 { margin: 0 0 8px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }button.danger { border-color: var(--jz-danger-border); color: var(--jz-danger); }.hint.warn { color: var(--jz-warn); }
/* 推荐行样式单源：SimilarRow.vue */
.poster-pick-head { display: flex; gap: 10px; align-items: baseline; margin-bottom: 8px; }
.poster-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 10px; max-height: 60vh; overflow: auto; padding: 2px; }
.poster-cand { position: relative; padding: 0; border: 2px solid transparent; border-radius: 8px; background: var(--jz-surface-3); cursor: pointer; overflow: hidden; }
.poster-cand.cur { border-color: var(--jz-success); }
.poster-cand img { width: 100%; aspect-ratio: 2/3; object-fit: cover; display: block; }
.poster-cand-meta { position: absolute; left: 0; right: 0; bottom: 0; font-size: 0.6875rem; color: var(--jz-text); background: rgba(0,0,0,.6); padding: 2px 4px; }
.poster-cand:disabled { opacity: 0.6; cursor: wait; }
</style>
