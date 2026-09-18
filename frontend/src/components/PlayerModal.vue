<template>
  <div class="dlg-mask" @click.self="$emit('close')">
    <div ref="dlgRef" class="player-dlg" role="dialog" aria-modal="true">
      <div class="pd-head">
        <h3>{{ title || ('版本 ' + versionId) }}</h3>
        <span v-if="methodLine" class="play-method">{{ methodLine }}</span>
        <span v-if="qualityLine" class="play-quality">{{ qualityLine }}</span>
        <span v-if="reasonLine" class="play-reason">{{ reasonLine }}</span>
        <span v-if="sessStatus" class="sess-status">{{ sessStatus }}</span>
        <span class="pd-spacer"></span>
        <div id="pv-set-host-hd" ref="hdSetHost" class="pd-set-host">
          <PlayerSettings
            :host="setHost" :open="settingsOpen"
            :quality="quality" :audios="audios" :audio-idx="audioIdx"
            :subs="subs" :sub-idx="subIdx"
            :sub-delay-visible="subDelayVisible" :sub-delay-text="subDelayText"
            :sub-is-vtt="subIsVtt" :sub-is-ass="subIsAss" :force-burn="forceBurn"
            :undo-disabled="undoDegradeDisabled" :compat-sub="compatSub"
            :has-local-sub="localSubs.length > 0"
            :direct-fail-url="directFailUrl" :method-line="methodLine" :quality-line="qualityLine"
            :sub-style="subStyle"
            @toggle-settings="toggleSettings"
            @quality-change="onQualityChange"
            @audio-change="onAudioChange"
            @sub-change="onSubChange"
            @shift-delay="shiftSubDelay"
            @update:sub-style="onSubStyleSet"
            @undo-degrade="undoDegrade"
            @compat-change="onCompatSet"
            @load-sub-file="onLoadSubFile"
            @remove-local-subs="removeLocalSubs"
            @copy-direct="copyDirectLink" />
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
          <div id="pv-set-host-fs" ref="fsSetHost" class="pd-set-host"></div>
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
        <!-- 掉帧浮层询问（窗口/全屏统一，画面居中）：10s 无操作视为放弃（不变），
             点取消同样保持原画 -->
        <div v-if="dropHint" class="drop-prompt" @dblclick.stop>
          <span>检测到原画直通丢帧（30 秒 {{ dropDrops }} 帧），切换到 1080p 转码？</span>
          <button class="drop-go" @click="switchTo1080">切换</button>
          <button class="drop-cancel" @click="cancelDropPrompt">取消</button>
          <span class="drop-count">{{ dropCountdown }}s</span>
        </div>
      </div>
      <div v-if="needGesture" class="gesture-bar">
        <span>片源已就绪，浏览器阻止了自动带声播放</span>
        <button class="play-now" @click="userPlay">▶ 点击播放</button>
      </div>
      <p class="hint-line">{{ hintLine }}</p>
    </div>
  </div>
</template>
<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, onUnmounted, nextTick } from 'vue'
import { api } from '../api.js'
import { copyText } from '../clipboard.js'
import { getCaps, probeStrings, withProbes } from '../caps.js'
import { ensureJassub } from '../jassubLoader.js'
import { ensurePgs } from '../pgsLoader.js'
import { normalizeSubStyle, subFontPx, pickSubAnchor, subBarPad, subInnerPad } from '../subStyle.js'
import { createDropGuard, tickDropGuard, isCopyVideoPath } from '../dropGuard.js'
import Spinner from './Spinner.vue'
import { useFocusTrap } from '../useFocusTrap.js'
import PlayerSettings from './PlayerSettings.vue'
import { subKind, fmtTime as fmt } from '../playerLabels.js'
import { parseVtt, activeCues, pickDefaultSub, decodeSubtitleBytes, localSubCodec } from '../subtitleParse.js'
import { pickProgressPosition } from '../progress.js'
import '../player.css'
// hls.js 懒加载（~600KB）：只在进入播放器且非 Safari 时才下载，不拖首屏
let HlsCls = null
async function ensureHls() {
  if (!HlsCls) HlsCls = (await import('hls.js')).default
  return HlsCls
}

const props = defineProps({ versionId: { type: Number, required: true }, title: { type: String, default: '' },
  kind: { type: String, default: 'movie' } })
const isEpisode = computed(() => props.kind === 'episode')
const kindParam = computed(() => isEpisode.value ? '?kind=episode' : '')
const kindSuffix = computed(() => isEpisode.value ? '&kind=episode' : '')
const subDelayKey = computed(() => 'jzmedia.subDelay.' + (isEpisode.value ? 'ep.' : '') + props.versionId)
const emit = defineEmits(['close', 'watched'])

