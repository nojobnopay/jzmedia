<template>
  <div class="dlg-mask" @click.self="$emit('close')">
    <div class="dlg player-dlg">
      <h3>{{ title || ('版本 ' + versionId) }}</h3>
      <p v-if="methodLine" class="play-method">{{ methodLine }}</p>
      <p v-if="reasonLine" class="play-reason">{{ reasonLine }}</p>
      <p v-if="sessStatus" class="sess-status">{{ sessStatus }}</p>
      <div v-if="resumeOffer" class="resume-bar">
        <span>上次看到 {{ resumeOffer }}</span>
        <button @click="resumePlay">继续播放</button>
        <button @click="restartPlay">从头开始</button>
      </div>
      <video ref="videoEl" controls autoplay playsinline preload="metadata" class="player-video"
        @error="onVideoError"></video>
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
              {{ subLabel(s, i) }}{{ s.image ? '（内封图片，需下载原盘）' : '' }}
            </option>
          </select>
        </label>
      </div>
      <p v-if="err" class="hint warn">{{ err }}</p>
      <div class="bar">
        <span class="pos-hint">{{ posHint }}</span>
        <button @click="$emit('close')">关闭</button>
      </div>
    </div>
  </div>
</template>
<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
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
const sessStatus = ref('')
let sessionId = null
let pingTimer = 0
const err = ref('')
const posHint = ref('')
const resumeOffer = ref('')
let resumePos = 0
let decidedDuration = 0
let doneWatched = false

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
  decidedDuration.value = Number(d.media?.duration) || 0
  applySubTrack()
  const startAt = resumePos || 0
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
    const onPlaying = () => { sessStatus.value = '' }
    v.addEventListener('playing', onPlaying, { once: true })
    if (v.canPlayType('application/vnd.apple.mpegurl')) {
      v.src = url
      tryPlay()
    } else {
      let Hls = null
      try { Hls = await ensureHls() } catch (e) { Hls = null }
      if (Hls && Hls.isSupported()) {
        hls = new Hls({ maxBufferLength: 30 })
        hls.on(Hls.Events.ERROR, (_ev, data) => {
          if (data && data.fatal) err.value = '播放错误：' + (data.details || data.type)
        })
        hls.on(Hls.Events.MEDIA_ATTACHED, () => tryPlay())
        hls.loadSource(url)
        hls.attachMedia(v)
      } else {
        sessStatus.value = ''
        err.value = '当前浏览器不支持 HLS，请用 Chrome/Edge/Safari'
      }
    }
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
  try {
    await api(`/api/stream/progress?version_id=${props.versionId}`, {
      method: 'POST', body: JSON.stringify({ position: v.currentTime, duration: dur || 0 })
    })
    lastSave = Date.now()
    posHint.value = `已记录 ${fmt(v.currentTime)}`
  } catch (e) { /* 进度上报失败不打扰播放 */ }
}
function onTime() {
  const v = videoEl.value
  if (v && !doneWatched) {
    const dur = Number.isFinite(v.duration) && v.duration > 0 ? v.duration : decidedDuration.value
    const remain = dur - v.currentTime
    // 阈值标已看：剩余<5%或<300s（含片尾曲场景），只触发一次
    if (dur > 0 && (remain / dur < 0.05 || remain < 300)) {
      doneWatched = true
      emit('watched')
    }
  }
  if (Date.now() - lastSave > 10000) saveNow()
}
async function onEnded() {
  await saveNow()
  emit('watched')
}
function resumePlay() {
  const v = videoEl.value
  if (v && resumePos > 0) {
    // HLS 起播 seek 由 start 参数完成（reload 时已带）；direct 由 #t 完成。
    // 若已在播放中则直接跳
    try { v.currentTime = resumePos } catch (e) { /* 忽略 */ }
  }
  resumeOffer.value = ''
  resumePos = 0
}
async function restartPlay() {
  resumeOffer.value = ''
  resumePos = 0
  try {
    await api(`/api/stream/progress?version_id=${props.versionId}`, { method: 'DELETE' })
  } catch (e) { /* 忽略 */ }
  reload()
}
function onVideoError() {
  if (!err.value) err.value = '文件为空或损坏，无法播放，请下载检查'
}
function onPlayingHide() { needGesture.value = false }
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
  const v = videoEl.value
  if (v) {
    v.addEventListener('timeupdate', onTime)
    v.addEventListener('pause', saveNow)
    v.addEventListener('ended', onEnded)
    v.addEventListener('playing', onPlayingHide)
  }
  window.addEventListener('beforeunload', saveNow)
})
onUnmounted(() => {
  saveNow()
  closeSession()
  const v = videoEl.value
  if (v) {
    v.removeEventListener('timeupdate', onTime)
    v.removeEventListener('pause', saveNow)
    v.removeEventListener('ended', onEnded)
    v.removeEventListener('playing', onPlayingHide)
  }
  window.removeEventListener('beforeunload', saveNow)
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
.hint.warn { color: #e0a63c; }
</style>
