<template>
  <div class="dlg-mask" @click.self="$emit('close')">
    <div class="dlg player-dlg" ref="dlgEl">
      <h3>{{ title || ('版本 ' + versionId) }}</h3>
      <p v-if="methodLine" class="play-method">{{ methodLine }}</p>
      <p v-if="reasonLine" class="play-reason">{{ reasonLine }}</p>
      <p v-if="sessStatus" class="sess-status">{{ sessStatus }}</p>
      <div v-if="resumeOffer" class="resume-bar">
        <span>上次看到 {{ resumeOffer }}</span>
        <button @click="resumePlay">继续播放</button>
        <button @click="restartPlay">从头开始</button>
      </div>
      <video ref="videoEl" :key="videoKey" :controls="!isHls" autoplay playsinline preload="metadata" class="player-video"
        @error="onVideoError"></video>
      <div v-if="isHls" class="ctl-bar">
        <button @click="togglePlay">{{ isPlaying ? '⏸' : '▶' }}</button>
        <span class="ctl-time">{{ fmt(seekPos) }} / {{ fmt(decidedDuration) }}</span>
        <input type="range" min="0" :max="Math.floor(decidedDuration)" step="1"
          :value="Math.floor(seekPos)" :disabled="seekPending || !(decidedDuration > 0)"
          @change="doSeek" class="ctl-seek" />
        <button @click="toggleMute">{{ muted ? '🔇' : '🔊' }}</button>
        <input type="range" min="0" max="100" :value="muted ? 0 : volume * 100"
          @input="setVolume" class="ctl-vol" />
        <button @click="toggleFull">{{ isFull ? '⤢' : '⛶' }}</button>
      </div>
      <div v-if="needGesture" class="gesture-bar">
        <span>片源已就绪，浏览器阻止了自动带声播放</span>
        <button class="play-now" @click="userPlay">▶ 点击播放</button>
      </div>
      <div class="play-opts">
        <label>画质
          <select v-model="quality" @change="reload">
            <option value="original">原画（默认）</option>
            <option value="1080p">1080p</option>
            <option value="720p">720p（弱 NAS 友好）</option>
          </select>
        </label>
        <label v-if="audios.length > 1">音轨
          <select v-model.number="audioIdx" @change="reload">
            <option v-for="(a, i) in audios" :key="i" :value="i">
              {{ audioLabel(a, i) }}
            </option>
          </select>
        </label>
        <label v-if="subs.length">字幕
          <select v-model.number="subIdx" @change="applySub">
            <option :value="-1">关闭</option>
            <option v-for="(s, i) in subs" :key="i" :value="i" :disabled="!!s.image">
              {{ subLabel(s, i) }}{{ s.image ? '（图片字幕，电视/Kodi可看）' : '' }}
            </option>
          </select>
          <span v-if="subs.length && !subs.some(s => !s.image)" class="sub-note">内封字幕均为图片型，浏览器不支持切换</span>
        </label>
      </div>
      <p v-if="err" class="hint warn">{{ err }}</p>
      <div class="bar">
        <span class="pos-hint">{{ bufLine || posHint }}</span>
        <button @click="copyDebug">复制调试信息</button>
        <button @click="$emit('close')">关闭</button>
      </div>
    </div>
  </div>