const dlgRef = ref(null)
const videoEl = ref(null)
let hls = null
useFocusTrap(ref(true), dlgRef)
let saveTimer = 0
let lastSave = 0
const quality = ref('auto')
const audioIdx = ref(0)
const subIdx = ref(-1)
const audios = ref([])
const subs = ref([])
// 临时加载的本地字幕（2026-09 用户需求）：浏览器端解析/渲染，不入库，关播放器即失效；
// 影片重载（切档/seek/音轨）后合并保留，选中项按 localId 恢复。
const localSubs = ref([])
let localSubSeq = 0
// ASS 渲染（JASSUB）：实例 + 归属键（版本:视频元素:轨:兼容标记，变更才重建）
let jassub = null
let assKey = ''
const assFonts = ref(-1)
// VTT 自绘字幕层：解析后的 cue（源时间轴 ms）+ 图层/循环句柄；代际防过期挂载
let vttCues = []
let vttLayer = null
let vttKey = ''
let vttSeq = 0
let vttLoopRvfc = 0
let vttLoopTimer = 0
let vttRO = null
let vttLastKey = ''
const vttNativeFallback = ref(false)
let vttAnchor = 'inside'   // 当前锚定：inside 画面内 / outside 下黑边
// 字幕外观/定位（全局记忆）：背景 0无/1半透明/2纯黑；描边 0无/1细/2粗；
// 位置 auto（黑边优先）/inside/outside；字号 1小/2中/3大。纯计算见 ../subStyle.js
const subStyle = ref(loadSubStyle())
function loadSubStyle() {
  try {
    return normalizeSubStyle(JSON.parse(localStorage.getItem('jzmedia.subStyle') || '{}'))
  } catch (e) { return normalizeSubStyle(null) }
}
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
// 掉帧看门狗：原画直通（视频 copy）且源 >1080p 时持续丢帧 → 提示手点降档。
// 不自动切、不落 localStorage（仅当次提示）。浮层询问（窗口/全屏统一，10s 超时=不变）。
let dropGuard = createDropGuard()
let dropPlayStart = 0
let dropHintFired = false
const dropHint = ref(false)
const dropDrops = ref(0)        // 触发窗口的丢帧数（提示文案）
const dropCountdown = ref(0)    // 浮层倒计时（秒）
let dropCountdownTimer = 0
function clearDropCountdown() {
  if (dropCountdownTimer) { clearInterval(dropCountdownTimer); dropCountdownTimer = 0 }
  dropCountdown.value = 0
}
function startDropCountdown() {
  clearDropCountdown()
  dropCountdown.value = 10
  dropCountdownTimer = setInterval(() => {
    if (dropCountdown.value <= 1) cancelDropPrompt()
    else dropCountdown.value -= 1
  }, 1000)
}
function cancelDropPrompt() {
  clearDropCountdown()
  dropHint.value = false
  posHint.value = '已保留原画（如仍卡顿可在「⚙ 设置」里切档）'
}
// 浮层出现即起倒计时（窗口/全屏统一）；消失/切档/卸载时清掉
watch(dropHint, (hint) => {
  if (hint) startDropCountdown()
  else clearDropCountdown()
})
const resumeOffer = ref('')
let resumePos = 0
// 续播条倒计时（用户 2026-09）：弹窗打开起 10s，未点「继续播放/从头开始」视为同意续播自动消条
let resumeTimer = 0
function clearResumeTimer() {
  if (resumeTimer) { clearTimeout(resumeTimer); resumeTimer = 0 }
}
// 最近一次会话的起始秒（resumePlay 对不支持 #t 的浏览器补跳用；reload 消费 resumePos 后清零）
let lastStartAt = 0
// “从头开始”：显式以 0 起，跳过 reload 的“保持当前位置”捕获
let startFromZero = false
const decidedDuration = ref(0)
let doneWatched = false
// HLS 绝对时间轴：会话按 start 开新流，片内 currentTime 从 0 起；显示/存档一律用 offset+片内
const startOffset = ref(0)
// 当前会话片内 0 对应的源时间（服务端 media_start；copy 会话=关键帧，转码=start）；
// direct=原文件时间轴，不需平移。客户端字幕（VTT/ASS/PGS）按它对齐播放进度。
let mediaStart = 0
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
// 全屏时控件/标题的显隐：鼠标活跃、暂停、seek 中、设置打开、有错误时常显
const overlayVisible = computed(() =>
  !isFull.value || mouseActive.value || seekPending.value || !isPlaying.value
  || settingsOpen.value || !!err.value)
// 设置弹层宿主：窗口模式在标题栏；全屏 Teleport 进全屏顶栏（随鼠标唤出，与进度条一致）。
// 用模板 ref（元素就绪后才有效）而不是选择器：Teleport 的字符串目标要求挂载前已存在。
const hdSetHost = ref(null)
const fsSetHost = ref(null)
const setHost = computed(() =>
  (isFull.value ? fsSetHost.value : hdSetHost.value) || hdSetHost.value || fsSetHost.value)
const isHls = computed(() => ['remux', 'audio_transcode', 'video_transcode']
  .includes(method.value))
