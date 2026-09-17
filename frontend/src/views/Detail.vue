<template>
  <div class="detail" v-if="m">
    <div class="hero">
      <div v-if="m.poster_path" class="hero-bg" :style="{ backgroundImage: `url(${posterUrl(m.poster_path)})` }"></div>
      <div class="hero-inner">
        <div class="topbar">
          <button @click="$router.back()">‹ 返回</button>
          <span class="top-right">
            <span v-if="savedFlash" class="saved-flash">已保存</span>
            <span v-if="regionNote" class="saved-flash" style="color:#e0a63c">{{ regionNote }}</span>
            <span v-if="buildVer" class="ver-tag" :title="'后端构建 ' + buildVer">构建 {{ buildVer }}</span>
            <button @click="toggleEdit">{{ editing ? '收起' : '编辑' }}</button>
          </span>
        </div>
        <div class="hero-main">
          <img v-if="m.poster_path" :src="posterUrl(m.poster_path)" class="poster zoomable" :alt="(m.title || '海报') + ' 海报'" title="查看大图" @click="openPoster" />
          <div v-else class="poster poster-empty"><Spinner :size="22" /><span>海报补齐中</span></div>
          <div class="hero-info">
            <h2>{{ m.title }} <span v-if="m.year" class="year">({{ m.year }})</span><span v-if="m.edition" class="edition-chip">{{ m.edition }}</span><span v-if="m.spec" class="edition-chip spec">{{ m.spec }}</span><span v-if="m.needs_review" class="needs-review">待确认</span><span v-if="m.watched" class="watched-chip">✓已看</span></h2>
            <div v-if="hasScore(m.tmdb_rating) || hasScore(m.douban_rating) || hasScore(m.custom_rating)" class="rating-row">
              <span v-if="hasScore(m.tmdb_rating)" class="rate-chip tmdb"><span class="stars">{{ starRow(m.tmdb_rating) }}</span> {{ fmtScore(m.tmdb_rating) }} <span class="src">TMDB</span></span>
              <span v-if="hasScore(m.douban_rating)" class="rate-chip douban">豆瓣 {{ fmtScore(m.douban_rating) }}</span>
              <span v-if="hasScore(m.custom_rating)" class="rate-chip custom">自评 {{ fmtScore(m.custom_rating) }}</span>
            </div>
            <p v-if="metaLine" class="meta-line">{{ metaLine }}</p>
            <div v-if="mediaBadge || mediaUnplayable || resumeText || noFfmpeg" class="media-row">
              <span v-if="mediaBadge" class="media-badge">{{ mediaBadge }}</span>
              <span v-if="mediaUnplayable" class="media-warn" :title="mediaError">无效文件，无法播放</span>
              <span v-if="noFfmpeg" class="media-warn" title="服务器缺 ffmpeg：转码/重封装不可用，直链与电视播放不受影响">转码不可用（缺 ffmpeg）</span>
              <span v-if="resumeText" class="resume-hint">{{ resumeText }}</span>
            </div>
            <div v-else-if="mediaLoading" class="media-row"><span class="media-loading">媒体信息探测中…</span></div>
            <div class="play-row">
              <button class="play-main" :disabled="heroBlocked" :title="heroBlockTip" @click="openHeroPlay">▶ 播放</button>
              <select v-if="(m.versions || []).length > 1" v-model.number="heroVid" class="ver-sel">
                <option v-for="v in m.versions" :key="v.id" :value="v.id" :disabled="!!verBlocked[v.id]">
                  {{ verLabel(v) }}{{ verBlocked[v.id] ? '（无效）' : (verFriendly(v.id) ? ' ★浏览器友好' : '') }}
                </option>
              </select>
              <span v-if="heroResume" class="resume-hint">{{ heroResume }}</span>
              <span v-if="!heroBlocked && !verFriendly(heroVid)" class="pre-wrap">
                <select v-model="preQuality" :disabled="!!preJob" class="pre-sel"
                  title="预转码目标：自动=按服务器能力（无硬件转码→720p，有硬件→1080p）">
                  <option value="auto">自动</option>
                  <option value="1080p">1080p</option>
                  <option value="720p">720p</option>
                  <option value="source">原画</option>
                </select>
                <button class="pre-btn" :disabled="!!preJob"
                  title="夜间/闲时把本片转好存着，完工后点播即静态秒播"
                  @click="startPrewarm">{{ preJob ? '预转码中…' : '开始预转码' }}</button>
              </span>
              <span v-if="preMsg" class="resume-hint">{{ preMsg }}</span>
            </div>
            <div v-if="(m.tags || []).length" class="tag-row">
              <span v-for="t in m.tags" :key="t" class="tag-chip">{{ t }}</span>
            </div>
            <div v-if="(m.collections || []).length" class="tag-row">
              <span v-for="c in m.collections" :key="c.id" class="col-chip" @click="$router.push('/c/' + c.id)">📁 {{ c.name }}</span>
            </div>
            <div v-if="hint && hint.collection_tmdb_id" class="hint-row">
              TMDB 系列：{{ hint.collection_name }}（库内 {{ hint.in_library_count }} 部）
              <button v-if="!hint.already_collected" @click="createFromSeries">一键建合集</button>
              <span v-else class="fhint">已收录</span>
              <span>{{ hintMsg }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="sections">
      <div class="body-grid">
        <div class="main-col">
          <section class="card-block">
            <h3>剧情简介</h3>
            <p v-if="m.overview_display" class="overview">{{ m.overview_display }}</p>
            <p v-else class="empty">暂无简介</p>
          </section>

          <section v-if="directors.length || actors.length" class="card-block">
            <h3>演职员</h3>
            <p v-if="directors.length" class="crew"><span class="role">导演</span>
              <span v-for="(p, i) in directors" :key="'d' + p.tmdb_id"><span class="actor-chip" @click="goPerson(p)">{{ p.name }}</span><span v-if="i < directors.length - 1"> </span></span>
            </p>
            <div v-if="actors.length" class="cast-wall">
              <div v-for="p in actors" :key="p.tmdb_id" class="cast-card" @click="goPerson(p)">
                <img v-if="p.avatar && p.avatar !== '-'" :src="posterUrl(p.avatar)" loading="lazy" :alt="p.name || '演员'" />
                <div v-else class="avatar-fallback">{{ (p.name || '?').slice(0, 1) }}</div>
                <div class="cast-name">{{ p.name }}</div>
                <div v-if="showCharacter && p.character_name" class="cast-char">{{ p.character_name }}</div>
              </div>
            </div>
          </section>

          <MovieFileManager :movie-id="Number(route.params.id)" :movie="m" :side-files="sideFiles"
            :ver-blocked="verBlocked" :ver-err="verErr" :ver-method="verMethod" :ver-friendly="verFriendly"
            @play="openStream" @changed="onFilesChanged" />
          <MovieUploadPanel :movie-id="Number(route.params.id)" @uploaded="load" />
        </div>

        <aside class="side-col">
          <section class="card-block facts">
            <h3>影片信息</h3>
            <div v-if="m.original_title" class="fact"><span>原标题</span><span>{{ m.original_title }}</span></div>
            <div v-if="(m.genres || []).length" class="fact"><span>类型</span><span>{{ (m.genres || []).join(' / ') }}</span></div>
            <div v-if="m.region || originName" class="fact"><span>产地</span><span>{{ [m.region, originName].filter(Boolean).join(' · ') }}</span></div>
            <div v-if="m.year" class="fact"><span>年份</span><span>{{ m.year }}</span></div>
            <div v-if="originalMoved" class="fact"><span>原始文件</span><span class="fact-val" :title="m.original_file_path">{{ m.original_file_path }} <button @click="goRestore">去恢复</button></span></div>
            <div v-if="m.tmdb_id" class="fact"><span>链接</span><span><a :href="`https://www.themoviedb.org/movie/${m.tmdb_id}`" target="_blank" rel="noopener">TMDB</a><a v-if="m.imdb_id" :href="`https://www.imdb.com/title/${m.imdb_id}/`" target="_blank" rel="noopener">IMDb</a></span></div>
          </section>
        </aside>
      </div>

      <section v-if="similar.length" class="card-block similar-block">
        <h3>库中类似 <span class="similar-sub">按系列 / 影人 / 类型 / 标签推荐</span></h3>
        <div class="similar-wrap">
          <button v-if="similar.length > 4" class="similar-nav left" aria-label="向左滚动" @click="scrollSimilar(-1)">‹</button>
          <div ref="simRowRef" class="similar-row" @scroll="onSimScroll">
            <div v-for="x in similar" :key="x.id" class="similar-card" @click="$router.push('/m/' + x.id)">
              <div class="poster-wrap">
                <img v-if="x.poster_path" :src="posterUrl(x.poster_path)" loading="lazy" :alt="(x.title || '影片') + ' 海报'" />
                <div v-else class="similar-no-poster" aria-hidden="true">{{ (x.title || '?').slice(0, 1) }}</div>
                <ScoreBadge :score="x.tmdb_rating" source="tmdb" />
              </div>
              <div class="similar-name" :title="x.title">{{ x.title }}<span v-if="x.year" class="similar-year">({{ x.year }})</span><span v-if="x.version_count > 1" class="similar-year">×{{ x.version_count }}</span><span v-if="hasScore(x.custom_rating)" class="similar-custom">♥{{ fmtScore(x.custom_rating) }}</span></div>
              <div v-if="x.reason" class="similar-reason" :title="x.reason">{{ x.reason }}</div>
            </div>
          </div>
          <div v-if="simBar.show" class="similar-bar" aria-hidden="true">
            <div class="similar-bar-thumb" :style="{ left: simBar.left + '%', width: simBar.width + '%' }"></div>
          </div>
          <button v-if="similar.length > 4" class="similar-nav right" aria-label="向右滚动" @click="scrollSimilar(1)">›</button>
        </div>
      </section>

      <MovieEditPanel v-if="editing" :movie="m" :movie-id="Number(route.params.id)"
        @close="editing = false" @saved="onEditSaved" @changed="onEditChanged"
        @matched="onMatched" @refreshed="onRefreshed" />
    </div>

    <PlayerModal v-if="playVid" ref="playerRef" :versionId="playVid" :title="playTitle"
      @close="closeStream" @watched="onPlayEnded" />

    <div v-if="posterDlg" class="dlg-mask" @click.self="closePoster">
      <div ref="posterDlgRef" class="dlg pv-dlg poster-dlg" role="dialog" aria-modal="true">
        <img :src="posterBig" class="pv-img poster-big" />
        <div class="bar"><span class="hint">{{ posterHi ? '高清原图' : '标清预览（原图加载中或不可用）' }}</span><a :href="posterBig" :download="baseName(posterBig)">下载</a><button @click="closePoster">关闭</button></div>
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
        <button @click="archHint = null" :disabled="archApplying">稍后（可在设置页「入库流程 → ③ 归档整理」处理）</button>
        <span>{{ archMsg }}</span>
      </div>
    </div>
  </div>
</template>
<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, posterUrl } from '../api.js'
import { usePolling } from '../usePolling.js'
import { getCaps } from '../caps.js'
import { hasScore, fmtScore, starRow } from '../ratings.js'
import Spinner from '../components/Spinner.vue'
import PlayerModal from '../components/PlayerModal.vue'
import ScoreBadge from '../components/ScoreBadge.vue'
import MovieUploadPanel from '../components/MovieUploadPanel.vue'
import MovieFileManager from '../components/MovieFileManager.vue'
import MovieEditPanel from '../components/MovieEditPanel.vue'
import { useFocusTrap } from '../useFocusTrap.js'

