<template>
  <div class="dlg-mask player-mask" @click.self="$emit('close')">
    <div ref="dlgRef" class="player-dlg" role="dialog" aria-modal="true" :aria-label="title || '视频播放器'">
      <header class="pd-head">
        <div class="pd-heading">
          <span class="pd-eyebrow">{{ preview ? '文件预览 · 不记录观看进度' : isEpisode ? '剧集' : isExtra ? '花絮' : '电影' }}</span>
          <h3>{{ title || ('版本 ' + versionId) }}</h3>
        </div>
        <span v-if="qualityBadge" class="quality-badge" :title="qualityLine">{{ qualityBadge }}</span>
        <button class="player-icon-btn pd-close" @click="$emit('close')" aria-label="关闭播放器" title="关闭（Esc）">
          <PlayerIcon name="close" />
        </button>
      </header>
      <div ref="pvWrapEl" class="pv-wrap has-bar"
        :class="{ 'hide-cursor': isFull && !overlayVisible }"
        :style="videoPadding ? { paddingTop: videoPadding } : {}"
        @pointermove="onMouseMove" @pointerdown="showOverlay" @dblclick="onSurfaceDoubleClick"
        @focusin="onControlFocus" @focusout="controlsFocused = false">
        <video ref="videoEl" :key="videoKey" autoplay playsinline preload="metadata" class="player-video"
          @click="onSurfaceClick" @error="onVideoError"></video>
        <img v-if="freezeFrame" :src="freezeFrame" class="freeze-frame" alt="" />
        <div v-if="(seekPending || booting) && !err" class="stage-state seek-ov" role="status" aria-live="polite">
          <Spinner :size="36" />
          <strong>{{ seekPending ? ('正在跳转到 ' + fmt(seekPos)) : '正在准备播放' }}</strong>
          <span>正在加载视频，请稍候</span>
        </div>
        <div v-else-if="err" class="stage-state" @dblclick.stop>
          <div class="status-card error-card" role="alert">
            <PlayerIcon name="info" :size="30" />
            <strong>播放遇到问题</strong>
            <p>{{ err }}</p>
            <JzButton size="compact" variant="primary" class="player-action" @click="reload">重新加载</JzButton>
          </div>
        </div>
        <div v-else-if="needGesture || !isPlaying" class="stage-state pause-state" @dblclick.stop>
          <button class="center-play" @click="needGesture ? userPlay() : togglePlay()" :aria-label="needGesture ? '开始播放' : '继续播放'">
            <PlayerIcon name="play" :size="38" />
          </button>
          <span>{{ needGesture ? '点击开始播放' : '已暂停' }}</span>
        </div>
        <div class="pv-top" :class="{ show: overlayVisible }" @dblclick.stop>
          <div class="pv-heading"><span class="pd-eyebrow">正在播放</span><span class="pv-title">{{ title || ('版本 ' + versionId) }}</span></div>
          <button class="player-icon-btn" @click="toggleFull" aria-label="退出全屏" title="退出全屏（Esc）">
            <PlayerIcon name="exitFullscreen" />
          </button>
        </div>
        <div v-if="resumeOffer && !booting && !seekPending" class="resume-bar" @dblclick.stop>
          <span>上次看到 <strong>{{ resumeOffer }}</strong></span>
          <JzButton size="compact" variant="primary" class="player-action" @click="resumePlay">继续观看</JzButton>
          <JzButton size="compact" class="player-action" @click="restartPlay">从头开始</JzButton>
        </div>
        <div v-if="(posHint || sessStatus) && overlayVisible && !booting && !seekPending" class="player-toast" role="status" @dblclick.stop>{{ posHint || sessStatus }}</div>
        <div class="pv-ctl" :class="{ show: overlayVisible }" @dblclick.stop>
          <PlayerSeekbar :duration="decidedDuration" :manifest="previews.manifest.value" :buffered="bufferedSections"
            :value="seekDragging ? seekPreview : seekPos"
            :disabled="seekPending || !(decidedDuration > 0)"
            @input="onSeekInput" @commit="onSeekCommit" @active="previewActive = $event"
            @cancel="seekDragging = false" />
          <div class="ctl-row">
            <div class="ctl-group">
              <button class="player-icon-btn main-play" @click="togglePlay" :aria-label="isPlaying ? '暂停' : '播放'"
                :title="isPlaying ? '暂停（空格）' : '播放（空格）'">
                <PlayerIcon :name="isPlaying ? 'pause' : 'play'" :size="26" />
              </button>
              <button class="player-icon-btn skip-btn" @click="seekBy(-10)" :disabled="!(decidedDuration > 0)" aria-label="快退 10 秒" title="快退 10 秒（←）"><PlayerIcon name="rewind" /></button>
              <button class="player-icon-btn skip-btn" @click="seekBy(10)" :disabled="!(decidedDuration > 0)" aria-label="快进 10 秒" title="快进 10 秒（→）"><PlayerIcon name="forward" /></button>
              <div class="volume-group">
                <button class="player-icon-btn" @click="toggleMute" :aria-label="muted ? '取消静音' : '静音'" :title="muted ? '取消静音' : '静音'">
                  <PlayerIcon :name="muted || volume === 0 ? 'mute' : 'volume'" />
                </button>
                <input type="range" min="0" max="100" :value="muted ? 0 : volume * 100" aria-label="音量"
                  :style="{ '--volume': (muted ? 0 : volume * 100) + '%' }" @input="setVolume" @change="blurPick" class="ctl-vol" />
              </div>
              <span class="ctl-time"><span>{{ fmt(seekDragging ? seekPreview : seekPos) }}</span><span class="time-divider">/</span><span class="time-total">{{ fmt(decidedDuration) }}</span></span>
            </div>
            <div class="ctl-group ctl-options">
              <button class="rate-btn" @click.stop="toggleSettings" :aria-label="'播放速度 ' + playbackRate + ' 倍'" title="播放速度" :aria-expanded="settingsOpen">{{ playbackRate }}<span>×</span></button>
              <PlayerSettings v-if="!isFull" v-bind="settingsProps" v-on="settingsEvents" />
              <PlayerSettings v-else v-bind="settingsProps" v-on="settingsEvents" />
              <button class="player-icon-btn" @click="toggleFull" :aria-label="isFull ? '退出全屏' : '全屏'" :title="isFull ? '退出全屏（Esc）' : '全屏（双击画面）'">
                <PlayerIcon :name="isFull ? 'exitFullscreen' : 'fullscreen'" />
              </button>
            </div>
          </div>
        </div>
        <div v-if="dropHint" class="drop-prompt status-card" @dblclick.stop role="alert">
          <PlayerIcon name="info" :size="28" />
          <strong>播放不够流畅？</strong>
          <p>过去 30 秒丢失 {{ dropDrops }} 帧，切换至 1080p 可减轻播放负担。</p>
          <div class="prompt-actions">
            <JzButton size="compact" variant="primary" class="player-action" @click="switchTo1080">切换至 1080p</JzButton>
            <JzButton size="compact" class="player-action" @click="cancelDropPrompt">保持原画 <span class="drop-count">{{ dropCountdown }}s</span></JzButton>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, onUnmounted, nextTick } from 'vue'