const bufLine = computed(() => {
  if (!isHls.value) return ''
  const total = Number(decidedDuration.value) || 0
  let s = `已播 ${fmt(seekPos.value)}`
  if (total > 0) s += ` / 全片 ${fmt(total)}`
  if (bufSecs.value > 0) s += `（已缓冲 ${Math.floor(bufSecs.value)}s）`
  return s
})
// 状态行：缓冲进度 + 操作提示并存（只显示 bufLine 会吞掉「直链已复制」等反馈）
const hintLine = computed(() => [bufLine.value, posHint.value].filter(Boolean).join(' · '))
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
  }[method.value] || method.value
})
// 实际输出（不再只显示所选档位）：plan.height 为服务端真正落地的封顶高度
const planHeight = ref(0)
const directUrl = ref('')   // 原文件直链（设置弹层「复制直链」给 VLC/Kodi 用）
const directFailUrl = ref('')
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
  dovi_not_supported: '含杜比视界（无 HDR10 兼容基底，浏览器无法直通，已重编；原盘 DV 请用电视/Kodi）',
  video_codec_not_supported: '视频编码浏览器不支持，已重编为 H264',
  video_bit_depth_not_supported: '10bit 视频浏览器不能直解，已重编为 H264 8bit',
  hdr_not_supported: 'HDR 片源本屏/浏览器不支持，已转 SDR',
  hdr_no_tonemap: '当前转码后端不做 HDR 色调映射，色彩可能偏灰；建议用「复制直链」交给电视/Kodi',
  audio_codec_not_supported: '音频编码浏览器不支持，已单独转 AAC（视频不重编）',
  container_not_supported: '容器不对，已无损换为浏览器兼容容器',
  resolution_downscale: '已按所选画质降档（省 CPU）',
  pgs_needs_burn: '图片字幕（PGS/VobSub）已烧录进画面（较耗 CPU，切换字幕或原画需重转码）',
  vobsub_needs_burn: 'VobSub 图片字幕只能烧录（会重编视频）',
  dovi_no_base_tonemap: '杜比视界无 HDR10 兼容基底，转码色彩不可靠；建议「复制直链」交给电视/Kodi',
  auto_downscale_720p: '已自动封顶 720p（未检测到硬件转码，4K 软转太重；可选“原画”强制原分辨率，更耗 CPU）',
  auto_downscale_1080p: '已自动封顶 1080p（硬件转码）',
  source_transcode: '已按原画原分辨率重编（CPU 占用高，可能卡顿）',
  audio_track_selection: '所选音轨需要走转封装（原文件直发只能播默认音轨）',
  subtitle_burn_forced: '图片字幕客户端解码不可用，已自动改为烧录',
  subtitle_not_found: '所选字幕不可用',
}
const reasonLine = computed(() => (reasons.value || []).map(r => REASON_TEXT[r] || r).join('；'))
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
                           force_burn: forceBurn.value, kind: props.kind }),
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
  dropGuard = createDropGuard()
  dropPlayStart = 0
  dropHintFired = false
  dropHint.value = false
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
  // 本地临时字幕不在服务端轨清单里：decide/sessions 一律传 null（防 subtitle_not_found）
  const serverSub = burnSub >= 0 ? burnSub : (isLocalSub(selectedSub()) ? null : (subIdx.value >= 0 ? subIdx.value : null))
  try {
    d = await decidePlayback(serverSub)
  } catch (e) {
    err.value = '无法播放：' + e.message
    return
  }
  if (gen !== reloadGen) return   // 新一轮 reload 已接管，放弃本轮（防两个 hls 实例互踩）
  burnOn = d.subtitle_mode ? d.subtitle_mode === 'burn' : wantBurn
  method.value = d.method
  reasons.value = d.reasons || []
  audios.value = d.media?.audio || []
  mergeSubs(d.media?.subs || [])
  // 默认字幕：打开时自动选一次（用户手动选过后不再覆盖）；规则见 pickDefaultSub
  // （PGS 已客户端渲染 → 可自动选；VobSub 等烧录轨仍不自动选）
  if (subIdx.value === -1 && !autoSubPicked) {
    const di = pickDefaultSub(subs.value)
    if (di >= 0) { subIdx.value = di; autoSubPicked = true }
  }
  planHeight.value = Number(d.plan?.height) || 0
  directUrl.value = d.direct_url || ''
  directFailUrl.value = ''
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
  const startAt = fromZero ? 0 : (resumePos || 0)
  resumePos = 0
  lastStartAt = Math.floor(startAt)
  startOffset.value = lastStartAt
  mediaStart = d.method === 'direct' ? 0 : lastStartAt
  applySubs()
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
                               force_burn: forceBurn.value, kind: props.kind }),
      })
    } catch (e) {
      sessStatus.value = ''
      err.value = String(e.message || '').startsWith('429')
        ? '服务器转码通道已满（最多 2 路），请稍后重试或先关闭其他播放'
        : '无法播放：' + e.message
      return
    }
    if (gen !== reloadGen) return   // 新一轮 reload 已接管（其会杀/复用本会话），别挂旧列表
    sessionId = s.session_id
    method.value = s.method || d.method
    reasons.value = s.reasons || d.reasons || []
    // 服务端实际媒体起点（copy 会话=目标前关键帧，可能早于请求 start）：字幕按它平移
    const ms = Number(s.media_start)
    if (Number.isFinite(ms) && Math.abs(ms - startOffset.value) > 0.01) {
      startOffset.value = ms
      mediaStart = ms
      applySubs()
    }
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
      subUrl: `/api/stream/${props.versionId}/sub/${subIdx.value}.sup${kindParam.value}`,
      workerUrl: mod.workerUrl,
      timeOffset: subShift() + subDelay.value,
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
      .then(() => {
        settled = true
        clearTimeout(timer)
        if (pgs === inst) { try { inst.timeOffset = subShift() + subDelay.value } catch (e) { /* 忽略 */ } }
        logEvt('pgs:ready', 'delay=' + subDelay.value)
      })
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
    // 全文判定（评审 R13-D3）：前 40KB 多为样式段，正文中文会漏判；2MB 上限防极端文件
    const t = (await r.text()).slice(0, 2000000)
    return /[\u2E80-\u9FFF\uF900-\uFAFF\u3400-\u4DBF\uAC00-\uD7AF]/.test(t)
  } catch (e) { return false }
}
async function mountAss(v, key) {
  let mod = null
  let meta = { fonts: [] }
  try {
    [mod, meta] = await Promise.all([
      ensureJassub(),
      api(`/api/stream/${props.versionId}/fonts${kindParam.value}`).catch(() => ({ fonts: [] })),
    ])
  } catch (e) {
    posHint.value = 'ASS 渲染组件加载失败，可勾选「兼容」改用 VTT 字幕'
    return
  }
  if (videoEl.value !== v || burnOn || subKind(selectedSub()) !== 'ass') return
  const sub = selectedSub() || {}
  const assUrl = sub.url || `/api/stream/${props.versionId}/sub/${subIdx.value}.ass${kindParam.value}`
  const fonts = (meta.fonts || []).map(f => f.url)
  // 无任何可用字体 + 含中日韩文本：libass 缺字形会显示不全，
  // 自动降级浏览器 VTT（系统字体渲染，保证可读；投放字体到 data/fonts/ 即恢复 ASS 样式）；
  // 本地临时 ASS 无服务端 VTT 变体 → 保持 JASSUB（宁可默认字体也不空白）
  if (!fonts.length && !isLocalSub(sub)) {
    const needCjk = await assNeedsCjk(assUrl)
    if (videoEl.value !== v || burnOn || subKind(selectedSub()) !== 'ass') return
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
      subUrl: assUrl,
      fonts,
      workerUrl: mod.workerUrl,
      wasmUrl: mod.wasmUrl,
      modernWasmUrl: mod.modernWasmUrl,
      timeOffset: subShift() + subDelay.value,
      // ASS 里指定字体缺失时用系统/内置兜底；无字体也不崩（libass 用内置 Liberation Sans）
      defaultFont: 'Liberation Sans',
    })
    assKey = key
    posHint.value = fonts.length
      ? `ASS 字幕（样式渲染，${fonts.length} 个可用字体）`
      : (isLocalSub(sub)
        ? 'ASS 字幕：未找到内嵌/内置字体，文字可能走默认字体；可把中文字体（woff2/ttf/ttc）放入 data/fonts/'
        : 'ASS 字幕：未找到内嵌/内置字体，文字可能走默认字体；异常可勾选「兼容」或投放字体到 data/fonts/')
    const inst = jassub
    Promise.resolve(inst.ready)
      .then(() => {
        if (jassub === inst) { try { inst.timeOffset = subShift() + subDelay.value } catch (e) { /* 忽略 */ } }
        logEvt('ass:ready', 'fonts=' + fonts.length)
      })
      .catch((e) => { posHint.value = 'ASS 渲染初始化失败' + (isLocalSub(sub) ? '' : '，可勾选「兼容」改用 VTT 字幕'); logEvt('ass:error', String(e).slice(0, 160)) })
  } catch (e) {
    posHint.value = 'ASS 渲染初始化失败' + (isLocalSub(sub) ? '' : '，可勾选「兼容」改用 VTT 字幕')
    logEvt('ass:init-error', String(e).slice(0, 160))
  }
}
// 当前会话片内 0 对应的源时间（direct=原文件时间轴，不需平移）
function subShift() {
  return method.value === 'direct' ? 0 : mediaStart
}
function applySubStyle() {
  if (!vttLayer) return
  vttLayer.className = `sub-layer bg-${subStyle.value.bg} ol-${subStyle.value.outline} anchor-${vttAnchor}`
}
function onSubStyleChange() {
  try {
    localStorage.setItem('jzmedia.subStyle', JSON.stringify({
      bg: subStyle.value.bg, outline: subStyle.value.outline,
      pos: subStyle.value.pos, size: subStyle.value.size,
    }))
  } catch (e) { /* 忽略 */ }
  applySubStyle()
  syncSubLayerRect()   // 位置/字号改动立即重排
  if (vttLayer) vttRender()
}
// 自绘图层：定位到视频「画面区」（contain 内接矩形，不进黑边）；字号随画面高缩放
function ensureSubLayer(v) {
  if (vttLayer && vttLayer.parentNode) return vttLayer
  vttLayer = document.createElement('div')
  vttLayer.className = 'sub-layer'
  vttLayer.setAttribute('aria-hidden', 'true')
  v.parentNode.insertBefore(vttLayer, v.nextSibling)
  applySubStyle()
  return vttLayer
}
function syncSubLayerRect() {
  const v = videoEl.value
  const layer = vttLayer
  if (!v || !layer || !layer.parentNode || !v.parentNode) return
  const cr = v.parentNode.getBoundingClientRect()
  const vr = v.getBoundingClientRect()
  if (!cr.width || !vr.width || !vr.height) return
  const left = vr.left - cr.left
  const top = vr.top - cr.top
  const w = vr.width
  const h = vr.height
  let picTop = 0
  let picW = w
  let picH = h
  const vw = Number(v.videoWidth) || 0
  const vh = Number(v.videoHeight) || 0
  if (vw > 0 && vh > 0) {
    const ar = vw / vh
    const boxAr = w / h
    if (ar > boxAr) {          // 画面更宽：上下黑边
      picH = w / ar
      picTop = (h - picH) / 2
    } else if (ar < boxAr) {   // 画面更高：左右黑边
      picW = h * ar
    }
  }
  const barBottom = Math.max(0, Math.round(h - picTop - picH))
  const fontPx = subFontPx(picH, subStyle.value.size)
  vttAnchor = pickSubAnchor(barBottom, fontPx, subStyle.value.pos)
  layer.style.fontSize = fontPx + 'px'
  if (vttAnchor === 'outside') {
    // 黑边模式（mpv sub-use-margins 同款）：整元素高度做定位，字底距屏幕底自适应；
    // 多行时允许“一行画面内一行黑边”，底部永不被裁
    layer.style.left = left + 'px'
    layer.style.top = top + 'px'
    layer.style.width = w + 'px'
    layer.style.height = h + 'px'
    layer.style.setProperty('--sub-pad', subBarPad(barBottom) + 'px')
  } else {
    // 画面内：约束在 contain 内接矩形（左右/上下黑边都不进），字号随画面高缩放
    layer.style.left = (left + (w - picW) / 2) + 'px'
    layer.style.top = (top + picTop) + 'px'
    layer.style.width = picW + 'px'
    layer.style.height = picH + 'px'
    layer.style.setProperty('--sub-pad', subInnerPad(picH) + 'px')
  }
  applySubStyle()
}
function startVttRO(v) {
  stopVttRO()
  if (typeof ResizeObserver === 'undefined' || !v || !v.parentNode) return
  vttRO = new ResizeObserver(() => syncSubLayerRect())
  try { vttRO.observe(v.parentNode); vttRO.observe(v) } catch (e) { /* 忽略 */ }
}
function stopVttRO() {
  if (vttRO) { try { vttRO.disconnect() } catch (e) { /* 忽略 */ } vttRO = null }
}
// 渲染循环：优先 requestVideoFrameCallback（帧级），否则 100ms 轮询兜底
function startVttLoop(v) {
  stopVttLoop()
  if (!v) return
  if (typeof v.requestVideoFrameCallback === 'function') {
    const step = () => {
      if (!vttLayer) return
      vttRender()
      try { vttLoopRvfc = v.requestVideoFrameCallback(step) } catch (e) { vttLoopRvfc = 0 }
    }
    try { vttLoopRvfc = v.requestVideoFrameCallback(step) } catch (e) { vttLoopRvfc = 0 }
  } else {
    vttLoopTimer = setInterval(vttRender, 100)
  }
}
function stopVttLoop() {
  const v = videoEl.value
  if (vttLoopRvfc && v && typeof v.cancelVideoFrameCallback === 'function') {
    try { v.cancelVideoFrameCallback(vttLoopRvfc) } catch (e) { /* 忽略 */ }
  }
  vttLoopRvfc = 0
  if (vttLoopTimer) { clearInterval(vttLoopTimer); vttLoopTimer = 0 }
}
// 命中判断：片内时间 + media_start + 用户延迟 落在 cue 源时间区间
function vttRender() {
  const v = videoEl.value
  const layer = vttLayer
  if (!v || !layer) return
  const off = (subShift() + subDelay.value) * 1000
  const t = (Number.isFinite(v.currentTime) ? v.currentTime : 0) * 1000 + off
  const act = activeCues(vttCues, t)   // 命中判定纯函数（评审 R13-Q4）
  const key = act.map(c => c.start + ':' + c.end).join(',')
  if (key === vttLastKey) return
  vttLastKey = key
  layer.textContent = ''
  for (const c of act) {
    const d = document.createElement('div')
    d.className = 'sub-cue' + (c.align ? ' ta-' + c.align : '')
    d.textContent = c.text
    layer.appendChild(d)
  }
}
function clearVttDom() {
  stopVttLoop()
  stopVttRO()
  const v = videoEl.value
  if (v) v.querySelectorAll('track').forEach(t => t.remove())
  const l = vttLayer
  vttLayer = null
  if (l) { try { l.remove() } catch (e) { /* 忽略 */ } }
}
function destroyVtt() {
  vttSeq++
  vttKey = ''
  vttLastKey = ''
  vttCues = []
  vttNativeFallback.value = false
  clearVttDom()
}
// 所选字幕是否走文本（自绘）渲染：VTT 本体 / ASS 勾选兼容 / ASS 无字体自动降级。
// 本地临时 ASS 无服务端 VTT 变体，不走兼容降级（保持 JASSUB 渲染）。
function isVttKind() {
  const s = selectedSub()
  const k = subKind(s)
  if (isLocalSub(s)) return k === 'vtt'
  return k === 'vtt' || (k === 'ass' && (compatSub.value
    || autoVttSub.value === Number(subIdx.value)))
}
function isVttSelected() {
  // 原生 <track> 兜底时不显示外观控件（改了也不生效，评审 B8/R13-B8）
  if (burnOn || vttNativeFallback.value) return false
  return isVttKind()
}
// 「外观」设置行可见性（复用以 isVttSelected 的同一判定）
const subIsVtt = computed(() => isVttSelected())
async function mountVttLayer(v, key) {
  const seq = ++vttSeq
  // 同轨重复调用（会话重载/延迟调整）不重拉：偏移在渲染时叠加
  if (vttKey === key && vttLayer && vttCues.length && !vttNativeFallback.value) {
    startVttLoop(v)
    startVttRO(v)
    syncSubLayerRect()
    vttRender()
    return
  }
  const sub = selectedSub() || {}
  const url = sub.url || `/api/stream/${props.versionId}/sub/${subIdx.value}.vtt${kindParam.value}`
  let text = typeof sub.text === 'string' ? sub.text : ''   // 本地字幕：已在加载期解码（含 GBK 兜底）
  if (!text) {
    try {
      const r = await fetch(url)
      if (!r.ok) throw new Error('http ' + r.status)
      text = await r.text()
    } catch (e) { text = '' }
  }
  if (seq !== vttSeq || videoEl.value !== v || !isVttSelected()) return
  const cues = text ? parseVtt(text) : []
  clearVttDom()
  vttKey = key
  vttCues = cues
  vttLastKey = ''
  vttNativeFallback.value = false
  if (!cues.length) {
    // 解析失败/空轨：回退原生 <track>（全局 ::cue 兜底样式已去默认黑底）
    vttNativeFallback.value = true
    const tr = document.createElement('track')
    tr.kind = 'subtitles'
    tr.src = url
    tr.default = true
    v.appendChild(tr)
    return
  }
  ensureSubLayer(v)
  startVttRO(v)
  startVttLoop(v)
  syncSubLayerRect()
  vttRender()
}
// 当前所选字幕轨（内嵌/外挂/本地统一取法）
function selectedSub() {
  return subs.value[subIdx.value] || null
}
function isLocalSub(s) {
  return !!(s && s.source === 'local')
}
// 服务端轨清单与本地临时字幕合并（每次 reload 后调用；本地轨追加在末尾，索引重排；
// 选中项若为本地轨按 localId 找回，避免清单变化后错位）。
function mergeSubs(serverSubs) {
  const sel = selectedSub()
  const selLocalId = isLocalSub(sel) ? sel.localId : ''
  subs.value = [...(serverSubs || []), ...localSubs.value]
  subs.value.forEach((s, i) => { s.index = i })
  if (selLocalId) {
    subIdx.value = subs.value.findIndex(s => s.localId === selLocalId)
  } else if (subIdx.value >= subs.value.length) {
    subIdx.value = -1
  }
}
function revokeLocalUrl(s) {
  if (s && s.blobUrl) {
    try { URL.revokeObjectURL(s.blobUrl) } catch (e) { /* 忽略 */ }
  }
}
// 临时加载本地字幕文件（srt/vtt 自绘、ass/ssa JASSUB；图片格式不支持，见 localSubCodec）。
// 纯浏览器端：不入库、不上传，关播放器即失效；同名文件重复加载自动替换。
async function onLoadSubFile(file) {
  const codec = localSubCodec(file && file.name)
  if (!codec) {
    posHint.value = '不支持的字幕格式：仅可临时加载 srt/vtt/ass/ssa（图片字幕请与正片同名放入片目录）'
    return
  }
  const localId = 'loc' + (++localSubSeq)
  const entry = { localId, source: 'local', title: (file && file.name) || ('本地字幕 ' + localSubSeq),
                  codec, image: 0, lang: '', default: 0, forced: 0 }
  try {
    if (codec === 'ass' || codec === 'ssa') {
      entry.url = URL.createObjectURL(file)
    } else {
      entry.text = decodeSubtitleBytes(await file.arrayBuffer())
      entry.url = URL.createObjectURL(new Blob([entry.text], { type: 'text/vtt' }))   // 原生 <track> 兜底用
    }
    entry.blobUrl = entry.url
  } catch (e) {
    posHint.value = '字幕文件读取失败：' + ((e && e.message) || e)
    return
  }
  const prev = localSubs.value.findIndex(s => s.title === entry.title)
  if (prev >= 0) {
    revokeLocalUrl(localSubs.value[prev])
    localSubs.value.splice(prev, 1)
  }
  localSubs.value.push(entry)
  mergeSubs(subs.value.filter(s => s.source !== 'local'))
  const li = subs.value.findIndex(s => s.localId === localId)
  if (li < 0) return
  subIdx.value = li
  autoSubPicked = true
  posHint.value = `已加载临时字幕：${entry.title}（仅本次播放，不入库）`
  if (burnOn) { reload(); return }   // 前一轨是烧录会话：重开会话去掉画面里烧死的字幕
  applySubs(true)
}
function removeLocalSubs() {
  if (!localSubs.value.length) return
  const wasLocal = isLocalSub(selectedSub())
  for (const s of localSubs.value) revokeLocalUrl(s)
  localSubs.value = []
  mergeSubs(subs.value.filter(s => s.source !== 'local'))
  if (wasLocal) subIdx.value = -1
  posHint.value = '已移除临时字幕'
  if (burnOn) { reload(); return }
  applySubs(true)
}
// 应用当前所选字幕（会话重载/换视频元素/切轨共用）：先拆旧层再按类型装新层。
// 文本(VTT 自绘)/ASS(PGS) 全部客户端渲染——切字幕不重开会话、不转码；仅 VobSub（burn）走烧录。
// 三类都吃 subShift()+subDelay 偏移（会话时间轴↔字幕绝对时间轴对齐）。
async function applySubs(force) {
  const v = videoEl.value
  if (!v) return
  const sel = selectedSub()
  const kind = burnOn ? 'burn' : subKind(sel)
  const subToken = isLocalSub(sel) ? sel.localId : subIdx.value
  const key = `${props.versionId}:${videoKey.value}:${subToken}:${compatSub.value ? 'v' : 'a'}`
  const toff = subShift() + subDelay.value
  if (jassub && (force || kind !== 'ass' || assKey !== key)) destroyAss()
  if (pgs && (force || kind !== 'pgs' || pgsKey !== key)) destroyPgs()
  if (kind === 'none' || kind === 'burn') { destroyVtt(); return }
  if (kind === 'vtt' || isVttKind()) {
    // 原生 <track> 兜底中：同轨无需重挂；换轨（含切到本地字幕）必须先清旧 track 再重挂
    if (vttNativeFallback.value) {
      if (vttKey === key) return
      destroyVtt()
    }
    await mountVttLayer(v, key)
    return
  }
  destroyVtt()
  // 渲染器注册表（评审 R13-Q2）：{alive,key,mount,reuse} → 新增渲染器只需加一条
  const r = SUB_RENDERERS[kind]
  if (!r) return
  if (r.alive() && r.key() === key) { r.reuse(toff); return }
  await r.mount(v, key)
}
// 外部渲染器（ASS/PGS）：挂载/复用/销毁统一由注册表驱动，键=版本:元素代:轨道:兼容位
const SUB_RENDERERS = {
  ass: {
    alive: () => !!jassub, key: () => assKey, mount: mountAss,
    reuse: (toff) => { try { jassub.timeOffset = toff } catch (e) { /* 忽略 */ } },
  },
  pgs: {
    alive: () => !!pgs, key: () => pgsKey, mount: mountPgs,
    reuse: (toff) => { try { pgs.timeOffset = toff } catch (e) { /* 忽略 */ } },
  },
}
// 所选字幕是否为图片型（需烧录；实际是否烧录以服务端 subtitle_mode 为准）
function imageSubSelected() {
  const s = selectedSub()
  return !!(s && s.image)
}
// 当前所选是否为 ASS/SSA（决定「兼容」开关是否显示）；本地临时 ASS 无服务端 VTT 变体，不显示
const subIsAss = computed(() => {
  const s = selectedSub()
  if (isLocalSub(s)) return false
  return subKind(s) === 'ass'
})
// 文本(VTT 自绘)/ASS/PGS 都支持 timeOffset（延迟控件可见）
const subDelayVisible = computed(() => {
  if (burnOn) return false
  const s = selectedSub()
  const k = subKind(s)
  if (k === 'vtt') return true
  if (isLocalSub(s)) return k === 'ass'   // 本地 ASS 直接 JASSUB 渲染，不受全局兼容开关影响
  return ['ass', 'pgs'].includes(k) && !compatSub.value && autoVttSub.value !== Number(subIdx.value)
})
const subDelayText = computed(() => (subDelay.value > 0 ? '+' : '') + subDelay.value.toFixed(1) + 's')
// PlayerSettings 回调（子组件只发信号，状态与副作用留在本组件）
const undoDegradeDisabled = computed(() => !forceBurn.value && !subIsAss.value && autoVttSub.value < 0 && !vttNativeFallback.value)
function onSubStyleSet(v) { subStyle.value = v; onSubStyleChange() }
function onCompatSet(v) { compatSub.value = v; onCompatChange() }
function shiftSubDelay(d) {
  const x = Math.round(Math.max(-10, Math.min(10, subDelay.value + d)) * 10) / 10
  subDelay.value = x
  try { localStorage.setItem(subDelayKey.value, String(x)) } catch (e) { /* 忽略 */ }
  const toff = subShift() + x
  if (jassub) { try { jassub.timeOffset = toff } catch (e) { /* 忽略 */ } }
  if (pgs) { try { pgs.timeOffset = toff } catch (e) { /* 忽略 */ } }
  if (vttLayer) vttRender()   // 自绘层：偏移在渲染时叠加，立即重绘
}
// 选完即失焦：否则焦点停在下拉框/滑块，方向键会去改控件而不是 seek/音量
function blurPick(e) {
  try {
    const el = e && e.target
    if (el && el.blur) el.blur()
  } catch (err) { /* 忽略 */ }
}
function onQualityChange(v) {
  quality.value = v
  dropHint.value = false
  reload()
}
// 掉帧看门狗采样（每 2s 一拍，挂在 bufTimer 上）：原画直通持续丢帧 → 提示 + 手点降档。
// 触发条件见 dropGuard.js（30s 窗口 ≥5 帧、起播 15s 后、仅视频 copy 路径）。
function sampleDropGuard() {
  const v = videoEl.value
  if (dropHintFired || !v || !v.getVideoPlaybackQuality) return
  if (!isCopyVideoPath(method.value, srcHeight.value)) return
  const playing = !v.paused && !v.seeking && !v.ended && (v.readyState || 0) >= 2
  if (playing && !dropPlayStart) dropPlayStart = Date.now()
  let q = null
  try { q = v.getVideoPlaybackQuality() } catch (e) { return }
  const r = tickDropGuard(dropGuard,
    { dropped: q.droppedVideoFrames, total: q.totalVideoFrames },
    { now: Date.now(), playing, startedAt: dropPlayStart })
  if (r.fire) {
    dropHintFired = true
    dropHint.value = true
    dropDrops.value = r.drops
    logEvt('drops:detected', 'd30=' + r.drops + ' f30=' + r.frames)
  }
}
function switchTo1080() {
  clearDropCountdown()
  onQualityChange('1080p')
  posHint.value = '已按建议切换到 1080p 转码；如仍想原画，可在「⚙ 设置」里切回'
}
// 音轨切换：fMP4 rendition 已在会话里 → 切 hls.audioTrack 即刻生效（视频不重编不重开）；
// 原生 Safari 用 video.audioTracks；会话尚未就绪时只改选择，等 MANIFEST_PARSED 应用；
// 都不支持（TS 回滚单轨产物）才回退重开会话。
function onAudioChange(v) {
  audioIdx.value = v
  logEvt('audio-change', 'ref=' + (Number(audioIdx.value) || 0) + ' ready=' + manifestReady)
  if (applyAudioTrack()) return
  if (applyNativeAudioTrack()) return
  if (isHls.value && !manifestReady) return
  reload()
}
// 字幕切换：文本/ASS/PGS 都是客户端渲染层 → 即时切换不重开会话；
// VobSub（burn）或已处于烧录模式（forceBurn 降级）才重开转码
function onSubChange(v) {
  subIdx.value = v
  autoSubPicked = true   // 用户手动选过字幕，不再自动选外挂默认轨
  if (burnOn || subKind(subs.value[subIdx.value]) === 'burn') { reload(); return }
  applySubs(true)
}
// 撤销自动降级（评审 B8/R13-Q3）：VTT 兼容/烧录/原生兜底一键恢复客户端渲染
function undoDegrade() {
  forceBurn.value = false
  autoVttSub.value = -1
  compatSub.value = false
  try { localStorage.setItem('jzmedia.subCompat', '0') } catch (e) { /* 忽略 */ }
  vttNativeFallback.value = false
  posHint.value = '已恢复客户端渲染，正在重新加载…'
  reload()
}
// 「兼容字幕(VTT)」：ASS 样式渲染异常/无字体时的降级开关
function onCompatChange() {
  try { localStorage.setItem('jzmedia.subCompat', compatSub.value ? '1' : '0') } catch (e) { /* 忽略 */ }
  applySubs(true)
}
// 复制原文件直链（VLC/Kodi/电视播放器）：HDR/DV 等浏览器难处理的片源走这条路
async function copyDirectLink() {
  const url = directUrl.value
  if (!url) { posHint.value = '直链暂不可用，请稍后重试'; return }
  directFailUrl.value = ''
  const abs = location.origin + url
  if (await copyText(abs)) {
    posHint.value = '直链已复制：可在 VLC/Kodi/电视播放器里打开'
  } else {
    directFailUrl.value = abs
    posHint.value = '自动复制失败（浏览器限制），已显示链接，点框后 Ctrl+C 手动复制'
  }
}
// 总时长统一用探测值：HLS 增长型清单里 v.duration 只是“已产出片段之和”（如 30s），
// 用它算剩余会一开播就误判“已看”、存档 duration 也会写坏导致详情页看不到续播。
function mediaDuration(v) {
  const real = Number(decidedDuration.value) || 0
  if (real > 0) return real
  return (v && Number.isFinite(v.duration) && v.duration > 0) ? v.duration : 0
}
// 进度上报去重（用户 2026-09）：并发调用共享同一请求；失败后至少间隔 10s 再试，
// 防 timeupdate 高频触发 + 网络失败时堆积重复 POST（存档为单行 UPSERT、无文件 I/O）。
let savePromise = null
let lastSaveAttempt = 0
// 取关闭/上报这一刻的存档内容（pos==null 表示无有效位置，不写）
function progressPayload(v) {
  const pos = pickProgressPosition({
    seekPending: seekPending.value, seekPreview: seekPreview.value,
    absPos: absPos(), currentTime: v.currentTime
  })
  return pos == null ? null : { pos, dur: mediaDuration(v) }
}
async function postProgress(p) {
  try {
    await api(`/api/stream/progress?version_id=${props.versionId}${kindSuffix.value}`, {
      method: 'POST', body: JSON.stringify({ position: p.pos, duration: p.dur || 0 }),
      keepalive: true,   // 关页/刷新时也能把最后一次进度发出去（评审 B8/R14-D3）
      timeout: 15000     // 单行 UPSERT：慢/挂起时及时放弃，别拖住关播后的「上次看到」刷新
    })
    lastSave = Date.now()
    posHint.value = `已记录 ${fmt(p.pos)}`
  } catch (e) { /* 进度上报失败不打扰播放 */ }
}
function saveNow() {
  if (savePromise) return savePromise
  lastSaveAttempt = Date.now()
  const v = videoEl.value
  const p = v ? progressPayload(v) : null
  savePromise = (p ? postProgress(p) : Promise.resolve()).finally(() => { savePromise = null })
  return savePromise
}
// 关闭时最终存档：等在途请求落定后按「关闭这一刻」的位置再写一次（顺序正确、不并发堆积）。
// 幂等：父级关窗时先调一次、卸载钩子再调返回同一 Promise，不会双发。
let finalSavePromise = null
function saveFinal() {
  if (finalSavePromise) return finalSavePromise
  const v = videoEl.value
  const p = v ? progressPayload(v) : null
  finalSavePromise = (savePromise || Promise.resolve()).then(() => (p ? postProgress(p) : undefined))
  return finalSavePromise
}
function onTime() {
  const v = videoEl.value
  if (!seekPending.value && !seekDragging.value) seekPos.value = absPos()
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
  if (Date.now() - Math.max(lastSave, lastSaveAttempt) > 10000) saveNow()   // 成功后 10s 间隔；失败也不密集重试
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
  clearResumeTimer()   // 用户已 seek：连倒计时一起清，防残留回调把画面拉回断点
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
  // direct 且浏览器没吃 #t（仍在片头附近）才补跳一次，点晚了也不把画面倒回断点。
  clearResumeTimer()
  resumeOffer.value = ''
  const v = videoEl.value
  if (v && method.value === 'direct' && lastStartAt > 0) {
    const cur = Number(v.currentTime) || 0
    if (cur < lastStartAt - 5) {
      try { v.currentTime = lastStartAt } catch (e) { /* 忽略 */ }
    }
  }
}
async function restartPlay() {
  clearResumeTimer()
  resumeOffer.value = ''
  resumePos = 0
  startFromZero = true
  startOffset.value = 0
  seekPos.value = 0
  try {
    await api(`/api/stream/progress?version_id=${props.versionId}${kindSuffix.value}`, { method: 'DELETE' })
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
  // 进入全屏视为同意继续播放（用户 2026-09）：立即消条
  if (isFull.value && resumeOffer.value) resumePlay()
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
      info.totalFrames = q.totalVideoFrames
      info.corruptedFrames = q.corruptedVideoFrames
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
    const saved = Number(localStorage.getItem(subDelayKey.value))
    if (Number.isFinite(saved)) subDelay.value = Math.round(saved * 10) / 10
  } catch (e) { /* 忽略 */ }
  try {
    const p = await api(`/api/stream/progress?version_id=${props.versionId}${kindSuffix.value}`)
    const pos = Number(p.position) || 0
    let dur = Number(p.duration) || 0
    // 旧版本用 HLS 增长清单时长（如 30s）写坏过存档：dur < pos 视为不可信，按未看完处理
    if (dur > 0 && dur < pos) dur = 0
    const remain = dur - pos
    if (pos > 15 && (dur === 0 || !(remain / dur < 0.05 || remain < 300))) {
      resumePos = pos
      resumeOffer.value = p.position_text || fmt(pos)
      // 弹窗打开即计时：10s 内未点任一按钮 = 同意续播，自动消条（direct 缺 #t 会在到点时补跳）
      clearResumeTimer()
      resumeTimer = setTimeout(() => { resumeTimer = 0; resumePlay() }, 10000)
    }
  } catch (e) { /* 无断点直接播 */ }
  await reload()
  bindVideo(videoEl.value)
  document.addEventListener('fullscreenchange', onFullChange)
  document.addEventListener('click', onDocClick)
  window.addEventListener('beforeunload', saveNow)
  window.addEventListener('keydown', onKeydown)
  try {
    // 调试口收进命名空间（评审 B8/R14-D4）；__jzPlayerDebug 保留兼容
    window.__jzPlayer = { snapshot: () => debugSnapshot(), hls: () => hls,
                          ass: () => jassub, pgs: () => pgs }
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
      sampleDropGuard()
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
    if (!v || v.ended) { stallTicks = 0; lastTickPos = -1 }
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
      }
    }
    else if ((v.readyState || 0) >= 2) {
      const cur = Number(v.currentTime) || 0
      const moved = lastTickPos >= 0 && Math.abs(cur - lastTickPos) > 0.05
      lastTickPos = cur
      if (moved) { stallTicks = 0 }
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
function onPausePing() {
  // 暂停会停止 hls.js 取片 → 服务端 10min 无心跳会回收会话（评审 B8/R12-D8）
  if (!sessionId) return
  api(`/api/stream/sessions/${sessionId}/ping`, { method: 'POST' })
    .catch(() => { /* 心跳失败不打扰 */ })
}
const _evtHandlers = {
  waiting: () => logEvt('video:waiting', 't=' + fmtT(videoEl.value)),
  stalled: () => logEvt('video:stalled', 't=' + fmtT(videoEl.value)),
  seeking: () => logEvt('video:seeking', 'to=' + fmtT(videoEl.value)),
  seeked: () => { logEvt('video:seeked', 't=' + fmtT(videoEl.value)) },
  emptied: () => logEvt('video:emptied', ''),
  suspend: () => logEvt('video:suspend', ''),
  abort: () => logEvt('video:abort', ''),
  canplay: () => logEvt('video:canplay', ''),
}
function bindVideo(v) {
  if (!v) return
  v.addEventListener('timeupdate', onTime)
  v.addEventListener('timeupdate', vttRender)
  v.addEventListener('seeked', vttRender)
  v.addEventListener('loadedmetadata', syncSubLayerRect)
  v.addEventListener('pause', saveNow)
  v.addEventListener('pause', onPlayState)
  v.addEventListener('pause', onPausePing)
  v.addEventListener('ended', onEnded)
  v.addEventListener('playing', onPlayingHide)
  v.addEventListener('play', onPlayState)
  for (const [ev, h] of Object.entries(_evtHandlers)) v.addEventListener(ev, h)
  muted.value = !!v.muted
  volume.value = Number(v.volume ?? 1)
}
function unbindVideo(v) {
  if (!v) return
  v.removeEventListener('timeupdate', onTime)
  v.removeEventListener('timeupdate', vttRender)
  v.removeEventListener('seeked', vttRender)
  v.removeEventListener('loadedmetadata', syncSubLayerRect)
  v.removeEventListener('pause', saveNow)
  v.removeEventListener('pause', onPlayState)
  v.removeEventListener('pause', onPausePing)
  v.removeEventListener('ended', onEnded)
  v.removeEventListener('playing', onPlayingHide)
  v.removeEventListener('play', onPlayState)
  for (const [ev, h] of Object.entries(_evtHandlers)) v.removeEventListener(ev, h)
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
  info.drops = { hint: dropHint.value, fired: dropHintFired,
    win: { at: dropGuard.at, dropped: dropGuard.dropped, total: dropGuard.total } }
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
onBeforeUnmount(() => {
  // Vue 在 unmount 阶段先 setRef(null) 再跑 onUnmounted：videoEl 在 onUnmounted 已为 null，
  // 最终进度必须在这里上报（用户 2026-09：拖进度后关闭，重开回到旧断点）。
  // 走 closeStream 关闭时父级已先调过 saveFinal（同一 Promise）；这里覆盖路由切换等直接卸载。
  saveFinal()
  try { unbindVideo(videoEl.value) } catch (e) { /* 忽略 */ }
})
onUnmounted(() => {
  closeSession()
  document.removeEventListener('fullscreenchange', onFullChange)
  document.removeEventListener('click', onDocClick)
  window.removeEventListener('beforeunload', saveNow)
  window.removeEventListener('keydown', onKeydown)
  if (hideTimer) clearTimeout(hideTimer)
  if (bufTimer) clearInterval(bufTimer)
  if (saveTimer) clearTimeout(saveTimer)
  clearDropCountdown()
  clearResumeTimer()
  destroyHls()
  destroyAss()
  destroyPgs()
  destroyVtt()
  for (const s of localSubs.value) revokeLocalUrl(s)   // 临时字幕 blob URL 回收
  localSubs.value = []
})
// 父级关窗时先取最终存档 Promise：落库后再刷新详情页「上次看到」（卸载后 emit 会被 Vue 丢弃）
defineExpose({ saveFinal })
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

/* 设置弹层样式随子组件 PlayerSettings.vue + 全局 player.css（R14-Q5 约定） */
.pd-set-host { display: inline-flex; align-items: center; }

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
.hint-line { margin: 0; color: #666; font-size: 0.8125rem; overflow-wrap: anywhere; }
/* 掉帧询问浮层（窗口/全屏统一）：画面居中，控件条/标题显隐都不遮挡 */
.drop-prompt {
  position: absolute; z-index: 6; top: 50%; left: 50%; transform: translate(-50%, -50%);
  display: flex; gap: 10px; align-items: center; max-width: 92vw;
  padding: 10px 16px; border-radius: 10px; background: rgba(0, 0, 0, .82);
  color: #eee; font-size: 0.875rem; box-shadow: 0 4px 18px rgba(0, 0, 0, .4);
}
.drop-prompt button { padding: 3px 14px; border-radius: 999px; font-size: 0.8125rem; cursor: pointer; }
.drop-go { background: #2b6cb0; border: 1px solid #2b6cb0; color: #fff; }
.drop-cancel { background: transparent; border: 1px solid #777; color: #ccc; }
.drop-count { color: #9ecfff; font-variant-numeric: tabular-nums; }
.hint.warn { color: #e0a63c; }
</style>
