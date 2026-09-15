<template>
  <div class="dlg-mask" @click.self="$emit('close')">
    <div class="player-dlg">
      <div class="pd-head">
        <h3>{{ title || ('版本 ' + versionId) }}</h3>
        <span v-if="methodLine" class="play-method">{{ methodLine }}</span>
        <span v-if="reasonLine" class="play-reason">{{ reasonLine }}</span>
        <span v-if="sessStatus" class="sess-status">{{ sessStatus }}</span>
        <span class="pd-spacer"></span>
        <button class="pd-mini" @click="copyDebug" title="复制调试信息（贴给开发者定位）">调试</button>
        <button class="pd-mini" @click="$emit('close')">关闭</button>
      </div>
      <div v-if="resumeOffer" class="resume-bar">
        <span>上次看到 {{ resumeOffer }}</span>
        <button @click="resumePlay">继续播放</button>
        <button @click="restartPlay">从头开始</button>
      </div>
      <!-- 播放容器：全屏目标；内含视频/冻结帧/提示/顶部标题/底部控件 -->
      <div ref="pvWrapEl" class="pv-wrap"
        :class="{ 'has-bar': isHls, 'hide-cursor': isFull && !overlayVisible }"
        :style="videoPadding ? { paddingTop: videoPadding } : {}"
        @mousemove="onMouseMove" @dblclick="toggleFull">
        <video ref="videoEl" :key="videoKey" :controls="!isHls" autoplay playsinline preload="metadata" class="player-video"
          @error="onVideoError"></video>
        <img v-if="freezeFrame" :src="freezeFrame" class="freeze-frame" alt="" />
        <div v-if="seekPending" class="seek-ov">
          <Spinner :size="18" />
          <span>正在转码到 {{ fmt(seekPos) }}…</span>
        </div>
        <p v-if="err" class="pv-err">{{ err }}</p>
        <div class="pv-top" :class="{ show: overlayVisible }">
          <span class="pv-title">{{ title || ('版本 ' + versionId) }}</span>
          <span v-if="sessStatus" class="pv-status">{{ sessStatus }}</span>
          <span class="pd-spacer"></span>
          <button class="pd-mini" @click="toggleFull" title="退出全屏（Esc）">⤡ 退出全屏</button>
        </div>
        <div v-if="isHls" class="pv-ctl" :class="{ show: overlayVisible }" @dblclick.stop>
          <button @click="togglePlay" :title="isPlaying ? '暂停（空格）' : '播放（空格）'">{{ isPlaying ? '⏸' : '▶' }}</button>
          <span class="ctl-time">{{ fmt(seekDragging ? seekPreview : seekPos) }} / {{ fmt(decidedDuration) }}</span>
          <input type="range" min="0" :max="Math.floor(decidedDuration)" step="1"
            :value="seekDragging ? seekPreview : Math.floor(seekPos)"
            :disabled="seekPending || !(decidedDuration > 0)"
            @input="onSeekInput" @change="onSeekCommit" class="ctl-seek" />
          <button @click="toggleMute" :title="muted ? '取消静音' : '静音'">{{ muted ? '🔇' : '🔊' }}</button>
          <input type="range" min="0" max="100" :value="muted ? 0 : volume * 100"
            @input="setVolume" class="ctl-vol" />
          <select v-model="quality" @change="reload" title="画质">
            <option value="original">原画</option>
            <option value="1080p">1080p</option>
            <option value="720p">720p</option>
          </select>
          <select v-if="audios.length > 1" v-model.number="audioIdx" @change="reload" title="音轨">
            <option v-for="(a, i) in audios" :key="i" :value="i">{{ audioLabel(a, i) }}</option>
          </select>
          <select v-if="subs.length" v-model.number="subIdx" @change="onSubChange" title="字幕">
            <option :value="-1">无字幕</option>
            <option v-for="(s, i) in subs" :key="i" :value="i">
              {{ subLabel(s, i) }}{{ s.image ? '（烧录）' : '' }}
            </option>
          </select>
          <button @click="toggleFull" :title="isFull ? '退出全屏（Esc）' : '全屏（双击画面）'">{{ isFull ? '⤡' : '⛶' }}</button>
        </div>
      </div>
      <div v-if="needGesture" class="gesture-bar">
        <span>片源已就绪，浏览器阻止了自动带声播放</span>
        <button class="play-now" @click="userPlay">▶ 点击播放</button>
      </div>
      <p class="hint-line">{{ bufLine || posHint }}</p>
    </div>
  </div>
</template>
<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { api } from '../api.js'
import Spinner from './Spinner.vue'
// hls.js 懒加载（~600KB）：只在进入播放器且非 Safari 时才下载，不拖首屏
let HlsCls = null
async function ensureHls() {
  if (!HlsCls) HlsCls = (await import('hls.js')).default
  return HlsCls
}

const props = defineProps({ versionId: { type: Number, required: true }, title: { type: String, default: '' } })
const emit = defineEmits(['close', 'watched'])