</template>
<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { api } from '../api.js'
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
let lastPlaylistUrl = ''
const lastHlsError = ref('')
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
const dlgEl = ref(null)
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
  pgs_needs_burn: '内封图片字幕需烧录（很耗 CPU），建议关闭字幕或下载原盘',
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
  lastAdvanceAt = Date.now()
  return true
}
// 自动带声播放被浏览器拦截时不再静默：给明确提示 + 一键起播
const needGesture = ref(false)
function tryPlay() {
  const v = videoEl.value
  if (!v) return
  try {
    const r = v.play()
    if (r && r.catch) r.then(() => { needGesture.value = false }).catch(() => { needGesture.value = true })
  } catch (e) { needGesture.value = true }
}
function userPlay() {
  const v = videoEl.value
  if (!v) return
  needGesture.value = false
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
async function reload() {
  err.value = ''
  sessStatus.value = ''
  needGesture.value = false
  stallTicks = 0
  lastTickPos = -1
  lastPlaylistUrl = ''
  lastHlsError.value = ''
  await closeSession()
  destroyHls()
  const v = videoEl.value
  if (v) { try { v.pause() } catch (e) { /* 忽略 */ } v.removeAttribute('src'); v.load() }
  let d
  try {
    d = await api(`/api/stream/${props.versionId}/decide?quality=${quality.value}&audio=${audioIdx.value}`)
  } catch (e) {
    err.value = '无法播放：' + e.message
    return
  }
  method.value = d.method
  reasons.value = d.reasons || []
  audios.value = d.media?.audio || []
  subs.value = d.media?.subs || []
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
  if (d.method === 'direct') {
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
        body: JSON.stringify({ quality: quality.value, audio: audioIdx.value, start: Math.floor(startAt) }),
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
    if (v.canPlayType('application/vnd.apple.mpegurl')) {
      v.src = url
      tryPlay()
    } else {
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
  if (subIdx.value >= 0 && subs.value[subIdx.value] && !subs.value[subIdx.value].image) {
    const tr = document.createElement('track')
    tr.kind = 'subtitles'
    tr.src = `/api/stream/${props.versionId}/sub/${subIdx.value}.vtt`
    tr.default = true
    v.appendChild(tr)
  }
}
function applySub() { applySubTrack() }
async function saveNow() {
  const v = videoEl.value
  if (!v || !Number.isFinite(v.currentTime) || v.currentTime <= 0) return
  const dur = Number.isFinite(v.duration) && v.duration > 0 ? v.duration : decidedDuration.value
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
  if (!seekPending.value) seekPos.value = absPos()
  lastAdvanceAt = Date.now()
  if (v && !doneWatched) {
    const dur = Number.isFinite(v.duration) && v.duration > 0 ? v.duration : decidedDuration.value
    const remain = dur - absPos()
    // 阈值标已看：剩余<5%或<300s（含片尾曲场景），只触发一次
    if (dur > 0 && (remain / dur < 0.05 || remain < 300)) {
      doneWatched = true
      emit('watched')
    }
  }
  if (Date.now() - lastSave > 10000) saveNow()
}
function doSeek(e) {
  // HLS 自绘进度：拖动即暂停+冻结滑块+提示，关旧开新（复用 start 参数），新流 playing 后解冻
  const t = Math.max(0, Math.floor(Number((e.target || {}).value) || 0))
  resumeOffer.value = ''
  resumePos = t
  seekPos.value = t
  seekPending.value = true
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
function onPlayingHide() { needGesture.value = false; seekPending.value = false; lastAdvanceAt = Date.now() }
function onFullChange() { isFull.value = !!document.fullscreenElement }
function togglePlay() {
  const v = videoEl.value
  if (!v) return
  if (v.paused) { needGesture.value = false; v.play().catch(() => { needGesture.value = true }) }
  else v.pause()
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
  const el = dlgEl.value
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
    const dur = Number(p.duration) || 0
    const remain = dur - pos
    if (pos > 15 && dur > 0 && !(remain / dur < 0.05 || remain < 300)) {
      resumePos = pos
      resumeOffer.value = p.position_text || fmt(pos)
    }
  } catch (e) { /* 无断点直接播 */ }
  await reload()
  bindVideo(videoEl.value)
  document.addEventListener('fullscreenchange', onFullChange)
  window.addEventListener('beforeunload', saveNow)
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
      watchStall()
    } catch (e) { bufSecs.value = 0 }
  }, 2000)
})
function watchStall() {
  // 每 2s 一拍。判定条件（不再看缓冲量——缓冲充足也可能楔死）：
  // HLS 会话存活、非 seek 切换中、元素声称在播(!paused && !ended)、有数据(readyState>=2)、
  // currentTime 连续 3 拍(约6s)不动 → 判定卡死。seeking 恒 true 超约 10s 同样自救。
  try {
    const v = videoEl.value
    if (!isHls.value || !sessionId || !lastPlaylistUrl) { stallTicks = 0; return }
    if (seekPending.value) return // seek 重开进行中，不跟它抢
    if (!v || v.paused || v.ended) { stallTicks = 0; lastTickPos = -1; lastAdvanceAt = Date.now() }
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
    recoverCount,
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
async function recoverStream() {
  // 同会话自救（不杀转码进程，分片继续产）：
  // 第 1-2 次只重建 hls 实例；第 3 次连 <video> 元素一起重建（应对元素级楔死）。
  if (recoverCount >= 3 || !lastPlaylistUrl) {
    if (recoverCount >= 3) err.value = '多次自动恢复失败，请关闭重进或切 720p'
    return
  }
  recoverCount += 1
  const target = Math.max(0, absPos() - startOffset.value)
  logEvt('recover', 'attempt=' + recoverCount + ' target=' + target.toFixed(1))
  console.warn('[hls-recover] reattach same session, attempt', recoverCount)
  sessStatus.value = '检测到停滞，正在恢复（第' + recoverCount + '次）…'
  try {
    if (recoverCount >= 3) {
      // 元素级楔死：换全新 video 节点再挂 hls
      const old = videoEl.value
      try { unbindVideo(old) } catch (e) { /* 忽略 */ }
      destroyHls()
      try { if (old) { old.pause() } } catch (e) { /* 忽略 */ }
      videoKey.value += 1
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
  if (bufTimer) clearInterval(bufTimer)
  if (saveTimer) clearTimeout(saveTimer)
  destroyHls()
})
</script>
<style scoped>
.player-dlg { max-width: 960px; }
.player-video { width: 100%; max-height: 60vh; background: #000; border-radius: 8px; }
.play-method { color: #888; font-size: 0.8125rem; margin: 0 0 4px; }
.play-reason { color: #9ecfff; font-size: 0.8125rem; margin: 0 0 8px; }
.sess-status { color: #e0a63c; font-size: 0.8125rem; margin: 0 0 8px; }
.gesture-bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; background: #262626; border: 1px solid #2b6cb0; border-radius: 8px; padding: 8px 12px; margin: 8px 0; color: #9ecfff; font-size: 0.875rem; }
.play-now { font-size: 1rem; padding: 6px 22px; border-radius: 999px; background: #2b6cb0; border: 1px solid #2b6cb0; color: #fff; cursor: pointer; }
.resume-bar { display: flex; gap: 8px; align-items: center; color: #7ed321; font-size: 0.875rem; margin-bottom: 8px; flex-wrap: wrap; }
.play-opts { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-top: 8px; font-size: 0.875rem; color: #aaa; }
.pos-hint { color: #666; font-size: 0.8125rem; margin-right: auto; }
.seek-row { display: flex; gap: 8px; align-items: center; margin-top: 6px; font-size: 0.75rem; color: #888; }
.seek-row input[type="range"] { flex: 1; }
.ctl-bar { display: flex; gap: 8px; align-items: center; margin-top: 6px; }
.ctl-bar button { padding: 4px 10px; }
.ctl-time { font-size: 0.75rem; color: #888; white-space: nowrap; }
.ctl-seek { flex: 1; }
.ctl-vol { width: 90px; }
.sub-note { color: #888; font-size: 0.75rem; }
.hint.warn { color: #e0a63c; }
</style>
