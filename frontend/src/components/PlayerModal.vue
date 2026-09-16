<template>
  <div class="dlg-mask" @click.self="$emit('close')">
    <div class="player-dlg">
      <div class="pd-head">
        <h3>{{ title || ('版本 ' + versionId) }}</h3>
        <span v-if="methodLine" class="play-method">{{ methodLine }}</span>
        <span v-if="qualityLine" class="play-quality">{{ qualityLine }}</span>
        <span v-if="reasonLine" class="play-reason">{{ reasonLine }}</span>
        <span v-if="sessStatus" class="sess-status">{{ sessStatus }}</span>
        <span class="pd-spacer"></span>
        <div class="pd-setwrap">
          <button class="pd-mini" :class="{ on: settingsOpen }" @click="toggleSettings"
            title="播放设置（画质/音轨/字幕/延迟）">⚙ 设置</button>
          <div v-if="settingsOpen" class="pd-set" @click.stop>
            <div class="set-row">
              <label>画质</label>
              <select v-model="quality" @change="onQualityChange" title="自动=按服务器能力；原画=不封顶重编（耗 CPU）">
                <option value="auto">自动（推荐）</option>
                <option value="source">原画</option>
                <option value="1080p">1080p</option>
                <option value="720p">720p</option>
              </select>
            </div>
            <div class="set-row" v-if="audios.length > 1">
              <label>音轨</label>
              <select v-model.number="audioIdx" @change="onAudioChange">
                <option v-for="(a, i) in audios" :key="i" :value="i">{{ audioLabel(a, i) }}</option>
              </select>
            </div>
            <div class="set-row" v-if="subs.length">
              <label>字幕</label>
              <select v-model.number="subIdx" @change="onSubChange">
                <option :value="-1">无字幕</option>
                <option v-for="(s, i) in subs" :key="i" :value="i">
                  {{ subLabel(s, i) }}{{ subBadge(s) }}
                </option>
              </select>
            </div>
            <div class="set-row" v-if="subDelayVisible">
              <label>延迟</label>
              <span class="set-inline">
                <button class="ctl-mini" @click="shiftSubDelay(-0.5)">−0.5</button>
                <span class="delay-val">{{ subDelayText }}</span>
                <button class="ctl-mini" @click="shiftSubDelay(0.5)">+0.5</button>
              </span>
            </div>
            <div class="set-row" v-if="subIsAss">
              <label>兼容</label>
              <label class="ctl-compat" title="ASS 渲染异常/缺字体时使用：改用简化 VTT 字幕">
                <input type="checkbox" v-model="compatSub" @change="onCompatChange" />VTT 字幕（丢样式）
              </label>
            </div>
            <p class="set-hint">{{ methodLine }}<span v-if="qualityLine"> · {{ qualityLine }}</span></p>
          </div>
        </div>
        <button class="pd-mini" @click="copyDebug">调试</button>
        <button class="pd-mini" @click="$emit('close')">关闭</button>
      </div>
      <div v-if="resumeOffer" class="resume-bar">
        <span>上次看到 {{ resumeOffer }}</span>
        <button @click="resumePlay">继续播放</button>
        <button @click="restartPlay">从头开始</button>
      </div>
      <!-- 播放容器：全屏目标；内含视频/冻结帧/提示/顶部标题/底部控件 -->
      <div ref="pvWrapEl" class="pv-wrap has-bar"
        :class="{ 'hide-cursor': isFull && !overlayVisible }"
        :style="videoPadding ? { paddingTop: videoPadding } : {}"
        @mousemove="onMouseMove" @dblclick="toggleFull">
        <video ref="videoEl" :key="videoKey" autoplay playsinline preload="metadata" class="player-video"
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
        <!-- 自绘控件条（HLS 与原文件直发统一使用：直发不再用原生控件遮挡画面） -->
        <div class="pv-ctl" :class="{ show: overlayVisible }" @dblclick.stop>
          <button @click="togglePlay" :title="isPlaying ? '暂停（空格）' : '播放（空格）'">{{ isPlaying ? '⏸' : '▶' }}</button>
          <span class="ctl-time">{{ fmt(seekDragging ? seekPreview : seekPos) }} / {{ fmt(decidedDuration) }}</span>
          <input type="range" min="0" :max="Math.floor(decidedDuration)" step="1"
            :value="seekDragging ? seekPreview : Math.floor(seekPos)"
            :disabled="seekPending || !(decidedDuration > 0)"
            @input="onSeekInput" @change="onSeekCommit" class="ctl-seek" />
          <button @click="toggleMute" :title="muted ? '取消静音' : '静音'">{{ muted ? '🔇' : '🔊' }}</button>
          <input type="range" min="0" max="100" :value="muted ? 0 : volume * 100"
            @input="setVolume" @change="blurPick" class="ctl-vol" />
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
import { getCaps, probeStrings, withProbes } from '../caps.js'
import { ensureJassub } from '../jassubLoader.js'
import { ensurePgs } from '../pgsLoader.js'
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
const quality = ref('auto')
const audioIdx = ref(0)
const subIdx = ref(-1)
const audios = ref([])
const subs = ref([])
// ASS 渲染（JASSUB）：实例 + 归属键（版本:视频元素:轨:兼容标记，变更才重建）
let jassub = null
let assKey = ''
const assFonts = ref(-1)
// PGS 图片字幕渲染（libpgs）：解码失败自动降级烧录（forceBurn 进 decide/sessions）
let pgs = null
let pgsCanvas = null
let pgsKey = ''
const forceBurn = ref(false)
// 字幕时间偏移（秒）：ASS(JASSUB)/PGS(libpgs) 的 timeOffset；按版本记忆
const subDelay = ref(0)
// 外挂中文默认轨只自动选一次（用户手动选过后不再自动覆盖）
let autoSubPicked = false
// 缺字体时自动转 VTT 的字幕轨索引（按轨，不污染其他 ASS 轨；重开播放器即重置）
const autoVttSub = ref(-1)
// 设置弹层（画质/音轨/字幕/延迟）：日常只留一个按钮，避免控件条拥挤/遮挡画面
const settingsOpen = ref(false)
function toggleSettings() { settingsOpen.value = !settingsOpen.value }
function onDocClick(e) {
  if (!settingsOpen.value) return
  const t = e && e.target
  if (t && t.closest && t.closest('.pd-setwrap')) return
  settingsOpen.value = false
}
// 「兼容字幕(VTT)」：ASS 样式渲染异常/无字体时的降级；跨会话记住选择
const compatSub = ref((() => {
  try { return localStorage.getItem('jzmedia.subCompat') === '1' } catch (e) { return false }
})())
const method = ref('')
const reasons = ref([])
const sessStatus = ref('')
let sessionId = null
let pingTimer = 0
// 客户端能力：基础矩阵一次；逐片候选码串实测结果按版本缓存（decide/sessions 带 caps）
let activeCaps = null
const probedCaps = {}
const err = ref('')
const posHint = ref('')
const resumeOffer = ref('')
let resumePos = 0
// 最近一次会话的起始秒（resumePlay 对不支持 #t 的浏览器补跳用；reload 消费 resumePos 后清零）
let lastStartAt = 0
// “从头开始”：显式以 0 起，跳过 reload 的“保持当前位置”捕获
let startFromZero = false
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
// reload 代际：并发 reload（起播等待中切音轨/快速切档）只允许最后一轮挂载，
// 否则两轮各自 new Hls 互踩 → 实际播放的实例与 UI/会话错位（切轨无效的根因）。
let reloadGen = 0
// HLS master 是否已解析（MANIFEST_PARSED）：解析前的切轨请求交给解析后 applyAudioTrack
let manifestReady = false
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
const isHls = computed(() => ['remux', 'audio_transcode', 'transcode', 'video_transcode']
  .includes(method.value))
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
  return {
    direct: 'Direct Play（原文件直发，零转码）',
    remux: 'Direct Stream（仅换容器，零画质损失）',
    audio_transcode: 'Direct Stream（仅音频转码，视频原样）',
    video_transcode: '视频转码中（按所选画质重编）',
    transcode: '转码中（按所选画质重编）',
  }[method.value] || method.value
})
// 实际输出（不再只显示所选档位）：plan.height 为服务端真正落地的封顶高度
const planHeight = ref(0)
const srcHeight = ref(0)
const qualityLine = computed(() => {
  if (!method.value) return ''
  const h = Number(planHeight.value) || 0
  const src = Number(srcHeight.value) || 0
  const dim = src ? `${src}p` : ''
  if (method.value === 'video_transcode') {
    if (h > 0) {
      const auto = (reasons.value || []).some(r =>
        r === 'auto_downscale_720p' || r === 'auto_downscale_1080p')
      return `实际输出 ${h}p${auto ? '（自动封顶）' : ''}`
    }
    return dim ? `原分辨率 ${dim} 重编` : '原分辨率重编'
  }
  return dim ? `原分辨率 ${dim} 直通` : '原分辨率直通'
})
const REASON_TEXT = {
  dovi_not_supported: '含杜比视界（浏览器无 DV 解码，已重编；原盘 DV 请用电视/Kodi 看）',
  video_codec_not_supported: '视频编码浏览器不支持，已重编为 H264',
  video_bit_depth_not_supported: '10bit 视频浏览器不能直解，已重编为 H264 8bit',
  hdr_not_supported: 'HDR 片源本屏/浏览器不支持，已转 SDR（色彩可能偏灰）',
  audio_codec_not_supported: '音频编码浏览器不支持，已单独转 AAC（视频不重编）',
  container_not_supported: '容器不对，已无损换为浏览器兼容容器',
  resolution_downscale: '已按所选画质降档（省 CPU）',
  pgs_needs_burn: '图片字幕（PGS/VobSub）已烧录进画面（较耗 CPU，切换字幕或原画需重转码）',
  auto_downscale_720p: '已自动封顶 720p（未检测到硬件转码，4K 软转太重；可选“原画”强制原分辨率，更耗 CPU）',
  auto_downscale_1080p: '已自动封顶 1080p（硬件转码）',
  source_transcode: '已按原画原分辨率重编（CPU 占用高，可能卡顿）',
  audio_track_selection: '所选音轨需要走转封装（原文件直发只能播默认音轨）',
  subtitle_not_found: '所选字幕不可用',
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
// 应用所选音轨（fMP4 rendition）：hls.audioTracks 顺序 = 服务端产出顺序（见 playback.audio_variants）
function applyAudioTrack() {
  if (!hls || !hls.audioTracks || !hls.audioTracks.length) {
    logEvt('hls:audio-skip', 'tracks=' + (hls && hls.audioTracks ? hls.audioTracks.length : -1) +
      ' want=' + (Number(audioIdx.value) || 0))
    return false
  }
  const i = Number(audioIdx.value) || 0
  if (i >= 0 && i < hls.audioTracks.length) {
    if (hls.audioTrack !== i) {
      try { hls.audioTrack = i; logEvt('hls:audioTrack', i) } catch (e) { /* 忽略 */ }
    }
    return true
  }
  return false
}
// 原生 HLS（Safari）音轨切换：video.audioTracks[k].enabled；不支持则返回 false
function applyNativeAudioTrack(v) {
  const tv = v || videoEl.value
  if (engine !== 'native' || !tv || !tv.audioTracks || !tv.audioTracks.length) return false
  const i = Number(audioIdx.value) || 0
  for (let k = 0; k < tv.audioTracks.length; k++) {
    try { tv.audioTracks[k].enabled = (k === i) } catch (e) { /* 忽略 */ }
  }
  return i < tv.audioTracks.length
}
// hls 挂载唯一入口（起播/自救共用）：接事件遥测，MEDIA_ATTACHED 后回到目标片内时间。
// opts.fromStart：新会话必须从片内 0 起播——增长型 live 列表 hls.js 默认从“直播边缘”
// 起（copy 档 ffmpeg 会抢跑到很后面，用户 seek 到 13:20 实际从 30:50 播）；会话的绝对
// 起点已由 startOffset 记录，片内 0 = 会话起点。自救保持默认（回到当前直播边缘附近）。
async function mountHls(v, url, targetMediaTime, opts) {
  let Hls = null
  try { Hls = await ensureHls() } catch (e) { Hls = null }
  if (!Hls || !Hls.isSupported()) {
    sessStatus.value = ''
    err.value = '当前浏览器不支持 HLS，请用 Chrome/Edge/Safari'
    return false
  }
  destroyHls()
  manifestReady = false
  const cfg = { maxBufferLength: 30 }
  if (opts && opts.fromStart) cfg.startPosition = 0
  hls = new Hls(cfg)
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
  hls.on(Hls.Events.MANIFEST_PARSED, () => {
    manifestReady = true
    logEvt('hls:manifest', 'tracks=' + ((hls && hls.audioTracks) ? hls.audioTracks.length : -1) +
      ' want=' + (Number(audioIdx.value) || 0))
    applyAudioTrack()   // 应用期间/已选音轨（含起播等待期用户先切好的选择）
  })
  // MANIFEST_PARSED 时 audioTracks 可能还是 0（控制器稍后才填充）：真正就绪在
  // AUDIO_TRACKS_UPDATED，必须在这里补应用一次，否则起播等待期的切轨选择丢失。
  hls.on(Hls.Events.AUDIO_TRACKS_UPDATED, () => { applyAudioTrack() })
  hls.on(Hls.Events.AUDIO_TRACK_SWITCHED, (_ev, data) => {
    try { logEvt('hls:AUDIO_TRACK_SWITCHED', String(((data || {}).id ?? ''))) } catch (e) { /* 忽略 */ }
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
// 播放决策：POST 带客户端实测 caps（目标文档 §4）；逐片候选码串先实测再复判一次
// （每个版本只实测一次），避免“粗判 HEVC 可播但该片 Main10 超档”之类误判。
async function decidePlayback(subArg) {
  const base = activeCaps || probedCaps[props.versionId] || await getCaps()
  const post = (caps) => api(`/api/stream/${props.versionId}/decide`, {
    method: 'POST',
    body: JSON.stringify({ quality: quality.value, audio: audioIdx.value,
                           sub: subArg, client: 'web', caps,
                           force_burn: forceBurn.value }),
  })
  const d = await post(base)
  const media = d.media || {}
  const strs = [...(media.vcaps || []),
    ...((media.audio || []).flatMap(a => a.caps || []))]
  if (!strs.length || probedCaps[props.versionId]) {
    activeCaps = probedCaps[props.versionId] || base
    return d
  }
  try {
    const probes = await probeStrings(strs, { w: media.width, h: media.height })
    probedCaps[props.versionId] = withProbes(base, probes)
    activeCaps = probedCaps[props.versionId]
    return await post(activeCaps)
  } catch (e) {
    activeCaps = base
    return d
  }
}
async function reload() {
  const gen = ++reloadGen
  err.value = ''
  sessStatus.value = ''
  needGesture.value = false
  stallTicks = 0
  lastTickPos = -1
  lastPlaylistUrl = ''
  lastHlsError.value = ''
  // 切画质/音轨/字幕烧录时保持当前播放位置：
  // 仅“非显式起播”（seek/续播/从头开始已预设目标）才用实时位置；用后即消耗 resumePos，
  // 否则残留的旧目标会让切档跳回上次 seek 点。
  const fromZero = startFromZero
  startFromZero = false
  if (!seekPending.value && !resumePos && !fromZero) {
    try {
      const cur = Math.floor(absPos())
      if (cur > 5) resumePos = cur
    } catch (e) { /* 忽略 */ }
  }
  // 换会话前抓一帧冻结画面：窗口不塌、无图像窗口不再出现
  freezeFrame.value = captureFrame()
  await closeSession()
  destroyHls()
  manifestReady = false   // 旧 master 已失效，新挂载解析前不再认为可切 rendition
  const v = videoEl.value
  if (v) { try { v.pause() } catch (e) { /* 忽略 */ } v.removeAttribute('src'); v.load() }
  let d
  // 图片字幕：默认客户端渲染（PGS→libpgs）；VobSub/解码降级走烧录（服务端 subtitle_mode）
  const wantBurn = imageSubSelected()
  const burnSub = wantBurn ? Number(subIdx.value) : -1
  try {
    d = await decidePlayback(burnSub >= 0 ? burnSub : (subIdx.value >= 0 ? subIdx.value : null))
  } catch (e) {
    err.value = '无法播放：' + e.message
    return
  }
  if (gen !== reloadGen) return   // 新一轮 reload 已接管，放弃本轮（防两个 hls 实例互踩）
  burnOn = d.subtitle_mode ? d.subtitle_mode === 'burn' : wantBurn
  method.value = d.method
  reasons.value = d.reasons || []
  audios.value = d.media?.audio || []
  subs.value = d.media?.subs || []
  // 外挂中文默认轨：打开时自动选一次（用户手动选过后不再覆盖；图片外挂不自动选）
  if (subIdx.value === -1 && !autoSubPicked) {
    const di = (subs.value || []).findIndex(s => s && s.source === 'sidecar'
      && !s.image && Number(s.default) === 1)
    if (di >= 0) { subIdx.value = di; autoSubPicked = true }
  }
  planHeight.value = Number(d.plan?.height) || 0
  srcHeight.value = Number(d.media?.height) || 0
  // 播放器比例：padding-top = min(片源高宽比, 76vh)（16:9 片源按屏幕宽度自适应）；
  // HLS 模式底部额外预留控件条高度，视频区不被遮挡。无数据时容器也不塌陷。
  try {
    const w = Number(d.media?.width) || 0
    const h = Number(d.media?.height) || 0
    const pct = (w > 0 && h > 0)
      ? (Math.round((h / w) * 10000) / 100) + '%'
      : '56.25%'
    const box = 'min(' + pct + ', 68vh)'
    // 统一为底部控件条预留 46px（HLS 与原文件直发一致），避免控件压住画面/字幕
    videoPadding.value = 'calc(' + box + ' + 46px)'
  } catch (e) { videoPadding.value = 'calc(min(56.25%, 68vh) + 46px)' }
  try {
    decidedDuration.value = Number(d.media?.duration) || 0
    applySubs()
  const startAt = fromZero ? 0 : (resumePos || 0)
  resumePos = 0
  lastStartAt = Math.floor(startAt)
  startOffset.value = lastStartAt
  mediaErrLogged = ''
  if (d.method === 'direct') {
    engine = 'direct'
    logEvt('engine:direct', '')
    v.src = encodeURI(d.direct_url) + (startAt > 0 ? `#t=${Math.floor(startAt)}` : '')
    tryPlay()
  } else {
    // 渐进式会话：服务端前 3 分片就绪即回，首画面不等整片
    sessStatus.value = (d.method === 'remux' || d.method === 'audio_transcode')
      ? '正在换封装…' : '正在转码（前分片生成中，稍候即播）…'
    let s
    try {
      s = await api(`/api/stream/${props.versionId}/sessions`, {
        method: 'POST',
        // 建会话要等前 3 分片（弱 CPU 转码慢），放宽到 300s，对齐服务端 deadline
        timeout: 300000,
        body: JSON.stringify({ quality: quality.value, audio: audioIdx.value,
                               start: Math.floor(startAt),
                               sub: burnSub >= 0 ? burnSub : null,
                               caps: activeCaps,
                               force_burn: forceBurn.value }),
      })
    } catch (e) {
      sessStatus.value = ''
      err.value = '无法播放：' + e.message
      return
    }
    if (gen !== reloadGen) return   // 新一轮 reload 已接管（其会杀/复用本会话），别挂旧列表
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
      // 原生 HLS 对增长型 live 同样默认从直播边缘起：新会话强制回到片内 0；
      // 音轨选择用 video.audioTracks 应用（Safari 有；没有则维持默认轨）
      const onMeta = () => {
        try { v.currentTime = 0 } catch (e) { /* 忽略 */ }
        applyNativeAudioTrack(v)
      }
      v.addEventListener('loadedmetadata', onMeta, { once: true })
      tryPlay()
    } else {
      engine = 'hls'
      logEvt('engine:hls', '')
      await mountHls(v, url, null, { fromStart: true })
    }
    }
  } catch (e) {
    // 兜底：起播链路任何意外都不再静默 0:00，直接显示人话错误
    sessStatus.value = ''
    err.value = '播放失败：' + (e && e.message ? e.message : e)
  }
}
// 字幕渲染分层：none / vtt（浏览器 <track>）/ ass（JASSUB）/ pgs（libpgs）/ burn（VobSub 烧录）
function subKind(s) {
  if (!s) return 'none'
  if (s.image) return String(s.codec || '').toLowerCase() === 'pgs' ? 'pgs' : 'burn'
  const c = String(s.codec || '').toLowerCase()
  return (c === 'ass' || c === 'ssa') ? 'ass' : 'vtt'
}
function destroyAss() {
  const inst = jassub
  jassub = null
  assKey = ''
  if (inst) { try { inst.destroy() } catch (e) { /* 忽略 */ } }
}
function destroyPgs() {
  const inst = pgs
  pgs = null
  pgsKey = ''
  if (inst) { try { inst.dispose() } catch (e) { /* 忽略 */ } }
  // 自建 canvas（libpgs 拥有时只解除引用，不删元素；我们统一切断引用后移除）
  if (pgsCanvas) {
    try { pgsCanvas.remove() } catch (e) { /* 忽略 */ }
    pgsCanvas = null
  }
}
// PGS 画布：显式传入并对齐「画面区」（不含底部控件条），避免字幕坐标落进控件条被遮挡。
// 注意：canvas 是 JS 动态创建，Vue scoped 样式不生效 → 内联样式（bottom 用 --pvb，全屏时归零）。
function ensurePgsCanvas(v) {
  if (pgsCanvas && pgsCanvas.parentNode === v.parentNode) return pgsCanvas
  pgsCanvas = document.createElement('canvas')
  const st = pgsCanvas.style
  st.position = 'absolute'
  st.top = '0'
  st.left = '0'
  // 高度必须减去控件条（放 bottom 会被 height:100% 覆盖；100% 是 wrap 的 padding box）
  st.width = '100%'
  st.height = 'calc(100% - var(--pvb))'
  st.pointerEvents = 'none'
  st.objectFit = 'contain'
  v.insertAdjacentElement('afterend', pgsCanvas)
  return pgsCanvas
}
// PGS 客户端解码不可用（库加载失败/解码异常）→ 自动降级烧录（重开会话并提示）
function fallbackBurnSub(msg) {
  if (forceBurn.value) return
  forceBurn.value = true
  posHint.value = (msg || 'PGS 客户端渲染不可用') + '，已自动切换为烧录模式（较耗 CPU）'
  logEvt('pgs:fallback-burn', String(msg || '').slice(0, 120))
  reload()
}
async function mountPgs(v, key) {
  let mod = null
  try {
    mod = await ensurePgs()
  } catch (e) {
    fallbackBurnSub('PGS 渲染组件加载失败')
    return
  }
  if (videoEl.value !== v || burnOn || subKind(subs.value[subIdx.value]) !== 'pgs') return
  destroyPgs()
  try {
    const inst = new mod.PgsRenderer({
      video: v,
      canvas: ensurePgsCanvas(v),
      subUrl: `/api/stream/${props.versionId}/sub/${subIdx.value}.sup`,
      workerUrl: mod.workerUrl,
      timeOffset: subDelay.value,
      aspectRatio: 'contain',   // 与 video object-fit 一致
    })
    pgs = inst
    pgsKey = key
    // worker 加载失败可能既不 resolve 也不 reject（事件被吞）→ 超时兜底降级烧录
    let settled = false
    const timer = setTimeout(() => {
      if (!settled && pgs === inst) fallbackBurnSub('PGS 渲染超时')
    }, 20000)
    Promise.resolve(inst.ready)
      .then(() => { settled = true; clearTimeout(timer); logEvt('pgs:ready', 'delay=' + subDelay.value) })
      .catch((e) => {
        settled = true
        clearTimeout(timer)
        if (pgs === inst) fallbackBurnSub('PGS 解码失败：' + String(e).slice(0, 80))
      })
  } catch (e) {
    fallbackBurnSub('PGS 渲染初始化失败')
  }
}
// ASS 是否含中日韩文本（无字体时判断是否需要降级 VTT；只看前 40KB 足够覆盖样式/首批对白）
async function assNeedsCjk(url) {
  try {
    const r = await fetch(url, { cache: 'no-store' })
    const t = (await r.text()).slice(0, 40000)
    return /[\u2E80-\u9FFF\uF900-\uFAFF\u3400-\u4DBF\uAC00-\uD7AF]/.test(t)
  } catch (e) { return false }
}
async function mountAss(v, key) {
  let mod = null
  let meta = { fonts: [] }
  try {
    [mod, meta] = await Promise.all([
      ensureJassub(),
      api(`/api/stream/${props.versionId}/fonts`).catch(() => ({ fonts: [] })),
    ])
  } catch (e) {
    posHint.value = 'ASS 渲染组件加载失败，可勾选「兼容」改用 VTT 字幕'
    return
  }
  if (videoEl.value !== v || burnOn || subKind(subs.value[subIdx.value]) !== 'ass') return
  const fonts = (meta.fonts || []).map(f => f.url)
  // 无任何可用字体 + 含中日韩文本：libass 缺字形会显示不全，
  // 自动降级浏览器 VTT（系统字体渲染，保证可读；投放字体到 data/fonts/ 即恢复 ASS 样式）
  if (!fonts.length) {
    const needCjk = await assNeedsCjk(`/api/stream/${props.versionId}/sub/${subIdx.value}.ass`)
    if (videoEl.value !== v || burnOn || subKind(subs.value[subIdx.value]) !== 'ass') return
    if (needCjk) {
      autoVttSub.value = Number(subIdx.value)
      posHint.value = '未找到中文字体：已用浏览器 VTT 显示；把任意中文字体（woff2/ttf/ttc）放入 data/fonts/ 可恢复 ASS 样式'
      logEvt('ass:no-font-vtt', 'sub=' + subIdx.value)
      applySubs(true)
      return
    }
  }
  assFonts.value = fonts.length
  destroyAss()
  try {
    jassub = new mod.JASSUB({
      video: v,
      subUrl: `/api/stream/${props.versionId}/sub/${subIdx.value}.ass`,
      fonts,
      workerUrl: mod.workerUrl,
      wasmUrl: mod.wasmUrl,
      modernWasmUrl: mod.modernWasmUrl,
      timeOffset: subDelay.value,
      // ASS 里指定字体缺失时用系统/内置兜底；无字体也不崩（libass 用内置 Liberation Sans）
      defaultFont: 'Liberation Sans',
    })
    assKey = key
    posHint.value = fonts.length
      ? `ASS 字幕（样式渲染，${fonts.length} 个可用字体）`
      : 'ASS 字幕：未找到内嵌/内置字体，文字可能走默认字体；异常可勾选「兼容」或投放字体到 data/fonts/'
    Promise.resolve(jassub.ready)
      .then(() => logEvt('ass:ready', 'fonts=' + fonts.length))
      .catch((e) => { posHint.value = 'ASS 渲染初始化失败，可勾选「兼容」改用 VTT 字幕'; logEvt('ass:error', String(e).slice(0, 160)) })
  } catch (e) {
    posHint.value = 'ASS 渲染初始化失败，可勾选「兼容」改用 VTT 字幕'
    logEvt('ass:init-error', String(e).slice(0, 160))
  }
}
// 应用当前所选字幕（会话重载/换视频元素/切轨共用）：先拆旧层再按类型装新层。
// 文本/ASS/PGS 全部客户端渲染——切字幕不重开会话、不转码；仅 VobSub（burn）走烧录。
async function applySubs(force) {
  const v = videoEl.value
  if (!v) return
  v.querySelectorAll('track').forEach(t => t.remove())
  const kind = burnOn ? 'burn' : subKind(subs.value[subIdx.value])
  const key = `${props.versionId}:${videoKey.value}:${subIdx.value}:${compatSub.value ? 'v' : 'a'}`
  if (jassub && (force || kind !== 'ass' || assKey !== key)) destroyAss()
  if (pgs && (force || kind !== 'pgs' || pgsKey !== key)) destroyPgs()
  if (kind === 'none' || kind === 'burn') return
  if (kind === 'vtt' || compatSub.value
      || (kind === 'ass' && autoVttSub.value === Number(subIdx.value))) {
    const tr = document.createElement('track')
    tr.kind = 'subtitles'
    tr.src = `/api/stream/${props.versionId}/sub/${subIdx.value}.vtt`
    tr.default = true
    v.appendChild(tr)
    return
  }
  if (kind === 'ass') {
    if (jassub && assKey === key) return
    await mountAss(v, key)
    return
  }
  if (kind === 'pgs') {
    if (pgs && pgsKey === key) return
    await mountPgs(v, key)
  }
}
// 所选字幕是否为图片型（需烧录；实际是否烧录以服务端 subtitle_mode 为准）
function imageSubSelected() {
  const s = subs.value[subIdx.value]
  return !!(s && s.image)
}
// 当前所选是否为 ASS/SSA（决定「兼容」开关是否显示）
const subIsAss = computed(() => subKind(subs.value[subIdx.value]) === 'ass')
// ASS/PGS 支持 timeOffset（延迟控件可见）
const subDelayVisible = computed(() =>
  ['ass', 'pgs'].includes(subKind(subs.value[subIdx.value])) && !compatSub.value && !burnOn
  && autoVttSub.value !== Number(subIdx.value))
const subDelayText = computed(() => (subDelay.value > 0 ? '+' : '') + subDelay.value.toFixed(1) + 's')
function shiftSubDelay(d) {
  const x = Math.round(Math.max(-10, Math.min(10, subDelay.value + d)) * 10) / 10
  subDelay.value = x
  try { localStorage.setItem('jzmedia.subDelay.' + props.versionId, String(x)) } catch (e) { /* 忽略 */ }
  if (jassub) { try { jassub.timeOffset = x } catch (e) { /* 忽略 */ } }
  if (pgs) { try { pgs.timeOffset = x } catch (e) { /* 忽略 */ } }
}
// 字幕下拉角标：烧录/PGS/ASS 样式 + 外挂来源
function subBadge(s) {
  const kind = subKind(s)
  const parts = []
  if (kind === 'burn') parts.push('烧录')
  else if (kind === 'pgs') parts.push('PGS')
  else if (kind === 'ass') parts.push('ASS 样式')
  if (s && s.source === 'sidecar') parts.push('外挂')
  return parts.length ? '（' + parts.join('·') + '）' : ''
}
// 选完即失焦：否则焦点停在下拉框，方向键会去改选项而不是 seek/音量
function blurPick(e) {
  try {
    const el = e && e.target
    if (el && el.blur) el.blur()
  } catch (err) { /* 忽略 */ }
}
function onQualityChange(e) { blurPick(e); reload() }
// 音轨切换：fMP4 rendition 已在会话里 → 切 hls.audioTrack 即刻生效（视频不重编不重开）；
// 原生 Safari 用 video.audioTracks；会话尚未就绪时只改选择，等 MANIFEST_PARSED 应用；
// 都不支持（TS 回滚单轨产物）才回退重开会话。
function onAudioChange(e) {
  blurPick(e)
  logEvt('audio-change', 'raw=' + String((e && e.target && e.target.value) ?? '') +
    ' ref=' + (Number(audioIdx.value) || 0) + ' ready=' + manifestReady)
  if (applyAudioTrack()) return
  if (applyNativeAudioTrack()) return
  if (isHls.value && !manifestReady) return
  reload()
}
// 字幕切换：文本/ASS/PGS 都是客户端渲染层 → 即时切换不重开会话；
// VobSub（burn）或已处于烧录模式（forceBurn 降级）才重开转码
function onSubChange(e) {
  blurPick(e)
  autoSubPicked = true   // 用户手动选过字幕，不再自动选外挂默认轨
  if (burnOn || subKind(subs.value[subIdx.value]) === 'burn') { reload(); return }
  applySubs(true)
}
// 「兼容字幕(VTT)」：ASS 样式渲染异常/无字体时的降级开关
function onCompatChange() {
  try { localStorage.setItem('jzmedia.subCompat', compatSub.value ? '1' : '0') } catch (e) { /* 忽略 */ }
  applySubs(true)
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
  // 松手：提交目标秒数 → 关旧会话开新会话；顺带失焦，让方向键回到快捷键
  const raw = Number((e.target || {}).value)
  const t = seekDragging.value ? seekPreview.value
    : Math.max(0, Math.floor(Number.isFinite(raw) ? raw : seekPos.value))
  seekDragging.value = false
  blurPick(e)
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
  if (v && method.value === 'direct' && lastStartAt > 0) {
    try { v.currentTime = lastStartAt } catch (e) { /* 忽略 */ }
  }
}
async function restartPlay() {
  resumeOffer.value = ''
  resumePos = 0
  startFromZero = true
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
  if (e.key === 'Escape' && settingsOpen.value) {   // 先关设置弹层，再谈退出全屏/关播
    e.preventDefault()
    settingsOpen.value = false
    return
  }
  if (e.code === 'Space' && !typing) {
    e.preventDefault()   // 直发也走自绘控件：空格统一播放/暂停
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
  // 字幕延迟按版本记忆（ASS/PGS 客户端渲染的 timeOffset）
  try {
    const saved = Number(localStorage.getItem('jzmedia.subDelay.' + props.versionId))
    if (Number.isFinite(saved)) subDelay.value = Math.round(saved * 10) / 10
  } catch (e) { /* 忽略 */ }
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
  document.addEventListener('click', onDocClick)
  window.addEventListener('beforeunload', saveNow)
  window.addEventListener('keydown', onKeydown)
  try {
    window.__jzPlayerDebug = () => debugSnapshot()
    window.__jzHls = () => hls  // 调试口：DevTools 里查 hls.audioTracks / currentLevel
    window.__jzAss = () => jassub
    window.__jzPgs = () => pgs
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
      try {
        const ats = hls.audioTracks || []
        info.hls.audio = {
          tracks: ats.map(t => `${t.name || ''}|${t.lang || ''}|${t.default ? 'D' : ''}`),
          current: hls.audioTrack,
        }
      } catch (e) { /* 忽略 */ }
      const lv = (((hls.levels || [])[hls.currentLevel] || {}).details) || null
      if (lv) {
        info.hls.level = { live: !!lv.live, frags: (lv.fragments || []).length,
          total: Math.round(lv.totalduration || 0), target: lv.targetduration,
          endSN: lv.endSN ?? null }
      }
    }
  } catch (e) { /* 忽略 */ }
  info.ass = { active: !!jassub, fonts: assFonts.value, compat: compatSub.value,
    kind: subKind(subs.value[subIdx.value]) }
  info.pgs = { active: !!pgs, delay: subDelay.value, force_burn: forceBurn.value }
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
      // 换过 <video> 元素：JASSUB/libpgs 的 canvas 挂在旧元素后面，必须重建
      destroyAss()
      destroyPgs()
      await mountHls(nv, lastPlaylistUrl, Math.max(0, target - 0.5))
      applySubs(true)
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
  document.removeEventListener('click', onDocClick)
  window.removeEventListener('beforeunload', saveNow)
  window.removeEventListener('keydown', onKeydown)
  if (hideTimer) clearTimeout(hideTimer)
  if (bufTimer) clearInterval(bufTimer)
  if (saveTimer) clearTimeout(saveTimer)
  destroyHls()
  destroyAss()
  destroyPgs()
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
.pd-mini.on { background: #2b6cb0; border-color: #2b6cb0; color: #fff; }

/* 设置弹层：日常只留「⚙ 设置」按钮，画质/音轨/字幕/延迟都收进来 */
.pd-setwrap { position: relative; display: inline-flex; }
.pd-set { position: absolute; right: 0; top: calc(100% + 6px); z-index: 20; width: min(360px, 78vw);
  background: #1d1d1d; border: 1px solid #3a3a3a; border-radius: 10px; padding: 10px 12px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, .5); display: flex; flex-direction: column; gap: 8px; }
.set-row { display: flex; align-items: center; gap: 8px; }
.set-row > label { color: #999; font-size: 0.75rem; width: 34px; flex: none; }
.set-row select { flex: 1; min-width: 0; background: #262626; color: #ddd; border: 1px solid #444; border-radius: 6px; padding: 4px 6px; font-size: 0.75rem; }
.set-inline { display: inline-flex; align-items: center; gap: 6px; }
.set-hint { margin: 2px 0 0; color: #777; font-size: 0.6875rem; }

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
.ctl-compat { display: inline-flex; align-items: center; gap: 3px; color: #aaa; font-size: 0.75rem; white-space: nowrap; cursor: pointer; }
.ctl-compat input { margin: 0; }
.ctl-mini { padding: 1px 6px !important; font-size: 0.75rem; line-height: 1.2; }
.delay-val { min-width: 34px; text-align: center; color: #7ed321; }
/* PGS 画布样式在 ensurePgsCanvas 内联设置（动态元素吃不到 scoped 样式） */

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
.play-quality { color: #7ed321; font-size: 0.75rem; }
.play-reason { color: #9ecfff; font-size: 0.75rem; }
.sess-status { color: #e0a63c; font-size: 0.75rem; }
.gesture-bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; background: #262626; border: 1px solid #2b6cb0; border-radius: 8px; padding: 8px 12px; margin: 4px 0 0; color: #9ecfff; font-size: 0.875rem; }
.play-now { font-size: 1rem; padding: 6px 22px; border-radius: 999px; background: #2b6cb0; border: 1px solid #2b6cb0; color: #fff; cursor: pointer; }
.resume-bar { display: flex; gap: 8px; align-items: center; color: #7ed321; font-size: 0.875rem; flex-wrap: wrap; }
.hint-line { margin: 0; color: #666; font-size: 0.8125rem; }
.hint.warn { color: #e0a63c; }
</style>