const route = useRoute()
const router = useRouter()
const m = ref(null)
const sideFiles = ref(null)
const msg = ref('')
const hint = ref(null)
const hintMsg = ref('')
const posterDlgRef = ref(null)
const archDlgRef = ref(null)
const regionNote = ref('')
// 库中类似（Plex 式推荐）：后端纯本地相似度，失败静默不挡详情页
const similar = ref([])
const simRowRef = ref(null)
// 扁平细线滚动指示（隐藏原生滚动条，thumb 反映滚动位置）
const simBar = ref({ show: false, left: 0, width: 100 })
const archHint = ref(null)
const archApplying = ref(false)
const archMsg = ref('')
const editing = ref(false)
const savedFlash = ref(false)
let flashTimer = null
const actors = computed(() => (m.value?.persons || []).filter(p => p.role === 'actor'))
// TMDB character 是贡献者自由文本、不随语言翻译：非英语片里是英文描述/罗马音
//（如"Piggy"/"Deyunan (voice)"），只有原语言为英语时才可信展示
const showCharacter = computed(() => String(m.value?.original_language || '').toLowerCase().startsWith('en'))
const directors = computed(() => (m.value?.persons || []).filter(p => p.role === 'director'))
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
const buildVer = ref('')
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
  const remain = dur - pos
  if (dur > 0 && remain > 0 && (remain / dur < 0.05 || remain < 300)) return ''
  return `上次看到 ${p.position_text || ''}`
})
async function loadMedia() {
  mediaLoading.value = true
  mediaError.value = ''
  // P1：一次取齐全版本（媒体+四档决策+最优版），带客户端实测 caps（打分随能力变化）
  try {
    const caps = await getCaps()
    const agg = await api('/api/stream/versions', {
      method: 'POST',
      body: JSON.stringify({ movie_id: Number(route.params.id), quality: 'auto', caps }),
    })
    verList.value = agg.versions || []
    bestVid.value = agg.best_version_id || null
    const cur = verList.value.find(x => Number(x.version_id) === Number(route.params.id))
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
    heroVid.value = bestVid.value || Number(route.params.id)
  } catch (e) {
    mediaInfo.value = null
    mediaError.value = String(e.message || e)
    heroVid.value = Number(route.params.id)
  } finally {
    mediaLoading.value = false
  }
  try {
    const p = await api(`/api/stream/progress?version_id=${heroVid.value}`)
    progressInfo.value = (p && Number(p.position) > 0) ? p : null
  } catch (e) { progressInfo.value = null }
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
  regionNote.value = ''
  m.value = await api('/api/movies/' + route.params.id)
  heroVid.value = Number(route.params.id)
  // 未匹配（刮削失败/无结果）自动展开编辑面板，直接可搜 TMDB 重新匹配（评审 B9 后续）
  if (!m.value.tmdb_id) editing.value = true
  loadMedia()
  loadSimilar()
  try {
    const h = await api('/api/health')
    noFfmpeg.value = !h.ffmpeg
    buildVer.value = h.build || ''
  } catch (e) { /* 健康检查失败不挡详情页 */ }
  await reloadFiles()
  try {
    hint.value = await api('/api/movies/' + route.params.id + '/collection-hint')
    if (!hint.value?.collection_tmdb_id) hint.value = null
  } catch (e) { hint.value = null }
}
async function loadSimilar() {
  const mid = route.params.id
  similar.value = []
  try {
    const d = await api(`/api/movies/${mid}/similar?limit=18`)
    // 路由已切走则丢弃过期回包（同组件切片）
    if (String(route.params.id) === String(mid)) similar.value = d.items || []
  } catch (e) { /* 推荐失败不挡详情页 */ }
  await nextTick()
  updateSimBar()
}
function updateSimBar() {
  const el = simRowRef.value
  if (!el || el.scrollWidth <= el.clientWidth + 1) {
    simBar.value = { show: false, left: 0, width: 100 }
    return
  }
  const view = el.clientWidth
  const total = el.scrollWidth
  const width = Math.max(8, (view / total) * 100)
  const maxScroll = total - view
  const left = maxScroll > 0 ? (el.scrollLeft / maxScroll) * (100 - width) : 0
  simBar.value = { show: true, left, width }
}
function onSimScroll() {
  updateSimBar()
}
function scrollSimilar(dir) {
  const el = simRowRef.value
  if (!el) return
  el.scrollBy({ left: dir * Math.max(240, el.clientWidth * 0.8), behavior: 'smooth' })
}
async function reloadFiles() {
  try {
    sideFiles.value = await api('/api/movies/' + route.params.id + '/files')
  } catch (e) { sideFiles.value = null }
  try {
    m.value = await api('/api/movies/' + route.params.id)
  } catch (e) { /* 忽略 */ }
}