const videoEl = ref(null)
let hls = null
let saveTimer = 0
let lastSave = 0
const quality = ref('original')
const audioIdx = ref(0)
const subIdx = ref(-1)
const audios = ref([])
const subs = ref([])
const method = ref('')
const reasons = ref([])
const autoDropped = ref(false)
const sessStatus = ref('')
let sessionId = null
let pingTimer = 0
const err = ref('')
const posHint = ref('')
const resumeOffer = ref('')
let resumePos = 0
const decidedDuration = ref(0)
let doneWatched = false
// HLS 绝对时间轴：会话按 start 开新流，片内 currentTime 从 0 起；显示/存档一律用 offset+片内
const startOffset = ref(0)
const seekPos = ref(0)
const bufSecs = ref(0)
let bufTimer = 0
// 卡死看门狗：HLS 播放中 currentTime 长期不动且无缓冲 → 同会话重挂 playlist 自救
let lastTickPos = -1
let stallTicks = 0
let seekStuckTicks = 0
let recoverCount = 0
let lastRecoverAt = 0
let lastPlaylistUrl = ''
const lastHlsError = ref('')
// 实际使用的播放引擎：hls(Plex式) | native(Safari原生) | direct(原文件)
let engine = 'none'
let mediaErrLogged = ''
// 元素级恢复 + 冻结遥测：看门狗不再看缓冲量，只看“该走的时间走没走”
const videoKey = ref(0)
let lastAdvanceAt = 0
let evtLog = []
const bootAt = Date.now()
function logEvt(kind, detail) {
  try {
    evtLog.push({ t: Math.round((Date.now() - bootAt) / 100) / 10, k: kind, d: String(detail ?? '').slice(0, 160) })
    if (evtLog.length > 40) evtLog = evtLog.slice(-40)
  } catch (e) { /* 忽略 */ }
}
// HLS 自绘控制条状态
const isPlaying = ref(false)
const muted = ref(false)
const volume = ref(1)
const isFull = ref(false)
const seekPending = ref(false)
let seekPendingSince = 0
// 当前会话是否已把图片字幕烧录进画面（切字幕/画质时据此决定是否重开）
let burnOn = false
// 拖动态本地化：input 期间只改 seekPreview，change(松手) 才提交 → 不被 timeupdate 抬杠
const seekDragging = ref(false)
const seekPreview = ref(0)
// 冻结帧 + 容器比例（padding-top 撑高，绝对定位铺满：换会话时窗口绝不塌）
const freezeFrame = ref('')
const videoPadding = ref('56.25%')
// 全屏目标 = 播放容器（不是整个弹窗）：画面居中、控件悬浮可隐
const pvWrapEl = ref(null)
const mouseActive = ref(false)
let hideTimer = 0
function onMouseMove() {
  if (!isFull.value) return
  mouseActive.value = true
  if (hideTimer) clearTimeout(hideTimer)
  hideTimer = setTimeout(() => { mouseActive.value = false }, 3000)
}
// 全屏时控件/标题的显隐：鼠标活跃、暂停、seek 中、有错误时常显
const overlayVisible = computed(() =>
  !isFull.value || mouseActive.value || seekPending.value || !isPlaying.value || !!err.value)
const isHls = computed(() => method.value === 'remux' || method.value === 'transcode')
const bufLine = computed(() => {
  if (!isHls.value) return ''
  const total = Number(decidedDuration.value) || 0
  let s = `已播 ${fmt(seekPos.value)}`
  if (total > 0) s += ` / 全片 ${fmt(total)}`
  if (bufSecs.value > 0) s += `（已缓冲 ${Math.floor(bufSecs.value)}s）`
  return s
})
function absPos() {
  const v = videoEl.value
  const cur = (v && Number.isFinite(v.currentTime)) ? v.currentTime : 0
  // Direct：媒体时间轴就是整片时间；HLS：会话时间轴从 0 起，需加会话起点
  if (method.value === 'direct') return Math.max(0, cur)
  return Math.max(0, startOffset.value + cur)
}