import { api } from '../api.js'
import { copyText } from '../clipboard.js'
import { getCaps, probeStrings, withProbes } from '../caps.js'
import { createDropGuard, tickDropGuard, isCopyVideoPath } from '../dropGuard.js'
import Spinner from './Spinner.vue'
import { useFocusTrap } from '../useFocusTrap.js'
import { useSubtitles } from '../useSubtitles.js'
import PlayerSettings from './PlayerSettings.vue'
import PlayerIcon from './PlayerIcon.vue'
import JzButton from './JzButton.vue'
import PlayerSeekbar from './PlayerSeekbar.vue'
import { applyPlaybackRate, normalizeRate, reusableSeekTime } from '../playbackControls.js'
import { usePlaybackPreviews } from '../usePlaybackPreviews.js'
import { fmtTime as fmt } from '../playerLabels.js'
import { isPlaybackComplete, pickProgressPosition } from '../progress.js'
import '../player.css'
// hls.js 懒加载（~600KB）：只在进入播放器且非 Safari 时才下载，不拖首屏
let HlsCls = null
async function ensureHls() {
  if (!HlsCls) HlsCls = (await import('hls.js')).default
  return HlsCls
}

const props = defineProps({ versionId: { type: Number, required: true }, title: { type: String, default: '' },
  kind: { type: String, default: 'movie' }, preview: { type: Boolean, default: false } })
const isEpisode = computed(() => props.kind === 'episode')
const isExtra = computed(() => props.kind === 'extra')
const kindParam = computed(() => isEpisode.value ? '?kind=episode'
  : (isExtra.value ? '?kind=extra' : ''))
const kindSuffix = computed(() => isEpisode.value ? '&kind=episode'
  : (isExtra.value ? '&kind=extra' : ''))
const emit = defineEmits(['close', 'watched', 'ended'])