const posterDlg = ref(false)
const posterBig = ref('')
const posterHi = ref(false)
let posterObjUrl = ''
async function openPoster() {
  if (!m.value?.poster_path) return
  posterBig.value = posterUrl(m.value.poster_path)
  posterHi.value = false
  posterDlg.value = true
  try {
    const r = await fetch(`/api/movies/${route.params.id}/poster-orig`)
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
function closePoster() {
  posterDlg.value = false
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
function toggleEdit() {
  editing.value = !editing.value
}
// 编辑面板回调（R05-Q4：子组件只发信号，重载/闪存/归档引导留在本页）
async function onFilesChanged() { await reloadFiles() }
async function onEditSaved() { editing.value = false; flashSaved(); await load() }
async function onEditChanged() { flashSaved(); await load() }
async function onRefreshed() { await load(); flashSaved(); await waitForMedia() }
async function onMatched({ oldRegion = '', background = {} } = {}) {
  await load()
  editing.value = false
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
function goPerson(p) {
  if (p && p.tmdb_id) router.push('/p/' + p.tmdb_id)
}
const originalMoved = computed(() => {
  const o = (m.value?.original_file_path || '').trim()
  return !!o && o !== m.value?.file_path
})
function goRestore() {
  router.push({ path: '/settings', query: { sec: 'sec-restore', ids: String(m.value.id) } })
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
      ? { mode: 'relocate', from_prefix: p.from_prefix, to_dir: p.to_dir,
          group_by_region: true, ids: [Number(route.params.id)], dry_run: false }
      : { mode: 'inplace', ids: [Number(route.params.id)], dry_run: false }
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
  window.addEventListener('resize', updateSimBar)
})
onUnmounted(() => {
  window.removeEventListener('keydown', escPlayer)
  window.removeEventListener('resize', updateSimBar)
  // 上传中止/计时由 MovieUploadPanel 自身卸载时处理（评审 B8/R05-B5）
  if (flashTimer) clearTimeout(flashTimer)
  if (posterObjUrl) URL.revokeObjectURL(posterObjUrl)
})
watch(() => route.params.id, () => { load() })   // 同组件切片重载（评审 B8/R05-Q3）
</script>
<style scoped>
.detail { padding-bottom: 24px; }.hero { position: relative; overflow: hidden; }.hero-bg {
  position: absolute; inset: 0;
  background-size: cover; background-position: center 20%;
  filter: blur(28px) brightness(.45) saturate(1.2);
  transform: scale(1.15);
  -webkit-mask-image: linear-gradient(#000 30%, transparent);
  mask-image: linear-gradient(#000 30%, transparent);
}.hero-inner { position: relative; width: 100%; box-sizing: border-box; padding: 12px 24px; max-width: min(1600px, 100%); margin: 0 auto; }.topbar { display: flex; justify-content: space-between; align-items: center; }.top-right { display: flex; gap: 8px; align-items: center; }.saved-flash { color: #7ed321; font-size: 0.875rem; }.ver-tag { color: #555; font-size: 0.75rem; }.hero-main { display: flex; gap: 20px; margin-top: 12px; align-items: flex-start; }.poster { width: 220px; border-radius: 8px; box-shadow: 0 8px 28px rgba(0,0,0,.55); }.poster.zoomable { cursor: zoom-in; }.poster-big { max-height: 78vh; width: auto; max-width: 100%; margin: 0 auto; display: block; }.poster-dlg { text-align: center; }.poster-dlg .bar { justify-content: center; }.poster-empty { aspect-ratio: 2/3; display: flex; flex-direction: column; gap: 8px; align-items: center; justify-content: center; background: #262626; color: #888; font-size: 0.875rem; box-shadow: none; }.hero-info { min-width: 0; }.hero-info h2 { margin: 0 0 8px; font-size: 1.875rem; }.hero-info .year { color: #aaa; font-weight: normal; font-size: 1.3125rem; }.needs-review { color: #ff6b6b; font-size: 0.875rem; border: 1px solid #6e2b2b; border-radius: 999px; padding: 1px 10px; margin-left: 8px; vertical-align: middle; }.edition-chip { color: #6ab0ff; font-size: 0.875rem; border: 1px solid #2b4a6e; border-radius: 999px; padding: 1px 10px; margin-left: 8px; vertical-align: middle; }.edition-chip.spec { color: #7ed321; border-color: #3a5a1e; }.meta-line { color: #aaa; font-size: 1rem; margin: 8px 0; }.media-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0; }.media-badge { color: #9ecfff; font-size: 0.875rem; border: 1px solid #2b4a6e; border-radius: 999px; padding: 1px 10px; }.media-warn { color: #e0a63c; font-size: 0.8125rem; border: 1px dashed #6e5426; border-radius: 999px; padding: 1px 10px; }.media-loading { color: #666; font-size: 0.8125rem; }.resume-hint { color: #7ed321; font-size: 0.8125rem; }.play-row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 12px 0 2px; }.play-main { font-size: 1rem; padding: 8px 28px; border-radius: 999px; background: #2b6cb0; border: 1px solid #2b6cb0; color: #fff; cursor: pointer; }.play-main:hover:not(:disabled) { background: #3580cc; }.play-main:disabled { background: #333; border-color: #444; color: #777; cursor: not-allowed; }.ver-sel { background: #262626; color: #ccc; border: 1px solid #444; border-radius: 8px; padding: 6px 8px; max-width: 320px; }.pre-wrap { display: inline-flex; gap: 6px; align-items: center; }.pre-sel { background: #262626; color: #ccc; border: 1px solid #6e5426; border-radius: 8px; padding: 6px 8px; font-size: 0.8125rem; }.pre-btn { background: transparent; border: 1px dashed #6e5426; color: #e0a63c; border-radius: 999px; padding: 6px 14px; cursor: pointer; font-size: 0.8125rem; }.pre-btn:disabled { opacity: 0.6; cursor: wait; }.src { color: #888; font-weight: normal; }.tag-row { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 4px; }.tag-chip { font-size: 0.8125rem; padding: 3px 12px; border-radius: 999px; border: 1px dashed #555; color: #ccc; }.col-chip { font-size: 0.8125rem; padding: 3px 12px; border-radius: 999px; border: 1px solid #2b4a6e; color: #6ab0ff; cursor: pointer; }.watched-chip { color: #7ed321; font-size: 0.875rem; border: 1px solid #3a5a1e; border-radius: 999px; padding: 1px 10px; margin-left: 8px; vertical-align: middle; }.hint-row { margin-top: 6px; color: #aaa; font-size: 0.875rem; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }.hint-row .fhint { color: #777; font-size: 0.75rem; }.sections { width: 100%; box-sizing: border-box; padding: 0 24px; max-width: min(1600px, 100%); display: flex; flex-direction: column; gap: 12px; margin: 12px auto 0; }.body-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(320px, 400px); gap: 12px; align-items: start; }.main-col { display: flex; flex-direction: column; gap: 12px; min-width: 0; }.side-col { min-width: 0; }@media (max-width: 860px) { .body-grid { grid-template-columns: 1fr; }}.card-block { background: #1c1c1c; border-radius: 10px; padding: 14px 16px; }.card-block h3 { margin: 0 0 10px; font-size: 1.0625rem; color: #ddd; }.overview { margin: 0; line-height: 1.8; color: #e6e6e6; font-size: 1rem; }.empty { margin: 0; color: #777; font-size: 0.9375rem; }.crew { margin: 8px 0; font-size: 0.9375rem; }.role { color: #888; margin-right: 8px; font-size: 0.875rem; }.actor-chip { display: inline-block; padding: 5px 14px; margin: 2px 4px 2px 0; border-radius: 999px; background: #262626; border: 1px solid #3a3a3a; cursor: pointer; font-size: 0.9375rem; }.actor-chip:hover { border-color: #6ab0ff; color: #6ab0ff; }.cast-wall { display: grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); gap: 12px; margin-top: 10px; }.cast-card { cursor: pointer; min-width: 0; }.cast-card img, .avatar-fallback { width: 100%; aspect-ratio: 3/4; object-fit: cover; border-radius: 8px; display: block; background: #262626; }.avatar-fallback { display: flex; align-items: center; justify-content: center; font-size: 2rem; color: #666; border: 1px solid #3a3a3a; }.cast-name { font-size: 0.875rem; margin-top: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }.cast-char { font-size: 0.75rem; color: #888; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }.facts .fact { display: flex; gap: 10px; font-size: 0.875rem; margin: 8px 0; align-items: flex-start; }.facts .fact span:first-child { color: #888; min-width: 48px; flex-shrink: 0; }.facts .fact-val { min-width: 0; flex: 1; overflow-wrap: anywhere; word-break: break-word; line-height: 1.6; }.facts .fact-val button { flex-shrink: 0; margin-left: 6px; white-space: nowrap; }.facts a { color: #6ab0ff; margin-right: 10px; }.arch-dlg { max-width: 720px; }.arch-list { list-style: none; margin: 6px 0; padding: 0; display: flex; flex-direction: column; gap: 6px; max-height: 40vh; overflow: auto; }.arch-list li { display: flex; gap: 8px; align-items: center; background: #262626; border: 1px solid #3a3a3a; border-radius: 8px; padding: 6px 10px; font-size: 0.8125rem; flex-wrap: wrap; }.arch-from { color: #888; overflow-wrap: anywhere; }.arch-arrow { color: #6ab0ff; }.arch-to { color: #7ed321; overflow-wrap: anywhere; }.dlg-mask { position: fixed; inset: 0; background: rgba(0,0,0,.66); display: flex; align-items: center; justify-content: center; z-index: 50; }.dlg { background: #1c1c1c; border-radius: 10px; padding: 16px; min-width: 320px; max-width: 860px; width: calc(100vw - 48px); max-height: 88vh; overflow: auto; }.dlg h3 { margin: 0 0 8px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }button.danger { border-color: #6e2b2b; color: #ff8a8a; }.hint.warn { color: #e0a63c; }
.similar-block { position: relative; }
.similar-sub { color: #777; font-size: 0.75rem; font-weight: normal; margin-left: 6px; }
.similar-wrap { position: relative; }
.similar-row { display: flex; gap: 12px; overflow-x: auto; padding: 2px 2px 10px; scroll-behavior: smooth; scrollbar-width: none; }
.similar-row::-webkit-scrollbar { display: none; }
.similar-bar { position: relative; height: 3px; margin: 0 2px; }
.similar-bar-thumb { position: absolute; top: 0; height: 100%; border-radius: 999px; background: rgba(255,255,255,.18); transition: background .15s; }
.similar-wrap:hover .similar-bar-thumb { background: rgba(255,255,255,.32); }
.similar-card { flex: 0 0 140px; width: 140px; cursor: pointer; min-width: 0; }
.similar-card .poster-wrap img { border-radius: 8px; transition: filter .15s; }
.similar-card:hover .poster-wrap img { filter: brightness(1.1); }
.similar-no-poster { width: 100%; aspect-ratio: 2/3; display: flex; align-items: center; justify-content: center; background: #242424; color: #555; font-size: 2rem; font-weight: bold; border-radius: 8px; user-select: none; }
.similar-name { font-size: 0.8125rem; margin-top: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.similar-year { color: #999; font-size: 0.75rem; margin-left: 4px; }
.similar-custom { color: #ff6b6b; font-size: 0.75rem; margin-left: 4px; }
.similar-reason { font-size: 0.75rem; color: #888; margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.similar-nav { position: absolute; top: 42%; transform: translateY(-50%); z-index: 2; width: 32px; height: 44px; border: none; border-radius: 8px; background: rgba(0,0,0,.62); color: #eee; font-size: 1.5rem; line-height: 1; cursor: pointer; opacity: 0; transition: opacity .15s; padding: 0; }
.similar-nav.left { left: 4px; }
.similar-nav.right { right: 4px; }
.similar-block:hover .similar-nav { opacity: 1; }
.similar-nav:hover { background: rgba(0,0,0,.85); }
@media (hover: none) { .similar-nav { display: none; } }
</style>