const methodLine = computed(() => {
  if (!method.value) return ''
  return { direct: 'Direct Play（原文件直发）', remux: 'Direct Stream（仅换容器，零画质损失）', transcode: '转码中（按所选画质重编）' }[method.value] || method.value
})
const REASON_TEXT = {
  dovi_not_supported: '含杜比视界（浏览器无 DV 解码，已降为 SDR；原盘 DV 请用电视/Kodi 看）',
  video_codec_not_supported: '视频编码浏览器不支持，已重编为 H264',
  audio_codec_not_supported: '音频编码浏览器不支持，已转为 AAC',
  container_not_supported: '容器不对，已无损换为浏览器兼容容器',
  resolution_downscale: '已按所选画质降档（省 CPU）',
  pgs_needs_burn: '图片字幕（PGS/VobSub）已烧录进画面（较耗 CPU，切换字幕或原画需重转码）',
  auto_downscale_720p: '已自动降为 720p（4K 片源软转太重，原画可在上方切回）',
}
const reasonLine = computed(() => (reasons.value || []).map(r => REASON_TEXT[r] || r).join('；'))
function audioLabel(a, i) {
  const parts = [`音轨${i + 1}`]
  if (a.codec) parts.push(String(a.codec).toUpperCase())
  if (a.channels) parts.push(a.channels + 'ch')
  if (a.lang) parts.push(a.lang)
  if (a.title) parts.push(a.title)
  return parts.join(' ')
}
function subLabel(s, i) {
  const parts = [`字幕${i + 1}`]
  if (s.lang) parts.push(s.lang)
  if (s.title) parts.push(s.title)
  if (s.codec && !s.image) parts.push(String(s.codec).toUpperCase())
  return parts.join(' ')
}
function fmt(sec) {
  sec = Math.max(0, Math.floor(Number(sec) || 0))
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`
}
function destroyHls() {
  if (hls) { try { hls.destroy() } catch (e) { /* 忽略 */ } hls = null }
}
// hls 挂载唯一入口（起播/自救共用）：接事件遥测，MEDIA_ATTACHED 后回到目标片内时间
async function mountHls(v, url, targetMediaTime) {
  let Hls = null
  try { Hls = await ensureHls() } catch (e) { Hls = null }
  if (!Hls || !Hls.isSupported()) {
    sessStatus.value = ''
    err.value = '当前浏览器不支持 HLS，请用 Chrome/Edge/Safari'
    return false
  }
  destroyHls()
  hls = new Hls({ maxBufferLength: 30 })
  hls.on(Hls.Events.ERROR, (_ev, data) => {
    if (data) {
      lastHlsError.value = (data.fatal ? 'FATAL ' : '') + (data.details || data.type)
      logEvt('hls:' + (data.fatal ? 'FATAL ' : '') + (data.details || data.type),
        'frag=' + ((((data || {}).frag || {}).sn ?? '')) + ' ' +
        String((((data || {}).error || {})).message || '').slice(0, 80))
      if (data.fatal) err.value = '播放错误：' + (data.details || data.type)
      else console.warn('[hls]', data.details || data.type, data)
    }
  })
  hls.on(Hls.Events.LEVEL_LOADED, (_ev, data) => {
    try {
      const det = (data || {}).details || {}
      logEvt('hls:LEVEL_LOADED', 'live=' + !!det.live + ' frags=' + ((det.fragments || []).length) +
        ' endSN=' + (det.endSN ?? '') + ' target=' + (det.targetduration ?? ''))
    } catch (e) { /* 忽略 */ }
  })
  hls.on(Hls.Events.FRAG_BUFFERED, (_ev, data) => {
    try { logEvt('hls:FRAG_BUFFERED', 'sn=' + ((((data || {}).frag || {}).sn ?? ''))) } catch (e) { /* 忽略 */ }
  })
  hls.on(Hls.Events.MEDIA_ATTACHED, () => {
    if (targetMediaTime !== null && targetMediaTime !== undefined) {
      try { v.currentTime = Math.max(0, targetMediaTime) } catch (e) { /* 忽略 */ }
    }
    tryPlay()
  })
  hls.loadSource(url)
  hls.attachMedia(v)
  engine = 'hls'
  lastAdvanceAt = Date.now()
  return true
}
// 自动带声播放被浏览器拦截时不再静默：给明确提示 + 一键起播
const needGesture = ref(false)
// 用户是否期望在播（区分故意暂停）：tryPlay/手动播放=true，暂停键=false
let wantPlaying = false
let lastPlayAttempt = 0
function tryPlay() {
  const v = videoEl.value
  if (!v) return
  wantPlaying = true
  lastPlayAttempt = Date.now()
  try {
    const r = v.play()
    if (r && r.catch) r.then(() => { needGesture.value = false }).catch(() => { needGesture.value = true })
  } catch (e) { needGesture.value = true }
}
function userPlay() {
  const v = videoEl.value
  if (!v) return
  needGesture.value = false
  wantPlaying = true
  lastPlayAttempt = Date.now()
  v.play().catch(() => { needGesture.value = true })
}
function stopPing() {
  if (pingTimer) { clearInterval(pingTimer); pingTimer = 0 }
}
async function closeSession() {
  stopPing()
  if (sessionId) {
    const sid = sessionId
    sessionId = null
    try {
      await api(`/api/stream/sessions/${sid}`, { method: 'DELETE' })
    } catch (e) { /* 关播失败由服务端 TTL/清道夫回收 */ }
  }
}
function startPing() {
  stopPing()
  if (!sessionId) return
  pingTimer = setInterval(async () => {
    if (!sessionId) return
    try {
      const r = await api(`/api/stream/sessions/${sessionId}/ping`, { method: 'POST' })
      if (r && r.running === false && (r.segments || 0) > 0) {
        sessStatus.value = '' // 转码完成，后台收尾
      }
    } catch (e) { /* 心跳失败不打扰播放 */ }
  }, 10000)
}
// 引擎选择（hls.js 官方建议）：仅现代 Safari（ManagedMediaSource）用原生 HLS。
// Chromium 147+ 的 canPlayType('application/vnd.apple.mpegurl') 会谎报 "maybe"，
// 但原生 HLS 会解析失败（Edge 153 实测 DEMUXER_ERROR_COULD_NOT_PARSE），必须优先 hls.js。
function canUseNativeHls(v) {
  try {
    return ('ManagedMediaSource' in window)
      && !!(v && v.canPlayType('application/vnd.apple.mpegurl'))
  } catch (e) { return false }
}
// 媒体级错误（Chrome 有时只置 v.error 不触发 error 事件）：记录 + 红字，供看门狗直通恢复
function noteMediaError() {
  const v = videoEl.value
  if (!v || !v.error) return false
  const key = v.error.code + ':' + (v.error.message || '')
  if (key !== mediaErrLogged) {
    mediaErrLogged = key
    logEvt('video:error', key.slice(0, 140))
  }
  if (!err.value) {
    err.value = v.error.code === 4
      ? '播放器解析失败（格式/解码错误），正在尝试自动恢复…'
      : '播放出错（媒体错误 ' + v.error.code + '），正在尝试自动恢复…'
  }
  return true
}
async function reload() {
  err.value = ''
  sessStatus.value = ''
  needGesture.value = false
  stallTicks = 0
  lastTickPos = -1
  lastPlaylistUrl = ''
  lastHlsError.value = ''
  // 切画质/音轨/字幕烧录时保持当前播放位置（此前会从 0 重播）
  if (!seekPending.value && !resumePos) {
    try {
      const cur = Math.floor(absPos())
      if (cur > 5) resumePos = cur
    } catch (e) { /* 忽略 */ }
  }
  // 换会话前抓一帧冻结画面：窗口不塌、无图像窗口不再出现
  freezeFrame.value = captureFrame()
  await closeSession()
  destroyHls()
  const v = videoEl.value
  if (v) { try { v.pause() } catch (e) { /* 忽略 */ } v.removeAttribute('src'); v.load() }
  let d
  // 图片字幕（PGS/VobSub）：服务端把所选字幕烧录进画面，需带 sub 参数重开转码会话
  const wantBurn = imageSubSelected()
  burnOn = wantBurn
  const burnSub = wantBurn ? Number(subIdx.value) : -1
  try {
    d = await api(`/api/stream/${props.versionId}/decide?quality=${quality.value}&audio=${audioIdx.value}` +
      (burnSub >= 0 ? `&sub=${burnSub}` : ''))
  } catch (e) {
    err.value = '无法播放：' + e.message
    return
  }
  method.value = d.method
  reasons.value = d.reasons || []
  audios.value = d.media?.audio || []
  subs.value = d.media?.subs || []
  // 播放器比例：padding-top = min(片源高宽比, 76vh)（16:9 片源按屏幕宽度自适应）；
  // HLS 模式底部额外预留控件条高度，视频区不被遮挡。无数据时容器也不塌陷。
  try {
    const w = Number(d.media?.width) || 0
    const h = Number(d.media?.height) || 0
    const pct = (w > 0 && h > 0)
      ? (Math.round((h / w) * 10000) / 100) + '%'
      : '56.25%'
    const box = 'min(' + pct + ', 68vh)'
    videoPadding.value = d.method === 'direct' ? box : ('calc(' + box + ' + 46px)')
  } catch (e) { videoPadding.value = 'calc(min(56.25%, 68vh) + 46px)' }
  try {
    decidedDuration.value = Number(d.media?.duration) || 0
    // 风险自动降档：需视频重编且片源>1080p 时，原画/1080p 转码太重则自动逃到 720p，
    // 只降一次防循环；direct/remux 永远保持所选画质。
    const needVideoEncode = d.method === 'transcode' && !(d.plan || {}).vcopy
    const srcH = Number(d.media?.height) || 0
    if (needVideoEncode && srcH > 1080 && quality.value !== '720p' && !autoDropped.value) {
      autoDropped.value = true
      quality.value = '720p'
      reasons.value = [...reasons.value, 'auto_downscale_720p']
      await reload()
      return
    }
    applySubTrack()
  const startAt = resumePos || 0
  startOffset.value = Math.floor(startAt)
  mediaErrLogged = ''
  if (d.method === 'direct') {
    engine = 'direct'
    logEvt('engine:direct', '')
    v.src = encodeURI(d.direct_url) + (startAt > 0 ? `#t=${Math.floor(startAt)}` : '')
    tryPlay()
  } else {
    // 渐进式会话：服务端前 3 分片就绪即回，首画面不等整片
    sessStatus.value = d.method === 'remux' ? '正在封装…' : '正在转码（前分片生成中，稍候即播）…'
    let s
    try {
      s = await api(`/api/stream/${props.versionId}/sessions`, {
        method: 'POST',
        // 建会话要等前 3 分片（弱 CPU 转码慢），放宽到 300s，对齐服务端 deadline
        timeout: 300000,
        body: JSON.stringify({ quality: quality.value, audio: audioIdx.value,
                               start: Math.floor(startAt),
                               sub: burnSub >= 0 ? burnSub : null }),
      })
    } catch (e) {
      sessStatus.value = ''
      err.value = '无法播放：' + e.message
      return
    }
    sessionId = s.session_id
    method.value = s.method || d.method
    reasons.value = s.reasons || d.reasons || []
    startPing()
    const url = s.playlist_url
    lastPlaylistUrl = url
    recoverCount = 0
    stallTicks = 0
    lastTickPos = -1
    const onPlaying = () => { sessStatus.value = '' }
    v.addEventListener('playing', onPlaying, { once: true })
    if (canUseNativeHls(v)) {
      engine = 'native'
      logEvt('engine:native', 'ManagedMediaSource')
      v.src = url
      tryPlay()
    } else {
      engine = 'hls'
      logEvt('engine:hls', '')
      await mountHls(v, url, null)
    }
    }
  } catch (e) {
    // 兜底：起播链路任何意外都不再静默 0:00，直接显示人话错误
    sessStatus.value = ''
    err.value = '播放失败：' + (e && e.message ? e.message : e)
  }
}
function applySubTrack() {
  const v = videoEl.value
  if (!v) return
  v.querySelectorAll('track').forEach(t => t.remove())
  if (burnOn) return // 烧录模式：字幕已在画面里
  if (subIdx.value >= 0 && subs.value[subIdx.value] && !subs.value[subIdx.value].image) {
    const tr = document.createElement('track')
    tr.kind = 'subtitles'
    tr.src = `/api/stream/${props.versionId}/sub/${subIdx.value}.vtt`
    tr.default = true
    v.appendChild(tr)
  }
}
function applySub() { applySubTrack() }
// 所选字幕是否为图片型（需烧录）
function imageSubSelected() {
  const s = subs.value[subIdx.value]
  return !!(s && s.image)
}
// 字幕切换：图片↔文本/关闭 涉及烧录状态变化 → 重开转码；纯文本切换只换 <track>
function onSubChange() {
  const wantBurn = imageSubSelected()
  if (wantBurn || burnOn) reload()
  else applySubTrack()
}
// 总时长统一用探测值：HLS 增长型清单里 v.duration 只是“已产出片段之和”（如 30s），
// 用它算剩余会一开播就误判“已看”、存档 duration 也会写坏导致详情页看不到续播。
function mediaDuration(v) {
  const real = Number(decidedDuration.value) || 0
  if (real > 0) return real
  return (v && Number.isFinite(v.duration) && v.duration > 0) ? v.duration : 0
}
async function saveNow() {
  const v = videoEl.value
  if (!v || !Number.isFinite(v.currentTime) || v.currentTime <= 0) return
  const dur = mediaDuration(v)
  const pos = absPos()
  try {
    await api(`/api/stream/progress?version_id=${props.versionId}`, {
      method: 'POST', body: JSON.stringify({ position: pos, duration: dur || 0 })
    })
    lastSave = Date.now()
    posHint.value = `已记录 ${fmt(pos)}`
  } catch (e) { /* 进度上报失败不打扰播放 */ }
}
function onTime() {
  const v = videoEl.value
  if (!seekPending.value && !seekDragging.value) seekPos.value = absPos()
  lastAdvanceAt = Date.now()
  if (v && !doneWatched) {
    const dur = mediaDuration(v)
    const pos = absPos()
    const remain = dur - pos
    // 阈值标已看：剩余<5%或<300s（含片尾曲场景），只触发一次；
    // remain>0 防“时长未知/播放列表时长偏小”时的误判。
    if (dur > 0 && remain > 0 && (remain / dur < 0.05 || remain < 300)) {
      doneWatched = true
      emit('watched')
    }
  }
  if (Date.now() - lastSave > 10000) saveNow()
}
function onSeekInput(e) {
  // 拖动中：只更新本地预览值（进度条不被播放回调重置），不触发重开会话
  const t = Math.max(0, Math.floor(Number((e.target || {}).value) || 0))
  seekDragging.value = true
  seekPreview.value = t
}
function onSeekCommit(e) {
  // 松手：提交目标秒数 → 关旧会话开新会话
  const raw = Number((e.target || {}).value)
  const t = seekDragging.value ? seekPreview.value
    : Math.max(0, Math.floor(Number.isFinite(raw) ? raw : seekPos.value))
  seekDragging.value = false
  doSeek(t)
}
function doSeek(t) {
  // HLS：拖动即关旧开新（复用 start 参数），新流 playing 后解冻；Direct：直接改 currentTime
  t = Math.max(0, Math.floor(Number(t) || 0))
  if (t === Math.floor(absPos())) return
  resumeOffer.value = ''
  seekPos.value = t
  seekPreview.value = t
  if (method.value === 'direct') {
    const v = videoEl.value
    if (v) { try { v.currentTime = t } catch (e) { /* 忽略 */ } }
    return
  }
  resumePos = t
  seekPending.value = true
  seekPendingSince = Date.now()
  try { videoEl.value && videoEl.value.pause() } catch (err) { /* 忽略 */ }
  sessStatus.value = '正在转码…'
  reload()
}
async function onEnded() {
  await saveNow()
  emit('watched')
}
function resumePlay() {
  // 起播时会话已按断点 start 开流（direct 靠 #t），这里只需消条；
  // 只有 direct 且浏览器没吃 #t 时才补跳一次。
  resumeOffer.value = ''
  const v = videoEl.value
  if (v && method.value === 'direct' && resumePos > 0) {
    try { v.currentTime = resumePos } catch (e) { /* 忽略 */ }
  }
  resumePos = 0
}
async function restartPlay() {
  resumeOffer.value = ''
  resumePos = 0
  startOffset.value = 0
  seekPos.value = 0
  try {
    await api(`/api/stream/progress?version_id=${props.versionId}`, { method: 'DELETE' })
  } catch (e) { /* 忽略 */ }
  reload()
}
function onVideoError() {
  if (err.value) return
  // 分流：Direct 是文件问题；HLS 多半是分片/缓冲断流（可重试或降档），别报“文件损坏”吓人
  err.value = method.value && method.value !== 'direct'
    ? '播放中断（分片加载失败），可关闭重进，或切 720p 再试'
    : '文件为空或损坏，无法播放，请下载检查'
}
function onPlayingHide() {
  needGesture.value = false
  seekPending.value = false
  seekPendingSince = 0
  seekDragging.value = false
  freezeFrame.value = ''
  lastAdvanceAt = Date.now()
}
// 冻结当前帧（MSE 同源分片不污染画布，可安全 toDataURL）；无画面返回 ''
function captureFrame() {
  try {
    const v = videoEl.value
    if (!v || !v.videoWidth || v.readyState < 2) return ''
    const c = document.createElement('canvas')
    c.width = v.videoWidth
    c.height = v.videoHeight
    const ctx = c.getContext('2d')
    if (!ctx) return ''
    ctx.drawImage(v, 0, 0, c.width, c.height)
    return c.toDataURL('image/jpeg', 0.72)
  } catch (e) { return '' }
}
function onFullChange() {
  isFull.value = !!document.fullscreenElement
  if (!isFull.value) {
    mouseActive.value = false
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = 0 }
  }
}
// 键盘快捷键（输入框/下拉聚焦时不拦截）：
// 空格=播放/暂停；←/→ = ±10s；↑/↓ = 音量 ±5%；Esc：全屏时只退全屏，非全屏才关播放器
function showOverlay() {
  if (hideTimer) { clearTimeout(hideTimer); hideTimer = 0 }
  mouseActive.value = true
  if (!isFull.value) return
  hideTimer = setTimeout(() => { mouseActive.value = false }, 3000)
}
function seekBy(delta) {
  const cur = absPos()
  const dur = Number(decidedDuration.value) || 0
  let t = Math.floor(cur + delta)
  if (t < 0) t = 0
  if (dur > 0 && t > Math.floor(dur) - 1) t = Math.floor(dur) - 1
  if (t === Math.floor(cur)) return
  logEvt('kbd:seek', fmt(t))
  doSeek(t)
}
function volumeBy(delta) {
  const v = videoEl.value
  if (!v) return
  const x = Math.max(0, Math.min(1, Number(v.volume ?? 1) + delta))
  v.volume = x
  v.muted = x <= 0
  volume.value = x
  muted.value = v.muted
  logEvt('kbd:vol', Math.round(x * 100) + '%')
}
function onKeydown(e) {
  const t = e.target || {}
  const tag = String(t.tagName || '').toLowerCase()
  const typing = tag === 'input' || tag === 'select' || tag === 'textarea' || t.isContentEditable
  if (e.code === 'Space' && !typing) {
    if (!isHls.value) return // Direct 模式交给浏览器原生控件
    e.preventDefault()
    showOverlay()
    togglePlay()
    return
  }
  if ((e.key === 'ArrowRight' || e.key === 'ArrowLeft') && !typing) {
    e.preventDefault()
    showOverlay()
    seekBy(e.key === 'ArrowRight' ? 10 : -10)
    return
  }
  if ((e.key === 'ArrowUp' || e.key === 'ArrowDown') && !typing) {
    e.preventDefault()
    showOverlay()
    volumeBy(e.key === 'ArrowUp' ? 0.05 : -0.05)
    return
  }
  if (e.key === 'Escape') {
    if (document.fullscreenElement) return // 浏览器先退全屏，再由用户决定是否关闭
    emit('close')
  }
}
function togglePlay() {
  const v = videoEl.value
  if (!v) return
  if (v.paused) { needGesture.value = false; wantPlaying = true; lastPlayAttempt = Date.now(); v.play().catch(() => { needGesture.value = true }) }
  else { wantPlaying = false; v.pause() }
}
function toggleMute() {
  const v = videoEl.value
  if (!v) return
  v.muted = !v.muted
  muted.value = v.muted
}
function setVolume(e) {
  const v = videoEl.value
  const x = Math.max(0, Math.min(100, Number((e.target || {}).value) || 0)) / 100
  volume.value = x
  if (!v) return
  v.volume = x
  v.muted = x <= 0
  muted.value = v.muted
}
function toggleFull() {
  const el = pvWrapEl.value
  try {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {})
    else if (el && el.requestFullscreen) el.requestFullscreen().catch(() => {})
  } catch (e) { /* 忽略 */ }
}
function onPlayState() {
  const v = videoEl.value
  isPlaying.value = !!(v && !v.paused && !v.ended)
  if (v) { muted.value = !!v.muted; volume.value = Number(v.volume ?? 1) }
}
async function copyDebug() {
  const info = debugSnapshot()
  try {
    const v = videoEl.value
    if (v && v.getVideoPlaybackQuality) {
      const q = v.getVideoPlaybackQuality()
      info.dropped = q.droppedVideoFrames
    }
  } catch (e) { /* 忽略 */ }
  try {
    if (sessionId) {
      const d = await api(`/api/stream/sessions/${sessionId}/debug`)
      info.debug = { running: d.running, exit: d.exit_code, segs: d.segments,
        items: d.playlist_items, finished: d.finished, complete: !!d.complete }
    }
    await navigator.clipboard.writeText(JSON.stringify(info))
    posHint.value = '调试信息已复制，贴给开发者即可定位'
  } catch (e) {
    posHint.value = '复制失败：' + JSON.stringify(info)
  }
}
onMounted(async () => {
  try {
    const p = await api(`/api/stream/progress?version_id=${props.versionId}`)
    const pos = Number(p.position) || 0
    let dur = Number(p.duration) || 0
    // 旧版本用 HLS 增长清单时长（如 30s）写坏过存档：dur < pos 视为不可信，按未看完处理
    if (dur > 0 && dur < pos) dur = 0
    const remain = dur - pos
    if (pos > 15 && (dur === 0 || !(remain / dur < 0.05 || remain < 300))) {
      resumePos = pos
      resumeOffer.value = p.position_text || fmt(pos)
    }
  } catch (e) { /* 无断点直接播 */ }
  await reload()
  bindVideo(videoEl.value)
  document.addEventListener('fullscreenchange', onFullChange)
  window.addEventListener('beforeunload', saveNow)
  window.addEventListener('keydown', onKeydown)
  try {
    window.__jzPlayerDebug = () => debugSnapshot()
  } catch (e) { /* 忽略 */ }
  bufTimer = setInterval(() => {
    try {
      const v = videoEl.value
      if (v && v.buffered && v.buffered.length && Number.isFinite(v.currentTime)) {
        bufSecs.value = Math.max(0, v.buffered.end(v.buffered.length - 1) - v.currentTime)
      } else {
        bufSecs.value = 0
      }
      // 媒体级错误（v.error 有时不触发 error 事件）：元素已中毒，直通元素级恢复
      if (noteMediaError() && Date.now() - lastRecoverAt > 8000 && recoverCount < 3) {
        recoverStream(true)
      }
      watchStall()
    } catch (e) { bufSecs.value = 0 }
  }, 2000)
})
function watchStall() {
  // 每 2s 一拍。判定条件（不再看缓冲量——缓冲充足也可能楔死）：
  // HLS 会话存活、非 seek 切换中、元素声称在播(!paused && !ended)、有数据(readyState>=2)、
  // currentTime 连续 3 拍(约6s)不动 → 判定卡死。seeking 恒 true 约 10s、媒体错误同样自救。
  try {
    const v = videoEl.value
    if (!isHls.value || !sessionId || !lastPlaylistUrl) { stallTicks = 0; return }
    if (seekPending.value) {
      if (!seekPendingSince) seekPendingSince = Date.now()
      if (Date.now() - seekPendingSince > 30000) {
        logEvt('watchdog', 'seekPending 超时放行')
        seekPending.value = false
        seekPendingSince = 0
      } else {
        return // seek 重开进行中，不跟它抢
      }
    }
    if (!v || v.ended) { stallTicks = 0; lastTickPos = -1; lastAdvanceAt = Date.now() }
    else if (v.paused) {
      stallTicks = 0; lastTickPos = -1
      if (v.error) {
        // 元素级致命错误：重试播放永远无效，直接元素级恢复
        if (Date.now() - lastRecoverAt > 8000 && recoverCount < 3) {
          logEvt('watchdog', 'media-error 元素级恢复')
          recoverStream(true)
        }
      } else if (wantPlaying && (v.readyState || 0) >= 2 && Date.now() - lastPlayAttempt > 5000) {
        logEvt('watchdog', 'paused-but-wanted 重试播放')
        tryPlay()
      } else {
        lastAdvanceAt = Date.now()
      }
    }
    else if ((v.readyState || 0) >= 2) {
      const cur = Number(v.currentTime) || 0
      const moved = lastTickPos >= 0 && Math.abs(cur - lastTickPos) > 0.05
      lastTickPos = cur
      if (moved) { stallTicks = 0; lastAdvanceAt = Date.now() }
      else {
        stallTicks += 1
        if (stallTicks >= 3) { stallTicks = 0; recoverStream() }
      }
    } else {
      stallTicks = 0 // 没数据（readyState<2）是真缺片，等分片而非自救
    }
    // seeking 恒定 true 超过约 10s（定位永远完不成）同样自救
    try {
      const vk = videoEl.value
      if (vk && vk.seeking && !vk.paused) {
        seekStuckTicks += 1
        if (seekStuckTicks >= 5) { seekStuckTicks = 0; recoverStream() }
      } else {
        seekStuckTicks = 0
      }
    } catch (e) { /* 忽略 */ }
  } catch (e) { /* 看门狗自身永不抛错 */ }
}
function bindVideo(v) {
  if (!v) return
  v.addEventListener('timeupdate', onTime)
  v.addEventListener('pause', saveNow)
  v.addEventListener('pause', onPlayState)
  v.addEventListener('ended', onEnded)
  v.addEventListener('playing', onPlayingHide)
  v.addEventListener('play', onPlayState)
  v.addEventListener('waiting', () => logEvt('video:waiting', 't=' + fmtT(v)))
  v.addEventListener('stalled', () => logEvt('video:stalled', 't=' + fmtT(v)))
  v.addEventListener('seeking', () => logEvt('video:seeking', 'to=' + fmtT(v)))
  v.addEventListener('seeked', () => { logEvt('video:seeked', 't=' + fmtT(v)); lastAdvanceAt = Date.now() })
  v.addEventListener('emptied', () => logEvt('video:emptied', ''))
  v.addEventListener('suspend', () => logEvt('video:suspend', ''))
  v.addEventListener('abort', () => logEvt('video:abort', ''))
  v.addEventListener('canplay', () => logEvt('video:canplay', ''))
  muted.value = !!v.muted
  volume.value = Number(v.volume ?? 1)
}
function unbindVideo(v) {
  if (!v) return
  v.removeEventListener('timeupdate', onTime)
  v.removeEventListener('pause', saveNow)
  v.removeEventListener('pause', onPlayState)
  v.removeEventListener('ended', onEnded)
  v.removeEventListener('playing', onPlayingHide)
  v.removeEventListener('play', onPlayState)
}
function fmtT(v) {
  try { return (Number(v.currentTime) || 0).toFixed(1) } catch (e) { return '?' }
}
function bufferedRanges() {
  try {
    const v = videoEl.value
    const out = []
    if (v && v.buffered) {
      for (let i = 0; i < v.buffered.length; i++) {
        out.push([Math.round(v.buffered.start(i) * 10) / 10, Math.round(v.buffered.end(i) * 10) / 10])
      }
    }
    return out
  } catch (e) { return [] }
}
function debugSnapshot() {
  const v = videoEl.value
  const info = {
    version: props.versionId, method: method.value, session: sessionId,
    pos: Math.floor(absPos()), buffered: Math.floor(bufSecs.value),
    hlsError: lastHlsError.value || '', quality: quality.value,
    recoverCount, engine,
  }
  try {
    if (v) {
      info.el = { ready: v.readyState, net: v.networkState, paused: v.paused,
        seeking: v.seeking, ended: v.ended, ct: Math.round((Number(v.currentTime) || 0) * 10) / 10,
        err: (v.error && (v.error.code + ':' + (v.error.message || ''))) || '' }
      info.ranges = bufferedRanges()
    }
  } catch (e) { /* 忽略 */ }
  try {
    if (hls) {
      let lat = null, edge = null
      try { lat = hls.latency } catch (e) { /* 忽略 */ }
      try { edge = hls.liveSyncPosition } catch (e) { /* 忽略 */ }
      info.hls = { latency: lat, liveEdge: edge }
      const lv = (((hls.levels || [])[hls.currentLevel] || {}).details) || null
      if (lv) {
        info.hls.level = { live: !!lv.live, frags: (lv.fragments || []).length,
          total: Math.round(lv.totalduration || 0), target: lv.targetduration,
          endSN: lv.endSN ?? null }
      }
    }
  } catch (e) { /* 忽略 */ }
  info.events = evtLog.slice(-25)
  return info
}
async function recoverStream(forceElement = false) {
  // 同会话自救（不杀转码进程，分片继续产）：
  // 常规：重建 hls 实例；媒体级致命错误(forceElement)或第 3 次：连 <video> 元素一起换新。
  if (recoverCount >= 3) {
    err.value = '多次自动恢复失败，请关闭重进或切 720p'
    return
  }
  if (!lastPlaylistUrl) {
    err.value = '无法自动恢复（缺少播放地址），请关闭重进'
    return
  }
  recoverCount += 1
  lastRecoverAt = Date.now()
  const target = Math.max(0, absPos() - startOffset.value)
  const useElement = forceElement || recoverCount >= 3
  logEvt('recover', 'attempt=' + recoverCount + ' element=' + useElement + ' target=' + target.toFixed(1))
  console.warn('[play-recover] attempt', recoverCount, 'element=', useElement)
  sessStatus.value = '检测到停滞，正在恢复（第' + recoverCount + '次）…'
  try {
    if (useElement) {
      // 元素级楔死/媒体解析错误：换全新 video 节点再挂 hls
      const old = videoEl.value
      try { unbindVideo(old) } catch (e) { /* 忽略 */ }
      destroyHls()
      try { if (old) { old.pause() } } catch (e) { /* 忽略 */ }
      videoKey.value += 1
      mediaErrLogged = ''
      await nextTick()
      const nv = videoEl.value
      if (!nv) return
      bindVideo(nv)
      muted.value = !!nv.muted
      volume.value = Number(nv.volume ?? 1)
      await mountHls(nv, lastPlaylistUrl, Math.max(0, target - 0.5))
      return
    }
    destroyHls()
    const v = videoEl.value
    if (!v) return
    await mountHls(v, lastPlaylistUrl, Math.max(0, target - 0.5))
  } catch (e) { /* 恢复失败等下次节拍或转 err */ }
}
onUnmounted(() => {
  saveNow()
  closeSession()
  try { unbindVideo(videoEl.value) } catch (e) { /* 忽略 */ }
  document.removeEventListener('fullscreenchange', onFullChange)
  window.removeEventListener('beforeunload', saveNow)
  window.removeEventListener('keydown', onKeydown)
  if (hideTimer) clearTimeout(hideTimer)
  if (bufTimer) clearInterval(bufTimer)
  if (saveTimer) clearTimeout(saveTimer)
  destroyHls()
})
</script>
<style scoped>
/* 弹窗自足样式：不再依赖父组件 scoped 的 .dlg；尺寸随屏幕比例自适应 */
.player-dlg {
  background: #161616; border-radius: 12px; padding: 12px 14px 10px;
  width: min(66vw, 1400px); max-width: min(66vw, 1400px);
  max-height: 94vh; overflow: auto;
  display: flex; flex-direction: column; gap: 8px;
  box-sizing: border-box;
}
.pd-head { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; min-width: 0; }
.pd-head h3 { margin: 0; font-size: 1.0625rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 46%; }
.pd-spacer { flex: 1; }
.pd-mini { font-size: 0.75rem; padding: 3px 10px; }