const dlgRef = ref(null)
const videoEl = ref(null)
const previews = usePlaybackPreviews(() => ({ id: props.versionId, kind: props.kind }))
const playbackRate = ref(readPlaybackRate())
const previewActive = ref(false)
const controlsFocused = ref(false)
const settingsPanelHeight = ref(300)
const bufferedSections = ref([])
let playerResizeObserver = null
let surfaceClickTimer = 0
let toastTimer = 0
function onControlFocus(e) { controlsFocused.value = !!e.target?.matches?.(':focus-visible') }
function onSurfaceClick() {
  clearTimeout(surfaceClickTimer)
  surfaceClickTimer = setTimeout(() => { if (!disposed && !booting.value && !seekPending.value) togglePlay() }, 220)
}
function onSurfaceDoubleClick() { clearTimeout(surfaceClickTimer); toggleFull() }
function observePlayerSize() {
  const el = pvWrapEl.value
  if (!el) return
  const resize = () => { settingsPanelHeight.value = Math.max(120, el.clientHeight - 108) }
  resize()
  if (typeof ResizeObserver === 'undefined') return
  playerResizeObserver = new ResizeObserver(resize)
  playerResizeObserver.observe(el)
}
function readPlaybackRate() {
  try { return normalizeRate(localStorage.getItem('jzmedia.playbackRate')) } catch { return 1 }
}
function restorePlaybackRate() {
  try { applyPlaybackRate(videoEl.value, playbackRate.value) }
  catch (e) { logEvt('rate:error', e.message) }
}
function onRateChange(rate) {
  playbackRate.value = normalizeRate(rate)
  restorePlaybackRate()
  try { localStorage.setItem('jzmedia.playbackRate', String(playbackRate.value)) }
  catch (e) { logEvt('rate:storage', e.message) }
  if (hls) hls.config.maxBufferLength = 30 * Math.max(1, playbackRate.value)
}
function syncPlaybackRate() {
  const rate = videoEl.value?.playbackRate
  if (rate > 0) playbackRate.value = normalizeRate(rate)
}
let hls = null
useFocusTrap(ref(true), computed(() => isFull.value ? pvWrapEl.value : dlgRef.value))
let saveTimer = 0
let lastSave = 0
const quality = ref('auto')
const audioIdx = ref(0)
const audios = ref([])
// 设置弹层（画质/音轨/字幕/延迟）：日常只留一个按钮，避免控件条拥挤/遮挡画面
const settingsOpen = ref(false)
function toggleSettings() { settingsOpen.value = !settingsOpen.value }
function onDocClick(e) {
  if (!settingsOpen.value) return
  const t = e && e.target
  if (t && t.closest && t.closest('.pd-setwrap')) return
  settingsOpen.value = false
}
const method = ref('')
const reasons = ref([])
const sessStatus = ref('')
// 起播等待浮层（decide/建会话最长 300s，画面纯黑会让用户以为卡死而关窗——关窗后的
// 晚到挂载正是“海报墙传出声音”的触发场景，见 reload/mountHls 的 disposed 守卫）
const booting = ref(false)
let sessionId = null
let pingTimer = 0
// 客户端能力：基础矩阵一次；逐片候选码串实测结果按版本缓存（decide/sessions 带 caps）
let activeCaps = null
const probedCaps = {}
const err = ref('')
const posHint = ref('')
watch(posHint, text => {
  clearTimeout(toastTimer)
  if (text) toastTimer = setTimeout(() => { posHint.value = '' }, 7000)
})
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
  posHint.value = '已保留原画（如仍卡顿可在「设置」里切档）'
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
// 卸载守卫：关窗后晚到的 decide/sessions 响应绝不挂 hls（元素已脱离文档，浏览器
// 不显示画面但会继续放音频 → 用户 2026-09 实测“关窗后海报墙听见片声”）。
let disposed = false
let bootCtrl = null   // 当前 reload 的取消控制器（关窗/新一轮 reload 中止在飞请求）
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
let keyboardSeekTimer = 0
let keyboardSeekTarget = null
let seekFallbackTimer = 0
// 冻结帧 + 容器比例（padding-top 撑高，绝对定位铺满：换会话时窗口绝不塌）
const freezeFrame = ref('')
const videoPadding = ref('calc(min(56.25%, 64vh) + var(--pvb))')
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
  || settingsOpen.value || previewActive.value || controlsFocused.value || !!err.value)
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
const qualityBadge = computed(() => (planHeight.value || srcHeight.value) ? `${planHeight.value || srcHeight.value}p` : '')
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
  // 动态 import 等待期可能已关窗/换元素/新一轮 reload：绝不在过期元素上建实例
  // （对齐 useSubtitles 的 videoEl.value !== v 守卫；此处是音频泄漏的直接入口）
  if (disposed || !v || videoEl.value !== v) return false
  if (opts && opts.gen != null && opts.gen !== reloadGen) return false
  if (opts && opts.signal && opts.signal.aborted) return false
  if (!Hls || !Hls.isSupported()) {
    sessStatus.value = ''
    err.value = '当前浏览器不支持 HLS，请用 Chrome/Edge/Safari'
    return false
  }
  destroyHls()
  manifestReady = false
  const cfg = { maxBufferLength: 30 * Math.max(1, playbackRate.value), maxLiveSyncPlaybackRate: 1, maxMaxBufferLength: 60, backBufferLength: 30 }
  if (opts && opts.fromStart) cfg.startPosition = Math.max(0, Number(targetMediaTime) || 0)
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
    try {
      const frag = (data || {}).frag || {}
      logEvt('hls:FRAG_BUFFERED', 'sn=' + (frag.sn ?? '') + ' type=' + (frag.type || ''))
    } catch (e) { /* 忽略 */ }
  })
  hls.on(Hls.Events.MEDIA_ATTACHED, () => {
    if (targetMediaTime !== null && targetMediaTime !== undefined) {
      try { v.currentTime = Math.max(0, targetMediaTime) } catch (e) { /* 忽略 */ }
    }
    tryPlay()
  })
  hls.on(Hls.Events.MANIFEST_PARSED, () => {
    restorePlaybackRate()
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
// 起播浮层收口：有明确错误或需要用户手势时立即撤掉，不遮挡错误/手势条
watch(needGesture, (v) => { if (v) booting.value = false })
watch(err, (v) => { if (v) booting.value = false })
// 用户是否期望在播（区分故意暂停）：tryPlay/手动播放=true，暂停键=false
let wantPlaying = false
let lastPlayAttempt = 0
function tryPlay() {
  const v = videoEl.value
  if (!v || disposed) return
  restorePlaybackRate()
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
  if (!sessionId || disposed) return
  pingTimer = setInterval(async () => {
    if (!sessionId || disposed) return
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
async function decidePlayback(subArg, signal) {
  const base = activeCaps || probedCaps[props.versionId] || await getCaps()
  const post = (caps) => api(`/api/stream/${props.versionId}/decide`, {
    method: 'POST',
    signal,
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
  clearTimeout(seekFallbackTimer)
  clearTimeout(keyboardSeekTimer)
  keyboardSeekTarget = null
  const gen = ++reloadGen
  // 中止上一轮在飞请求（旧 decide/sessions 即使晚到也会被 gen/disposed 守卫丢弃）
  if (bootCtrl) { try { bootCtrl.abort() } catch (e) { /* 忽略 */ } }
  const ctrl = new AbortController()
  bootCtrl = ctrl
  booting.value = true
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
  if (!v || disposed) { booting.value = false; return }   // 关窗/换元素后不再起新一轮
  try { v.pause() } catch (e) { /* 忽略 */ }
  v.removeAttribute('src'); v.load()
  let d
  // 图片字幕：默认客户端渲染（PGS→libpgs）；VobSub/解码降级走烧录（服务端 subtitle_mode）
  const wantBurn = imageSubSelected()
  const burnSub = wantBurn ? Number(subIdx.value) : -1
  // 本地临时字幕不在服务端轨清单里：decide/sessions 一律传 null（防 subtitle_not_found）
  const serverSub = burnSub >= 0 ? burnSub : (isLocalSub(selectedSub()) ? null : (subIdx.value >= 0 ? subIdx.value : null))
  try {
    d = await decidePlayback(serverSub, ctrl.signal)
  } catch (e) {
    if (disposed || gen !== reloadGen || ctrl.signal.aborted) return
    err.value = '无法播放：' + e.message
    return
  }
  if (disposed || gen !== reloadGen) return   // 新一轮 reload/关窗已接管，放弃本轮（防两个 hls 实例互踩）
  burnOn = d.subtitle_mode ? d.subtitle_mode === 'burn' : wantBurn
  method.value = d.method
  reasons.value = d.reasons || []
  audios.value = d.media?.audio || []
  // 合并服务端轨 + 自动选默认轨（打开时一次；用户手动选过后不再覆盖）
  syncForSession(d.media?.subs || [])
  planHeight.value = Number(d.plan?.height) || 0
  directUrl.value = d.direct_url || ''
  directFailUrl.value = ''
  srcHeight.value = Number(d.media?.height) || 0
  // 播放器比例：padding-top = min(片源高宽比, 64vh)（16:9 片源按屏幕宽度自适应）；
  // HLS 模式底部额外预留控件条高度，视频区不被遮挡。无数据时容器也不塌陷。
  try {
    const w = Number(d.media?.width) || 0
    const h = Number(d.media?.height) || 0
    const pct = (w > 0 && h > 0)
      ? (Math.round((h / w) * 10000) / 100) + '%'
      : '56.25%'
    const box = 'min(' + pct + ', 64vh)'
    // 与 CSS 的两行控件高度同源，视频和字幕都避开控件区。
    videoPadding.value = 'calc(' + box + ' + var(--pvb))'
  } catch (e) { videoPadding.value = 'calc(min(56.25%, 64vh) + var(--pvb))' }
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
    // 服务端已编码路径和查询参数，再 encodeURI 会把中文路径中的 % 重复编码。
    v.src = d.direct_url + (startAt > 0 ? `#t=${Math.floor(startAt)}` : '')
    tryPlay()
  } else {
    // 渐进式会话：服务端按计划等待首批分片，首画面不等整片
    sessStatus.value = (d.method === 'remux' || d.method === 'audio_transcode')
      ? '正在换封装…' : '正在转码（前分片生成中，稍候即播）…'
    let s
    try {
      s = await api(`/api/stream/${props.versionId}/sessions`, {
        method: 'POST',
        // 建会话要等首批分片（弱 CPU 转码慢），放宽到 300s，对齐服务端 deadline
        timeout: 300000,
        signal: ctrl.signal,
        body: JSON.stringify({ quality: quality.value, audio: audioIdx.value,
                               start: Math.floor(startAt),
                               sub: burnSub >= 0 ? burnSub : null,
                               caps: activeCaps,
                               force_burn: forceBurn.value, kind: props.kind }),
      })
    } catch (e) {
      if (disposed || gen !== reloadGen || ctrl.signal.aborted) return
      sessStatus.value = ''
      err.value = String(e.message || '').startsWith('429')
        ? '服务器转码通道已满（最多 2 路），请稍后重试或先关闭其他播放'
        : '无法播放：' + e.message
      return
    }
    if (disposed || gen !== reloadGen) {
      // 晚到会话：本轮已被关窗/新档接管，立即回收，防孤儿 ffmpeg + 泄漏心跳（服务端
      // 另有 unclaimed 快速收割兜底客户端 abort 拿不到 sid 的情况）
      const late = s && s.session_id
      if (late) api(`/api/stream/sessions/${late}`, { method: 'DELETE' }).catch(() => { /* 忽略 */ })
      return
    }
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
    const initialTime = Math.max(0, Number(s.initial_time) || 0)
    startPing()
    const url = s.playlist_url
    lastPlaylistUrl = url
    recoverCount = 0
    stallTicks = 0
    lastTickPos = -1
    const onPlaying = () => { sessStatus.value = ''; booting.value = false }
    v.addEventListener('playing', onPlaying, { once: true })
    if (canUseNativeHls(v)) {
      engine = 'native'
      logEvt('engine:native', 'ManagedMediaSource')
      v.src = url
      // 原生 HLS 显式定位，避免增长型列表从直播边缘起播；完整缓存按目标时间起播。
      // 音轨选择用 video.audioTracks 应用（Safari 有；没有则维持默认轨）
      const onMeta = () => {
        if (disposed || gen !== reloadGen || videoEl.value !== v) return
        try { v.currentTime = initialTime } catch (e) { logEvt('seek:init', e.message) }
        restorePlaybackRate()
        applyNativeAudioTrack(v)
      }
      v.addEventListener('loadedmetadata', onMeta, { once: true })
      tryPlay()
    } else {
      engine = 'hls'
      logEvt('engine:hls', '')
      await mountHls(v, url, initialTime, { fromStart: true, gen, signal: ctrl.signal })
    }
    }
  } catch (e) {
    // 兜底：起播链路任何意外都不再静默 0:00，直接显示人话错误（关窗/新档接管后不报）
    if (disposed || gen !== reloadGen) return
    sessStatus.value = ''
    err.value = '播放失败：' + (e && e.message ? e.message : e)
  }
}
// 字幕子系统（选轨/本地临时字幕/VTT 自绘/ASS/PGS/延迟/外观）：见 ../useSubtitles.js
// （R13-Q1/R14-Q1 自本组件抽离；对外标识符与原实现同名，模板/调用点不变）
const {
  subs, subIdx, localSubs, subStyle, forceBurn, compatSub,
  subIsAss, subIsVtt, subDelayText, subDelayVisible, undoDegradeDisabled,
  selectedSub, isLocalSub, imageSubSelected,
  syncForSession, applySubs, onSubChange, onLoadSubFile, removeLocalSubs,
  onSubStyleSet, onCompatSet, undoDegrade, shiftSubDelay,
  destroyAss, destroyPgs, dispose,
  attachVideo: attachSubtitleVideo, detachVideo: detachSubtitleVideo,
  debugInfo: subtitleDebugInfo, assInstance, pgsInstance,
} = useSubtitles({
  videoEl, videoKey,
  versionId: () => props.versionId,
  kindParam: () => kindParam.value,
  isEpisode: () => isEpisode.value,
  delayKeyPrefix: () => (isExtra.value ? 'x.' : (isEpisode.value ? 'ep.' : '')),
  getMethod: () => method.value,
  getMediaStart: () => mediaStart,
  getBurnOn: () => burnOn,
  reload: () => reload(),
  toast: (t) => { posHint.value = t },
  logEvt: (kind, detail) => logEvt(kind, detail),
})

// 设置放在容器内的底部控件栏，全屏仍可访问；保留条件实例及父级 open 状态。
const settingsProps = computed(() => ({
  panelHeight: settingsPanelHeight.value,
  reasonLine: reasonLine.value,
  bufferLine: bufLine.value,
  playbackRate: playbackRate.value,
  previewBusy: previews.busy.value,
  previewStatus: previews.status.value,
  open: settingsOpen.value,
  quality: quality.value,
  audios: audios.value,
  audioIdx: audioIdx.value,
  subs: subs.value,
  subIdx: subIdx.value,
  subDelayVisible: subDelayVisible.value,
  subDelayText: subDelayText.value,
  subIsVtt: subIsVtt.value,
  subIsAss: subIsAss.value,
  forceBurn: forceBurn.value,
  undoDisabled: undoDegradeDisabled.value,
  compatSub: compatSub.value,
  hasLocalSub: localSubs.value.length > 0,
  directFailUrl: directFailUrl.value,
  methodLine: methodLine.value,
  qualityLine: qualityLine.value,
  subStyle: subStyle.value,
}))
const settingsEvents = {
  'copy-debug': copyDebug,
  'rate-change': onRateChange,
  'preview-start': previews.start,
  'preview-cancel': previews.cancel,
  'toggle-settings': toggleSettings,
  'quality-change': onQualityChange,
  'audio-change': onAudioChange,
  'sub-change': onSubChange,
  'shift-delay': shiftSubDelay,
  'update:sub-style': onSubStyleSet,
  'undo-degrade': undoDegrade,
  'compat-change': onCompatSet,
  'load-sub-file': onLoadSubFile,
  'remove-local-subs': removeLocalSubs,
  'copy-direct': copyDirectLink,
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
  posHint.value = '已按建议切换到 1080p 转码；如仍想原画，可在「设置」里切回'
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
  if (props.preview) return
  try {
    await api(`/api/stream/progress?version_id=${props.versionId}${kindSuffix.value}`, {
      method: 'POST', body: JSON.stringify({ position: p.pos, duration: p.dur || 0 }),
      keepalive: true,   // 关页/刷新时也能把最后一次进度发出去（评审 B8/R14-D3）
      timeout: 15000     // 单行 UPSERT：慢/挂起时及时放弃，别拖住关播后的「上次看到」刷新
    })
    lastSave = Date.now()
  } catch (e) { /* 进度上报失败不打扰播放 */ }
}
function saveNow() {
  if (props.preview) return Promise.resolve()
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
  if (props.preview) return Promise.resolve()
  if (finalSavePromise) return finalSavePromise
  const v = videoEl.value
  const p = v ? progressPayload(v) : null
  finalSavePromise = (savePromise || Promise.resolve()).then(() => (p ? postProgress(p) : undefined))
  return finalSavePromise
}
function onTime() {
  const v = videoEl.value
  if (!seekPending.value && !seekDragging.value) seekPos.value = absPos()
  // 播放状态自愈：play/pause 事件若在监听绑定前/元素替换间隙丢失，timeupdate 低频校正
  if (v && isPlaying.value !== (!v.paused && !v.ended)) onPlayState()
  if (!props.preview && v && !doneWatched) {
    const dur = mediaDuration(v)
    const pos = absPos()
    // 短片也必须先看足比例；真正结束仍由 ended 事件处理连播。
    if (isPlaybackComplete(pos, dur)) {
      doneWatched = true
      emit('watched')
    }
  }
  if (!props.preview && Date.now() - Math.max(lastSave, lastSaveAttempt) > 10000) saveNow()   // 成功后 10s 间隔；失败也不密集重试
}
function onSeekInput(e) {
  clearTimeout(keyboardSeekTimer)
  keyboardSeekTarget = null
  // 拖动中：只更新本地预览值（进度条不被播放回调重置），不触发重开会话
  const t = Math.max(0, Math.floor(Number((e.target || {}).value) || 0))
  seekDragging.value = true
  seekPreview.value = t
}
function onSeekCommit(e) {
  // 松手才提交；优先复用当前会话，顺带失焦让方向键回到快捷键
  const raw = Number((e.target || {}).value)
  const t = seekDragging.value ? seekPreview.value
    : Math.max(0, Math.floor(Number.isFinite(raw) ? raw : seekPos.value))
  seekDragging.value = false
  blurPick(e)
  doSeek(t)
}
function doSeek(t) {
  clearTimeout(keyboardSeekTimer)
  keyboardSeekTarget = null
  clearTimeout(seekFallbackTimer)
  t = Math.max(0, Math.min(Math.max(0, decidedDuration.value - 0.25), Math.floor(Number(t) || 0)))
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
  const localTime = reusableSeekTime(videoEl.value, t, mediaStart)
  if (!seekPending.value && localTime !== null) {
    const v = videoEl.value
    try {
      v.currentTime = localTime
      logEvt('seek:reuse', fmt(t))
      const gen = reloadGen
      seekFallbackTimer = setTimeout(() => {
        if (!disposed && gen === reloadGen && (v.seeking || Math.abs(absPos() - t) > 5) && v.readyState < 3) restartSeek(t)
      }, 8000)
      return
    } catch (e) { logEvt('seek:reuse-error', e.message) }
  }
  restartSeek(t)
}
function restartSeek(t) {
  resumePos = t
  seekPending.value = true
  seekPendingSince = Date.now()
  try { videoEl.value && videoEl.value.pause() } catch (err) { /* 忽略 */ }
  sessStatus.value = '正在转码…'
  reload()
}
async function onEnded() {
  if (props.preview) return
  await saveNow()
  emit('watched')
  emit('ended')   // 剧集连播：父级据此切下一集（watched 可能在片尾前 5% 触发，不可用于连播）
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
  if (!props.preview) {
    try {
      await api(`/api/stream/progress?version_id=${props.versionId}${kindSuffix.value}`, { method: 'DELETE' })
    } catch (e) { /* 忽略 */ }
  }
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
  booting.value = false
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
  if (isFull.value) showOverlay()
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
  const cur = keyboardSeekTarget ?? (seekPending.value ? seekPreview.value : absPos())
  const dur = Number(decidedDuration.value) || 0
  let t = Math.floor(cur + delta)
  if (t < 0) t = 0
  if (dur > 0 && t > Math.floor(dur) - 1) t = Math.floor(dur) - 1
  if (t === Math.floor(cur)) return
  logEvt('kbd:seek', fmt(t))
  keyboardSeekTarget = t
  seekPreview.value = t
  seekDragging.value = true
  clearTimeout(keyboardSeekTimer)
  keyboardSeekTimer = setTimeout(() => { seekDragging.value = false; doSeek(t) }, 180)
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
  // 设置里的下拉、折叠项与按钮保留原生键盘行为。
  if (e.key !== 'Escape' && t.closest?.('.pd-set')) return
  if (e.code === 'Space' && !typing && !['button', 'summary', 'a'].includes(tag)) {
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
  if (isPlaying.value) booting.value = false   // direct 起播早于 bindVideo 时也要收浮层
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
  observePlayerSize()
  if (!props.preview) {
    try {
      const p = await api(`/api/stream/progress?version_id=${props.versionId}${kindSuffix.value}`)
      const pos = Number(p.position) || 0
      let dur = Number(p.duration) || 0
      // 旧版本用 HLS 增长清单时长（如 30s）写坏过存档：dur < pos 视为不可信，按未看完处理
      if (dur > 0 && dur < pos) dur = 0
      if (pos > 15 && !isPlaybackComplete(pos, dur)) {
        resumePos = pos
        resumeOffer.value = p.position_text || fmt(pos)
        // 弹窗打开即计时：10s 内未点任一按钮 = 同意续播，自动消条（direct 缺 #t 会在到点时补跳）
        clearResumeTimer()
        resumeTimer = setTimeout(() => { resumeTimer = 0; resumePlay() }, 10000)
      }
    } catch (e) { /* 无断点直接播 */ }
  }
  if (disposed) return
  await reload()
  // 进度请求/reload 期间可能已关窗：此时不能再绑监听/起定时器（onUnmounted 已经清理过）
  if (disposed) return
  bindVideo(videoEl.value)
  document.addEventListener('fullscreenchange', onFullChange)
  document.addEventListener('click', onDocClick)
  if (!props.preview) window.addEventListener('beforeunload', saveNow)
  window.addEventListener('keydown', onKeydown)
  try {
    // 调试口收进命名空间（评审 B8/R14-D4）；__jzPlayerDebug 保留兼容
    window.__jzPlayer = { snapshot: () => debugSnapshot(), hls: () => hls,
                          ass: () => assInstance(), pgs: () => pgsInstance() }
    window.__jzPlayerDebug = () => debugSnapshot()
  } catch (e) { /* 忽略 */ }
  bufTimer = setInterval(() => {
    try {
      const v = videoEl.value
      const offset = method.value === 'direct' ? 0 : startOffset.value
      bufferedSections.value = bufferedRanges().map(([start, end]) => [start + offset, end + offset])
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
  loadedmetadata: restorePlaybackRate,
  ratechange: syncPlaybackRate,
  waiting: () => logEvt('video:waiting', 't=' + fmtT(videoEl.value)),
  stalled: () => logEvt('video:stalled', 't=' + fmtT(videoEl.value)),
  seeking: () => logEvt('video:seeking', 'to=' + fmtT(videoEl.value)),
  seeked: () => { clearTimeout(seekFallbackTimer); logEvt('video:seeked', 't=' + fmtT(videoEl.value)) },
  emptied: () => logEvt('video:emptied', ''),
  suspend: () => logEvt('video:suspend', ''),
  abort: () => logEvt('video:abort', ''),
  canplay: () => logEvt('video:canplay', ''),
}
function bindVideo(v) {
  if (!v) return
  restorePlaybackRate()
  v.addEventListener('timeupdate', onTime)
  attachSubtitleVideo(v)
  v.addEventListener('pause', saveNow)
  v.addEventListener('pause', onPlayState)
  v.addEventListener('pause', onPausePing)
  v.addEventListener('ended', onEnded)
  v.addEventListener('playing', onPlayingHide)
  v.addEventListener('play', onPlayState)
  for (const [ev, h] of Object.entries(_evtHandlers)) v.addEventListener(ev, h)
  muted.value = !!v.muted
  volume.value = Number(v.volume ?? 1)
  // 起播竞态：reload 里的 tryPlay/autoplay 的 play 事件可能早于本函数绑定监听，
  // 绑定后立即同步一次，否则播放中按钮仍显示 ▶（用户反馈：播放/暂停图标一样）
  onPlayState()
}
function unbindVideo(v) {
  if (!v) return
  v.removeEventListener('timeupdate', onTime)
  detachSubtitleVideo(v)
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
    recoverCount, engine, playing: isPlaying.value,
    freeze: !!freezeFrame.value, seekPending: seekPending.value,
  }
  try {
    if (v) {
      info.el = { ready: v.readyState, net: v.networkState, paused: v.paused,
        rate: v.playbackRate,
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
  Object.assign(info, subtitleDebugInfo())
  info.drops = { hint: dropHint.value, fired: dropHintFired,
    win: { at: dropGuard.at, dropped: dropGuard.dropped, total: dropGuard.total } }
  info.events = evtLog.slice(-25)
  return info
}
async function recoverStream(forceElement = false) {
  // 同会话自救（不杀转码进程，分片继续产）：
  // 常规：重建 hls 实例；媒体级致命错误(forceElement)或第 3 次：连 <video> 元素一起换新。
  if (disposed) return
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
      if (disposed || !nv) return
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
  playerResizeObserver?.disconnect()
  clearTimeout(surfaceClickTimer)
  clearTimeout(toastTimer)
  clearTimeout(keyboardSeekTimer)
  clearTimeout(seekFallbackTimer)
  // Vue 在 unmount 阶段先 setRef(null) 再跑 onUnmounted：videoEl 在 onUnmounted 已为 null，
  // 最终进度必须在这里上报（用户 2026-09：拖进度后关闭，重开回到旧断点）。
  // 走 closeStream 关闭时父级已先调过 saveFinal（同一 Promise）；这里覆盖路由切换等直接卸载。
  saveFinal()
  const v = videoEl.value
  if (v) {
    // 显式停掉媒体元素：节点被移出文档后浏览器不显示画面但会继续放音频（HTML 规范），
    // 晚到的 hls 挂载正是“关窗后海报墙出声”的入口，这里先断源再让 destroyHls 收尾。
    try { v.pause() } catch (e) { /* 忽略 */ }
    try { v.removeAttribute('src') } catch (e) { /* 忽略 */ }
    try { v.load() } catch (e) { /* 忽略 */ }
  }
  try { unbindVideo(videoEl.value) } catch (e) { /* 忽略 */ }
})
onUnmounted(() => {
  // 先作废在飞 reload：晚到的 decide/sessions 响应不得再 startPing/挂 hls（音频泄漏根因）
  disposed = true
  reloadGen += 1
  wantPlaying = false
  if (bootCtrl) { try { bootCtrl.abort() } catch (e) { /* 忽略 */ } bootCtrl = null }
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
  dispose()
})
// 父级关窗时先取最终存档 Promise：落库后再刷新详情页「上次看到」（卸载后 emit 会被 Vue 丢弃）
defineExpose({ saveFinal })
</script>
<style scoped>
.player-mask { background: rgba(5,5,8,.86); backdrop-filter: blur(12px); padding: 16px; box-sizing: border-box; }
.player-dlg {
  --player-accent: var(--jz-accent, #e50914);
  color: var(--jz-text); background: var(--jz-bg); border: 1px solid #ffffff15; border-radius: var(--jz-radius-dialog);
  width: min(1120px, 94vw); max-height: 94vh; max-height: 94dvh; overflow: auto;
  box-shadow: 0 24px 100px #0009; box-sizing: border-box;
}
.pd-head { display: flex; gap: 16px; align-items: center; padding: 16px 20px; }
.pd-heading { flex: 1; min-width: 0; }
.pd-eyebrow { display: block; font-size: .625rem; letter-spacing: .12em; color: var(--jz-text-faint); line-height: 1.4; margin-bottom: 4px; }
.pd-head h3 { margin: 0; font-size: 1rem; font-weight: 500; line-height: 1.5; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.quality-badge { padding: 3px 7px; border: 1px solid #ffffff25; border-radius: 5px; font-size: .625rem; font-weight: 600; color: var(--jz-text-dim); }
.pd-close { color: var(--jz-text-dim); }
.pv-wrap { position: relative; background: #000; overflow: hidden; width: 100%; }
.pv-wrap.has-bar { --pvb: 84px; }
.pv-wrap.hide-cursor { cursor: none; }
.player-video, .freeze-frame { position: absolute; top: 0; left: 0; width: 100%; height: calc(100% - var(--pvb)); object-fit: contain; background: #000; display: block; }
.freeze-frame { pointer-events: none; }
.stage-state { position: absolute; inset: 0 0 var(--pvb); display: flex; flex-direction: column; gap: 12px;
  align-items: center; justify-content: center; background: #0005; font-size: .8125rem; z-index: 4; }
.stage-state strong { font-weight: 500; font-size: .9375rem; }
.stage-state > span { color: var(--jz-text-dim); font-size: .75rem; }
.seek-ov { background: #08080b99; }
.pause-state { pointer-events: none; background: linear-gradient(transparent, #0003); }
.center-play { pointer-events: auto; display: grid; place-items: center; width: 76px; height: 76px; padding: 0 0 0 3px;
  border: 1px solid #ffffff40; border-radius: 50%; color: var(--jz-on-accent); background: #16161a99; backdrop-filter: blur(8px);
  box-shadow: 0 4px 28px #0005; transition: background .2s, transform .2s; }
.center-play:hover { transform: scale(1.06); background: #ffffff30; }
.pv-ctl { position: absolute; left: 0; right: 0; bottom: 0; height: 84px; padding: 4px 20px 12px;
  display: flex; flex-direction: column; box-sizing: border-box; background: var(--jz-bg); z-index: 6; }
.ctl-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 40px; }
.ctl-group, .volume-group { display: flex; align-items: center; gap: 4px; min-width: 0; }
.ctl-options { flex: none; gap: 6px; }
.volume-group { margin-left: 4px; }
.ctl-time { display: flex; gap: 8px; margin-left: 12px; font-size: .75rem; font-variant-numeric: tabular-nums; white-space: nowrap; color: var(--jz-text-faint); }
.ctl-time > span:first-child { color: var(--jz-text); }
.time-divider { color: #626267; }
.ctl-vol { width: 68px; height: 4px; margin: 0 8px 0 2px; padding: 0; border: 0; border-radius: 4px;
  background: linear-gradient(to right, #ddd var(--volume), #ffffff30 var(--volume)); appearance: none; cursor: pointer; }
.ctl-vol::-webkit-slider-thumb { appearance: none; width: 10px; height: 10px; background: var(--jz-text); border: 0; border-radius: 50%; }
.ctl-vol::-moz-range-thumb { width: 10px; height: 10px; background: var(--jz-text); border: 0; border-radius: 50%; }
.rate-btn { height: var(--jz-control-current); min-width: 44px; padding: 0 8px; border: 0; border-radius: var(--jz-radius-s); color: var(--jz-text); background: transparent;
  font-size: .8125rem; font-weight: 600; font-variant-numeric: tabular-nums; }
.rate-btn span { margin-left: 2px; font-weight: 400; color: var(--jz-text-dim); }
.rate-btn:hover { background: #ffffff12; }
.pv-top { display: none; }
.pv-heading { flex: 1; min-width: 0; }
.pv-title { display: block; font-size: 1rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.resume-bar { position: absolute; left: 20px; bottom: calc(var(--pvb) + 18px); z-index: 5; display: flex; align-items: center; flex-wrap: wrap;
  gap: 8px; max-width: calc(100% - 40px); box-sizing: border-box; padding: 10px 12px; border: 1px solid #ffffff20; border-radius: 10px;
  background: #1c1c20ee; box-shadow: 0 6px 20px #0004; font-size: .75rem; color: var(--jz-text-dim); }
.resume-bar > span { margin-right: 8px; }
.resume-bar strong { color: var(--jz-on-accent); font-weight: 500; font-variant-numeric: tabular-nums; }
.player-toast { position: absolute; left: 50%; bottom: calc(var(--pvb) + 20px); transform: translateX(-50%); z-index: 7;
  width: max-content; max-width: calc(100% - 40px); padding: 9px 14px; border: 1px solid #ffffff18; border-radius: 9px;
  color: var(--jz-text); background: #202024ed; font-size: .75rem; line-height: 1.6; box-sizing: border-box; overflow-wrap: anywhere; pointer-events: none; }
.status-card { display: flex; flex-direction: column; align-items: center; gap: 12px; width: min(380px, calc(100% - 40px));
  max-height: calc(100% - 24px); overflow: auto; box-sizing: border-box; padding: 24px; border: 1px solid #ffffff20;
  border-radius: var(--jz-radius-dialog); background: #1c1c20f2; text-align: center; box-shadow: 0 12px 40px #0006; }
.status-card strong, .status-card > .player-action, .prompt-actions { flex-shrink: 0; }
.status-card strong { font-size: 1rem; font-weight: 500; }
.status-card p { min-height: 0; overflow: auto; margin: 0; color: var(--jz-text-dim); font-size: .8125rem; line-height: 1.7; overflow-wrap: anywhere; }
.drop-prompt { position: absolute; left: 50%; top: calc((100% - var(--pvb)) / 2); transform: translate(-50%, -50%); z-index: 8; }
.prompt-actions { display: flex; align-items: center; justify-content: center; flex-wrap: wrap; gap: 8px; }
.drop-count { margin-left: 4px; color: var(--jz-text-faint); font-variant-numeric: tabular-nums; }
/* 窗口态为字幕保留画面区；全屏控件悬浮，视频铺满容器。 */
.pv-wrap:fullscreen { padding-top: 0 !important; width: 100vw; height: 100vh; --pvb: 0px; }
.pv-wrap:fullscreen .pv-ctl { height: 108px; padding: 24px 32px 16px; background: linear-gradient(transparent, #000c);
  opacity: 0; pointer-events: none; transition: opacity .2s; }
.pv-wrap:fullscreen .pv-ctl.show { opacity: 1; pointer-events: auto; }
.pv-wrap:fullscreen .pv-top { display: flex; gap: 20px; align-items: center; position: absolute; inset: 0 0 auto;
  padding: 24px 32px 44px; background: linear-gradient(#000b, transparent); opacity: 0; pointer-events: none; transition: opacity .2s; z-index: 5; }
.pv-wrap:fullscreen .pv-top.show { opacity: 1; pointer-events: auto; }
.pv-wrap:fullscreen .resume-bar, .pv-wrap:fullscreen .player-toast { bottom: 118px; }
@media (max-width: 700px) {
  .player-mask { padding: 10px; }
  .player-dlg { width: 100%; border-radius: 12px; }
  .pd-head { padding: 12px 14px; gap: 10px; }
  .pd-head h3 { font-size: .875rem; }
  .ctl-vol { display: none; }
  .volume-group { margin-left: 0; }
  .ctl-time { margin-left: 6px; gap: 5px; font-size: .6875rem; }
  .pv-ctl { padding-left: 12px; padding-right: 12px; }
  .ctl-row { gap: 4px; }
  .ctl-group, .ctl-options { gap: 2px; }
  .center-play { width: 60px; height: 60px; }
  .resume-bar { left: 12px; max-width: calc(100% - 24px); gap: 6px; padding: 8px; }
  .status-card { padding: 14px; gap: 8px; }
  .status-card > svg { display: none; }
  .pv-wrap:fullscreen .pv-ctl { padding-left: 16px; padding-right: 16px; }
  .pv-wrap:fullscreen .pv-top { padding: 16px 20px 30px; }
}
/* Six 44px controls fit at 320px. Time owns a separate line; speed stays in settings. */
@media (max-width: 600px) {
  .pv-wrap.has-bar:not(:fullscreen) { --pvb: 104px; }
  .pv-ctl { height: 104px; padding: 4px 8px 12px; }
  .ctl-row { position: relative; padding-top: 16px; min-height: 44px; }
  .ctl-time { position: absolute; top: 0; left: 0; margin: 0; font-size: .6875rem; }
  .rate-btn { display: none; }
  .quality-badge { display: none; }
  .pv-wrap:fullscreen .pv-ctl { height: 128px; padding-left: 8px; padding-right: 8px; }
}

@media (prefers-reduced-motion: reduce) {
  .center-play, .pv-wrap:fullscreen .pv-ctl, .pv-wrap:fullscreen .pv-top { transition: none; }
}
</style>