.pv-wrap { --pvb: 0px; position: relative; background: #000; border-radius: 8px; overflow: hidden; width: 100%; }
.pv-wrap.has-bar { --pvb: 46px; }
.pv-wrap.hide-cursor { cursor: none; }
.player-video { position: absolute; top: 0; left: 0; right: 0; bottom: var(--pvb); width: 100%; height: auto; object-fit: contain; background: #000; display: block; }
.freeze-frame { position: absolute; top: 0; left: 0; right: 0; bottom: var(--pvb); width: 100%; height: auto; object-fit: contain; background: #000; }
.seek-ov { position: absolute; top: 0; left: 0; right: 0; bottom: var(--pvb); display: flex; gap: 10px; align-items: center; justify-content: center; background: rgba(0, 0, 0, .45); color: #e0a63c; font-size: 0.9375rem; }
.pv-err { position: absolute; left: 10px; right: 10px; bottom: calc(var(--pvb) + 8px); margin: 0; padding: 6px 10px; border-radius: 6px; background: rgba(0,0,0,.72); color: #e0a63c; font-size: 0.8125rem; z-index: 3; }

/* 底部控件：窗口态在视频下方保留条内；全屏态为悬浮层并按鼠标显隐 */
.pv-ctl { position: absolute; left: 0; right: 0; bottom: 0; height: var(--pvb); display: flex; gap: 8px; align-items: center; padding: 0 10px; box-sizing: border-box; background: #101010; z-index: 2; }
.pv-ctl button { padding: 4px 10px; }
.pv-ctl select { max-width: 130px; min-width: 0; background: #262626; color: #ddd; border: 1px solid #444; border-radius: 6px; padding: 4px 6px; font-size: 0.75rem; }
.ctl-time { font-size: 0.75rem; color: #999; white-space: nowrap; }
.ctl-seek { flex: 1; min-width: 80px; }
.ctl-vol { width: 80px; }

/* 顶部标题条：仅全屏显示 */
.pv-top { display: none; }
.pv-status { color: #e0a63c; font-size: 0.8125rem; }

/* ===== 全屏（容器全屏：画面居中、控件悬浮可隐） ===== */
.pv-wrap:fullscreen { padding-top: 0 !important; width: 100vw; height: 100vh; border-radius: 0; --pvb: 0px; }
.pv-wrap:fullscreen .player-video,
.pv-wrap:fullscreen .freeze-frame,
.pv-wrap:fullscreen .seek-ov { bottom: 0; height: 100%; }
.pv-wrap:fullscreen .pv-err { bottom: 76px; }
.pv-wrap:fullscreen .pv-ctl {
  height: auto; padding: 26px 18px 14px;
  background: linear-gradient(transparent, rgba(0, 0, 0, .88));
  opacity: 0; pointer-events: none; transition: opacity .25s ease;
  position: absolute; left: 0; right: 0; bottom: 0; top: auto;
}
.pv-wrap:fullscreen .pv-ctl.show { opacity: 1; pointer-events: auto; }
.pv-wrap:fullscreen .pv-top {
  display: flex; gap: 12px; align-items: center;
  position: absolute; left: 0; right: 0; top: 0; padding: 12px 18px 30px; box-sizing: border-box;
  background: linear-gradient(rgba(0, 0, 0, .85), transparent);
  opacity: 0; pointer-events: none; transition: opacity .25s ease; z-index: 4;
}
.pv-wrap:fullscreen .pv-top.show { opacity: 1; pointer-events: auto; }
.pv-title { color: #eee; font-size: 0.9375rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.play-method { color: #888; font-size: 0.75rem; }
.play-reason { color: #9ecfff; font-size: 0.75rem; }
.sess-status { color: #e0a63c; font-size: 0.75rem; }
.gesture-bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; background: #262626; border: 1px solid #2b6cb0; border-radius: 8px; padding: 8px 12px; margin: 4px 0 0; color: #9ecfff; font-size: 0.875rem; }
.play-now { font-size: 1rem; padding: 6px 22px; border-radius: 999px; background: #2b6cb0; border: 1px solid #2b6cb0; color: #fff; cursor: pointer; }
.resume-bar { display: flex; gap: 8px; align-items: center; color: #7ed321; font-size: 0.875rem; flex-wrap: wrap; }
.hint-line { margin: 0; color: #666; font-size: 0.8125rem; }
.hint.warn { color: #e0a63c; }
</style>
