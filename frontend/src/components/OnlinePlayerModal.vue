<template>
  <div class="dlg-mask online-player-mask" @click.self="requestClose">
    <section ref="dialogEl" class="player-dlg online-player-dlg" role="dialog" aria-modal="true"
      :aria-label="title || '在线视频播放器'">
      <header class="online-player-heading">
        <div class="online-player-title">
          <span>在线点播 · 原样播放</span>
          <h3>{{ title || '在线视频' }}</h3>
        </div>
        <span v-if="transport" class="online-transport">{{ transport.toUpperCase() }}</span>
        <JzButton variant="ghost" icon-only class="player-icon-btn" aria-label="关闭播放器" title="关闭（Esc）" @click="requestClose">
          <PlayerIcon name="close" />
        </JzButton>
      </header>
      <div ref="stageEl" class="online-player-stage" :class="{ 'controls-hidden': !controlsVisible }"
        @pointermove="pointerMove" @pointerdown="pointerDown" @focusin="showControls"
        @click="surfaceClick" @dblclick="surfaceDoubleClick">
        <div class="online-player-picture">
          <video :key="videoKey" ref="videoEl" class="online-player-video" playsinline webkit-playsinline preload="metadata"
            disableremoteplayback controlslist="noremoteplayback" x-webkit-airplay="deny"
            @loadedmetadata="metadata" @durationchange="updateDuration" @timeupdate="timeUpdate"
            @playing="playing" @play="playState" @pause="playState" @volumechange="volumeState"
            @ratechange="rateState" @seeked="seeked" @ended="ended" @error="mediaError" />
          <div class="online-player-corner" :inert="!controlsVisible" :aria-hidden="!controlsVisible"
            @click.stop @pointerdown.stop @dblclick.stop>
            <JzButton variant="ghost" icon-only class="player-icon-btn" :aria-label="isFull ? '退出全屏' : '全屏'"
              :title="isFull ? '退出全屏（Esc）' : '全屏（双击画面）'" @click="toggleFull">
              <PlayerIcon :name="isFull ? 'exitFullscreen' : 'fullscreen'" />
            </JzButton>
          </div>
          <div v-if="error" class="online-player-state online-player-error" role="alert" @click.stop @dblclick.stop>
            <PlayerIcon name="info" :size="30" />
            <strong>播放遇到问题</strong>
            <p>{{ error }}</p>
            <JzButton variant="primary" @click="retry">重新加载</JzButton>
          </div>
          <div v-else-if="booting || seeking" class="online-player-state" role="status" aria-live="polite">
            <Spinner :size="36" />
            <strong>{{ seeking ? '正在跳转' : '正在准备播放' }}</strong>
            <span>正在加载视频，请稍候</span>
          </div>
          <div v-else-if="needGesture || paused" class="online-player-state online-player-paused" @dblclick.stop>
            <JzButton variant="ghost" icon-only class="online-center-play" :aria-label="needGesture ? '开始播放' : '继续播放'"
              @click.stop="togglePlay"><PlayerIcon name="play" :size="38" /></JzButton>
            <span v-if="needGesture">点击开始播放</span>
          </div>
          <p v-if="hint && !error && !booting" class="online-player-toast" role="status">{{ hint }}</p>
        </div>
        <div class="online-player-controls pv-ctl" :inert="isFull && !controlsVisible" :aria-hidden="isFull && !controlsVisible"
          @click.stop @pointerdown.stop @dblclick.stop>
          <PlayerSeekbar :value="dragging ? seekPreview : position" :duration="duration" :buffered="buffered"
            :disabled="!canSeek || booting || !!error" :preview-hint-text="''" :preview-container="stageEl"
            @input="seekInput" @commit="seekCommit" @cancel="dragging = false" @active="previewActive = $event" />
          <div class="online-player-control-row">
            <span class="online-player-time"><span>{{ fmt(dragging ? seekPreview : position) }}</span><span>/</span><span>{{ fmt(duration) }}</span></span>
            <div class="online-player-buttons">
              <JzButton variant="ghost" icon-only class="player-icon-btn" :aria-label="paused ? '播放' : '暂停'"
                :title="paused ? '播放（空格）' : '暂停（空格）'" :disabled="booting || !!error" @click="togglePlay">
                <PlayerIcon :name="paused ? 'play' : 'pause'" :size="26" />
              </JzButton>
              <JzButton variant="ghost" icon-only class="player-icon-btn" aria-label="快退 10 秒" title="快退 10 秒（←）"
                :disabled="!canSeek || booting || !!error" @click="seekBy(-10)"><PlayerIcon name="rewind" /></JzButton>
              <JzButton variant="ghost" icon-only class="player-icon-btn" aria-label="快进 10 秒" title="快进 10 秒（→）"
                :disabled="!canSeek || booting || !!error" @click="seekBy(10)"><PlayerIcon name="forward" /></JzButton>
              <PlayerVolumeControl ref="volumeControl" :volume="volume" :muted="muted" :open="activePanel === 'volume'"
                :panel-height="panelHeight" :volume-supported="volumeSupported" @toggle="toggleMenu('volume')"
                @open="openVolume" @close="closeMenu" @volume-change="setVolume" @toggle-mute="toggleMute" />
            </div>
            <PlayerQuickControls ref="quickControls" :panel="activePanel" :panel-height="panelHeight" :playback-rate="rate"
              :audios="audios" :audio-idx="audioIndex" :subs="subtitles" :sub-idx="subtitleIndex"
              :show-tracks="audios.length > 1 || subtitles.length > 0" :allow-local-subtitles="false" :allow-subtitle-settings="false"
              @toggle="toggleMenu" @close="closeMenu" @rate-change="setRate" @audio-change="selectAudio" @sub-change="selectSubtitle" />
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from '../api.js'
import { createPlaybackPageLifecycle } from '../playbackLifecycle.js'
import { createOnlinePlaybackClient, createOnlinePlaybackController, createOnlineProgressMemory,
  canRecoverOnlineStartupGap, onlineIdentityKey, validateOnlineIdentity } from '../onlinePlayback.js'
import { applyPlaybackRate, normalizeRate, surfaceClickRevealsOnly } from '../playbackControls.js'
import { fmtTime as fmt } from '../playerLabels.js'
import { supportsMediaVolume } from '../volumeControls.js'
import { registerViewingPlaybackCleanup, refreshViewing } from '../viewing.js'
import { useFocusTrap } from '../useFocusTrap.js'
import { landscapeLockWanted, lockLandscape, orientationEnv, unlockOrientation } from '../screenOrientation.js'
import JzButton from './JzButton.vue'
import PlayerIcon from './PlayerIcon.vue'
import PlayerSeekbar from './PlayerSeekbar.vue'
import PlayerVolumeControl from './PlayerVolumeControl.vue'
import PlayerQuickControls from './PlayerQuickControls.vue'
import Spinner from './Spinner.vue'
import '../player.css'
import '../onlinePlayer.css'

const props = defineProps({ onlineIdentity: { type: Object, required: true }, title: { type: String, default: '' },
  initialPosition: { type: Number, default: 0 }, prepareSession: { type: Function, default: null } })
const emit = defineEmits(['close', 'progress', 'ended', 'session'])
const dialogEl = ref(null), stageEl = ref(null), videoEl = ref(null)
const volumeControl = ref(null), quickControls = ref(null)
const focus = useFocusTrap(ref(true), dialogEl)
const client = createOnlinePlaybackClient({ request: api })
const controller = createOnlinePlaybackController({ client })
const memory = createOnlineProgressMemory()
const descriptor = ref(null), transport = ref(''), engine = ref('')
const videoKey = ref(0), position = ref(0), duration = ref(0), buffered = ref([])
const paused = ref(true), booting = ref(true), seeking = ref(false), needGesture = ref(false), error = ref('')
const volume = ref(1), muted = ref(false), rate = ref(1), volumeSupported = ref(true)
const audios = ref([]), subtitles = ref([]), audioIndex = ref(0), subtitleIndex = ref(-1)
const activePanel = ref(''), panelHeight = ref(240), isFull = ref(false), hint = ref('')
const dragging = ref(false), seekPreview = ref(0), previewActive = ref(false), pointerActive = ref(true)
const firstFrame = ref(false), startupGapRecoveries = ref(0)
const canSeek = computed(() => !!descriptor.value?.seekable && duration.value > 0)
const controlsVisible = computed(() => pointerActive.value || paused.value || booting.value || seeking.value
  || !!activePanel.value || previewActive.value || needGesture.value || !!error.value)
let identity = null, hls = null, hlsClass = null, generation = 0, disposed = false, mounted = false
let bootController = null, pendingStart = 0, pauseWanted = false, endedOnce = false
let resumeApplied = false
let frameHandle = null, frameVideo = null, resizeObserver = null
let hideTimer = null, hintTimer = null, seekTimer = null, startupTimer = null, surfaceTimer = null
let overlayAtPointerDown = true, presentedFrames = 0, networkErrors = 0, renewals = 0, lastRenewal = -Infinity
let diagnosing = false
const errors = []
const identityKey = computed(() => { try { return onlineIdentityKey(props.onlineIdentity) } catch { return '' } })

function current(gen, video = videoEl.value) {
  return !disposed && !pageLifecycle.isAway() && gen === generation && !!video && video === videoEl.value
}
function cleanCategory(value, fallback = 'online_playback_failed') {
  return typeof value === 'string' && /^[a-zA-Z0-9_-]{1,80}$/.test(value) ? value : fallback
}
function record(category) { if (errors.length < 32) errors.push(cleanCategory(category)) }
function notify(text) {
  hint.value = text
  clearTimeout(hintTimer)
  hintTimer = setTimeout(() => { hint.value = '' }, 6000)
}
function capture() {
  if (!identity) return null
  const value = Number(videoEl.value?.currentTime)
  const actual = descriptor.value && firstFrame.value && Number.isFinite(value) ? value : position.value
  return { identity, position: Math.max(0, actual || 0), duration: duration.value,
    // pause() updates the element before its queued DOM event updates refs.
    // Capture the synchronous user/load intent, including startup and errors.
    paused: pauseWanted, ended: endedOnce, first_frame: firstFrame.value }
}
function save(snapshot = capture(), reason = 'tick') {
  if (!snapshot) return
  const data = { position: snapshot.position, duration: snapshot.duration, paused: snapshot.paused,
    completed: !!snapshot.ended, first_frame: !!snapshot.first_frame, reason }
  // Persistent progress keeps the actual ending position. Only the in-memory
  // replay point resets, so a completed film cannot become a false zero seek.
  if (snapshot.first_frame) memory.save(snapshot.identity, { ...data, position: snapshot.ended ? 0 : snapshot.position })
  emit('progress', { identity: snapshot.identity, ...data })
}
function clearStartup() { clearTimeout(startupTimer); startupTimer = null }
function armStartup(video, gen) {
  clearStartup()
  startupTimer = setTimeout(() => { if (current(gen, video) && !firstFrame.value) fail('online_startup_timeout', '视频加载超时，请检查连接后重试') }, 60000)
}
function detachMedia() {
  clearStartup()
  clearTimeout(seekTimer)
  startupTimer = seekTimer = null
  if (frameHandle !== null && frameVideo?.cancelVideoFrameCallback) frameVideo.cancelVideoFrameCallback(frameHandle)
  frameHandle = null; frameVideo = null
  if (hls) { hls.destroy(); hls = null }
  const video = videoEl.value
  if (video) {
    try { video.pause(); video.removeAttribute('src'); video.load() } catch { /* Element is already detached. */ }
  }
  seeking.value = false
  paused.value = true
}
function stop() {
  generation++
  bootController?.abort(); bootController = null
  detachMedia()
  descriptor.value = null
  booting.value = false
  return controller.stop()
}
function fail(category, message) {
  save(undefined, 'error')
  record(category)
  error.value = message
  void stop()
}
function errorMessage(problem) {
  return ({
    provider_not_ready: '视频源适配器尚未启用，请稍后重试',
    provider_resolution_failed: '视频源暂时无法提供播放地址，请稍后重试',
    invalid_provider_response: '视频源返回的资料格式不受支持，请选择其他来源',
    invalid_provider_result: '视频源返回的播放描述无效，请选择其他来源',
    provider_media_unsupported: '此线路不支持直接播放，请关闭播放器并选择其他线路',
    provider_not_found: '此内容已从视频源移除，请关闭播放器后重新搜索',
    provider_line_required: '请选择明确的播放线路后再播放',
    provider_line_not_found: '此线路已变化，请关闭播放器并重新选择线路',
    provider_episode_not_found: '此分集已变化或不再提供，请关闭播放器后重新选集',
    provider_timeout: '连接视频源超时，请稍后重试',
    provider_rate_limited: '视频源暂时限制了请求次数，请稍后重试',
    provider_auth_failed: '视频源要求上游认证，请检查来源配置或选择其他来源',
    provider_network_error: '无法连接视频源，请检查其网络设置后重试',
    provider_upstream_error: '视频源暂时无法提供播放地址，请稍后重试',
    provider_catalog_busy: '当前在线查询较多，请稍后重试',
    provider_response_limit: '视频源返回的资料过大，暂时无法读取',
    provider_ambiguous_identity: '视频源的线路或集名重复，请选择其他来源',
    provider_cancelled: '播放地址查询已取消，请重新加载',
    online_source_changed: '视频源配置已变化，请关闭播放器后重新打开详情',
    online_source_disabled: '此视频源已停用，请返回选择其他来源',
    online_children_unavailable: '当前观看模式暂不支持在线内容',
    viewing_session_required: '请重新连接服务后再播放',
    viewing_session_invalid: '当前设备会话已失效，请重新连接服务',
    online_access_revoked: '观看权限已变化，请重新连接服务',
    session_limit: '当前设备或来源的播放会话已满，请先关闭其他播放',
    request_limit: '当前取流请求较多，请稍后重试',
    byte_budget_exhausted: '本次播放已达到流量限额，请重新加载',
  }[problem?.code] || '暂时无法加载视频，请检查连接后重试')
}
function watchFrames(video, gen) {
  if (!video.requestVideoFrameCallback) return
  frameVideo = video
  const frame = () => {
    if (!current(gen, video)) return
    firstFrame.value = true; presentedFrames++
    clearStartup()
    frameHandle = video.requestVideoFrameCallback(frame)
  }
  frameHandle = video.requestVideoFrameCallback(frame)
}
function policy() {
  const retry = () => ({ maxNumRetry: 2, retryDelayMs: 500, maxRetryDelayMs: 1000 })
  return { default: { maxTimeToFirstByteMs: 10000, maxLoadTimeMs: 30000,
    timeoutRetry: retry(), errorRetry: retry() } }
}
async function mountSource(video, result, gen, signal) {
  if (result.transport === 'mp4') {
    engine.value = 'mp4'; video.src = result.media_url
    return
  }
  const native = !!video.canPlayType('application/vnd.apple.mpegurl')
  const safari = /Safari/i.test(navigator.userAgent) && !/Chrome|Chromium|Android|CriOS/i.test(navigator.userAgent)
  if (native && safari) { engine.value = 'native-hls'; video.src = result.media_url; return }
  let Hls
  try { Hls = hlsClass || (hlsClass = (await import('hls.js')).default) }
  catch {
    if (!current(gen, video) || signal.aborted) return
    if (native) { engine.value = 'native-hls'; video.src = result.media_url; return }
    fail('hls_component_unavailable', '播放器组件加载失败，请刷新页面后重试')
    return
  }
  if (!current(gen, video) || signal.aborted) return
  if (!Hls.isSupported()) {
    if (native) { engine.value = 'native-hls'; video.src = result.media_url; return }
    fail('hls_unsupported', '当前设备无法播放此 HLS 视频，请选择其他来源或设备')
    return
  }
  engine.value = 'hls'
  const player = hls = new Hls({ autoStartLoad: false, debug: false, startPosition: pendingStart,
    maxBufferLength: 30, maxMaxBufferLength: 60, backBufferLength: 30, maxBufferSize: 30 * 1024 ** 2,
    nudgeMaxRetry: 1, maxLiveSyncPlaybackRate: 1,
    manifestLoadPolicy: policy(), playlistLoadPolicy: policy(), fragLoadPolicy: policy(), keyLoadPolicy: policy() })
  const active = () => current(gen, video) && hls === player && !signal.aborted
  player.on(Hls.Events.MANIFEST_PARSED, () => {
    if (!active()) return
    updateTracks(); player.startLoad(pendingStart); void attemptPlay()
  })
  player.on(Hls.Events.LEVEL_LOADED, (_event, data) => {
    if (!active()) return
    const total = Number(data.details?.totalduration)
    if (!duration.value && !data.details?.live && Number.isFinite(total) && total > 0) duration.value = total
  })
  player.on(Hls.Events.AUDIO_TRACKS_UPDATED, () => { if (active()) updateTracks() })
  player.on(Hls.Events.SUBTITLE_TRACKS_UPDATED, () => { if (active()) updateTracks() })
  player.on(Hls.Events.ERROR, (_event, data) => {
    if (!active()) return
    const category = cleanCategory(data.details, 'hls_error')
    record(category)
    if (data.details === 'bufferSeekOverHole' && !firstFrame.value && pendingStart <= .5) {
      const jump = /^fragment loaded with buffer holes, seeking from ([0-9.e+-]+) to ([0-9.e+-]+)$/.exec(data.reason || '')
      const observation = { type: data.type, detail: data.details, fatal: data.fatal,
        beforeFirstFrame: true, priorRecoveries: startupGapRecoveries.value, mediaTime: video.currentTime,
        seekFrom: jump ? Number(jump[1]) : null, seekTo: jump ? Number(jump[2]) : null }
      if (canRecoverOnlineStartupGap(observation)) { startupGapRecoveries.value++; return }
      fail('initial_gap_recovery_limit', '视频起点无法稳定播放，请选择其他来源或设备')
      return
    }
    if (data.type === Hls.ErrorTypes.NETWORK_ERROR && data.details !== 'aborted') networkErrors++
    if (!data.fatal && networkErrors < 6) return
    if ([404, 410].includes(Number(data.response?.code)) && renew()) return
    if ([401, 403].includes(Number(data.response?.code))) {
      void refreshViewing().catch(() => {})
      fail('online_access_revoked', '观看权限已变化，请重新连接服务')
    } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
      fail(category, '此设备无法解析或解码视频，请选择其他来源或设备')
    } else {
      fail(category, '视频加载失败，请检查连接后重新加载')
    }
  })
  player.loadSource(result.media_url)
  player.attachMedia(video)
}
async function load({ start = position.value, paused: keepPaused = false, recovering = false } = {}) {
  if (disposed || pageLifecycle.isAway() || !identity) return
  const gen = ++generation
  bootController?.abort()
  const control = bootController = new AbortController()
  detachMedia()
  descriptor.value = null; transport.value = ''; engine.value = ''
  error.value = ''; hint.value = ''; booting.value = true; needGesture.value = false
  firstFrame.value = false; startupGapRecoveries.value = 0; presentedFrames = 0; networkErrors = 0
  audios.value = []; subtitles.value = []; audioIndex.value = 0; subtitleIndex.value = -1
  buffered.value = []; dragging.value = false; endedOnce = false; diagnosing = false; resumeApplied = false
  if (!recovering) { renewals = 0; lastRenewal = -Infinity }
  pendingStart = Math.max(0, Number.isFinite(Number(start)) ? Number(start) : 0)
  position.value = pendingStart; pauseWanted = !!keepPaused; paused.value = !!keepPaused
  videoKey.value++
  await nextTick()
  const video = videoEl.value
  if (!current(gen, video) || control.signal.aborted) return
  video.autoplay = !pauseWanted; video.muted = muted.value
  volumeSupported.value = supportsMediaVolume(video)
  if (volumeSupported.value) video.volume = volume.value
  applyPlaybackRate(video, rate.value)
  watchFrames(video, gen)
  armStartup(video, gen)
  try {
    await api('/api/viewing/session', { signal: control.signal, timeout: 10000 })
    if (!current(gen, video) || control.signal.aborted) return
    const result = await controller.begin(identity, { signal: control.signal })
    if (!current(gen, video) || control.signal.aborted) { await client.release(result.session_id); return }
    descriptor.value = result; transport.value = result.transport
    emit('session', { identity: result.identity, session_id: result.session_id })
    // Acquire the progress writer before there can be a decoded frame. Closing
    // still releases the media lease immediately, even while this bounded claim
    // is pending; that late result must never attach media to a closed player.
    try { await props.prepareSession?.({ identity: result.identity, session_id: result.session_id }) }
    catch { /* Saving history is optional; its failure does not prevent playback. */ }
    if (!current(gen, video) || control.signal.aborted || !descriptor.value) return
    duration.value = Number(result.duration_seconds) || 0
    await mountSource(video, result, gen, control.signal)
    if (!current(gen, video) || control.signal.aborted || !descriptor.value) return
    if (engine.value !== 'hls') void attemptPlay()
  } catch (problem) {
    if (!current(gen, video) || control.signal.aborted || problem?.code === 'online_cancelled') return
    fail(cleanCategory(problem?.code), errorMessage(problem))
  }
}
function renew() {
  // A fresh source that never produced a frame may be missing or rejected by
  // the gateway's protocol policy. Show that startup failure explicitly; a
  // follow-up 404 must not conceal it as a successful session renewal.
  if (!firstFrame.value && Date.now() < Number(descriptor.value?.expires_at || 0) * 1000) return false
  if (renewals >= 1 || Date.now() - lastRenewal < 30000) return false
  renewals++; lastRenewal = Date.now()
  const snapshot = capture()
  save(snapshot)
  void load({ start: snapshot?.position || 0, paused: snapshot?.paused ?? true, recovering: true })
  return true
}
function retry() {
  void load({ start: position.value, paused: pauseWanted, recovering: false })
}
function validEvent(event) { return !disposed && !pageLifecycle.isAway() && descriptor.value && event.currentTarget === videoEl.value }
function updateDuration(event) {
  if (!validEvent(event)) return
  const value = Number(event.currentTarget.duration)
  if (!descriptor.value.duration_seconds && Number.isFinite(value) && value > 0) duration.value = value
}
function metadata(event) {
  if (!validEvent(event)) return
  updateDuration(event); updateTracks()
  const video = event.currentTarget
  if (!resumeApplied && pendingStart > 0 && descriptor.value.seekable) {
    try { video.currentTime = Math.min(pendingStart, duration.value > 0 ? Math.max(0, duration.value - .1) : pendingStart) } catch { /* HLS startPosition handles a not-yet-seekable element. */ }
  }
  resumeApplied = true
  applyPlaybackRate(video, rate.value)
  if (pauseWanted) { video.pause(); paused.value = true; booting.value = false; clearStartup() }
  else void attemptPlay()
}
function timeUpdate(event) {
  if (!validEvent(event)) return
  const video = event.currentTarget
  if (firstFrame.value && Number.isFinite(video.currentTime) && !dragging.value) position.value = Math.max(0, video.currentTime)
  buffered.value = Array.from({ length: video.buffered.length }, (_, i) => [video.buffered.start(i), video.buffered.end(i)])
  if (firstFrame.value) save()
}
function playing(event) {
  if (!validEvent(event)) return
  paused.value = false; booting.value = false; needGesture.value = false; seeking.value = false
  if (!event.currentTarget.requestVideoFrameCallback) { firstFrame.value = true; clearStartup() }
  clearTimeout(seekTimer)
  showControls()
}
function playState(event) {
  if (!validEvent(event)) return
  paused.value = event.currentTarget.paused
  // Media events report observed state; teardown, buffering and failures must
  // not overwrite the intent set synchronously by controls or a new load.
  if (event.type === 'pause' && !booting.value && !event.currentTarget.error && !event.currentTarget.ended) save(undefined, 'pause')
}
async function attemptPlay() {
  const video = videoEl.value, gen = generation
  if (!current(gen, video) || !descriptor.value) return
  if (pauseWanted) { video.pause(); paused.value = true; booting.value = false; clearStartup(); return }
  if (!firstFrame.value && startupTimer === null) armStartup(video, gen)
  try {
    await video.play()
    if (current(gen, video)) { paused.value = false; needGesture.value = false }
  } catch (problem) {
    if (!current(gen, video) || problem?.name === 'AbortError') return
    if (problem?.name === 'NotAllowedError') { clearStartup(); booting.value = false; needGesture.value = true; paused.value = true; return }
    fail('online_decode_unsupported', '此设备无法解析或解码视频，请选择其他来源或设备')
  }
}
function togglePlay() {
  if (error.value) return
  const video = videoEl.value
  if (endedOnce) { void load({ start: 0, paused: false }); return }
  if (!video || !descriptor.value) return
  if (video.paused) { pauseWanted = false; void attemptPlay() }
  else { pauseWanted = true; video.pause() }
  showControls()
}
function seekInput(event) { dragging.value = true; seekPreview.value = Number(event.target.value) || 0; showControls() }
function seekCommit(event) {
  const target = dragging.value ? seekPreview.value : Number(event.target.value)
  dragging.value = false; doSeek(target)
}
function doSeek(value) {
  if (!canSeek.value || !descriptor.value || error.value) return
  const target = Math.min(Math.max(0, duration.value - .1), Math.max(0, Number(value) || 0))
  try {
    videoEl.value.currentTime = target; position.value = target; pendingStart = target; seeking.value = true
    clearTimeout(seekTimer)
    const gen = generation
    seekTimer = setTimeout(() => { if (current(gen) && seeking.value) fail('online_seek_timeout', '跳转暂未完成，请重新加载或选择其他来源') }, 10000)
    save(undefined, 'seek')
  } catch { notify('当前设备暂时无法跳转到此位置') }
}
function seekBy(change) { doSeek(position.value + change); showControls() }
function seeked(event) {
  if (!validEvent(event)) return
  clearTimeout(seekTimer); seeking.value = false; timeUpdate(event)
}
function ended(event) {
  if (!validEvent(event) || endedOnce || booting.value) return
  position.value = Number.isFinite(event.currentTarget.currentTime) ? event.currentTarget.currentTime : position.value
  endedOnce = true; pauseWanted = true; paused.value = true; save(undefined, 'ended')
  const endingIdentity = identity
  void stop(); emit('ended', endingIdentity)
}
async function mediaError(event) {
  if (!validEvent(event) || diagnosing) return
  const video = event.currentTarget, gen = generation, code = Number(video.error?.code)
  if (code === 3) { fail('online_decode_unsupported', '此设备无法解析或解码视频，请选择其他来源或设备'); return }
  if (engine.value === 'hls') return // The HLS error event has the response status and bounded retry context.
  diagnosing = true
  const diagnostic = new AbortController()
  const outer = bootController?.signal
  const abort = () => diagnostic.abort()
  const timer = setTimeout(abort, 5000)
  if (outer?.aborted) abort()
  else outer?.addEventListener('abort', abort, { once: true })
  try {
    const response = await fetch(descriptor.value.media_url, { method: 'HEAD', credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: diagnostic.signal })
    if (!current(gen, video)) return
    if ([404, 410].includes(response.status)) {
      if (renew()) return
      fail('online_session_expired', '播放会话已失效，请重新加载')
      return
    }
    if ([401, 403].includes(response.status)) { void refreshViewing().catch(() => {}); fail('online_access_revoked', '观看权限已变化，请重新连接服务'); return }
    if (response.ok && code === 4) { fail('online_decode_unsupported', '此设备无法解析或解码视频，请选择其他来源或设备'); return }
  } catch { /* A media network error remains local to this online session. */ }
  finally { clearTimeout(timer); outer?.removeEventListener('abort', abort); if (gen === generation) diagnosing = false }
  if (current(gen, video)) fail('online_media_network_error', '视频加载失败，请检查连接后重新加载')
}
function setRate(value) { rate.value = normalizeRate(value); applyPlaybackRate(videoEl.value, rate.value); showControls() }
function rateState(event) { if (validEvent(event)) rate.value = normalizeRate(event.currentTarget.playbackRate) }
function setVolume(value) {
  const video = videoEl.value
  if (!video || !volumeSupported.value) return
  try { video.volume = Math.max(0, Math.min(1, Number(value) || 0)); video.muted = video.volume === 0; volume.value = video.volume; muted.value = video.muted }
  catch { volumeSupported.value = false }
}
function toggleMute() { const video = videoEl.value; if (video) { video.muted = !video.muted; muted.value = video.muted } }
function volumeState(event) { if (event.currentTarget === videoEl.value) { volume.value = event.currentTarget.volume; muted.value = event.currentTarget.muted } }
function updateTracks() {
  const video = videoEl.value
  const audioTracks = hls?.audioTracks || Array.from(video?.audioTracks || [])
  const textTracks = hls?.subtitleTracks || Array.from(video?.textTracks || []).filter(track => ['subtitles', 'captions'].includes(track.kind))
  audios.value = audioTracks.map(track => ({ title: String(track.name || track.label || ''), lang: String(track.lang || track.language || '') }))
  subtitles.value = textTracks.map(track => ({ title: String(track.name || track.label || ''), lang: String(track.lang || track.language || ''), codec: 'vtt' }))
  if (hls) { audioIndex.value = Math.max(0, hls.audioTrack); subtitleIndex.value = hls.subtitleDisplay ? hls.subtitleTrack : -1 }
}
function selectAudio(index) {
  if (hls) { hls.audioTrack = index; audioIndex.value = index; return }
  const tracks = Array.from(videoEl.value?.audioTracks || [])
  tracks.forEach((track, i) => { track.enabled = i === index }); audioIndex.value = index
}
function selectSubtitle(index) {
  if (hls) { hls.subtitleTrack = index; hls.subtitleDisplay = index >= 0 }
  else Array.from(videoEl.value?.textTracks || []).filter(track => ['subtitles', 'captions'].includes(track.kind))
    .forEach((track, i) => { track.mode = i === index ? 'showing' : 'disabled' })
  subtitleIndex.value = index
}
function showControls() {
  pointerActive.value = true; clearTimeout(hideTimer)
  hideTimer = setTimeout(() => { pointerActive.value = false }, 3000)
}
function pointerMove(event) { if (event.pointerType === 'mouse') showControls() }
function pointerDown(event) { if (event.target.closest('button, input, select, .volume-control, .player-quick-controls')) return; overlayAtPointerDown = controlsVisible.value; showControls() }
function surfaceClick(event) {
  if (event.target.closest('button, input, select, .online-player-controls, .online-player-state, .online-player-corner')) return
  const reveal = surfaceClickRevealsOnly(overlayAtPointerDown)
  clearTimeout(surfaceTimer)
  surfaceTimer = setTimeout(() => { if (!reveal) togglePlay() }, 220)
}
function surfaceDoubleClick(event) {
  if (event.target.closest('button, input, select, .online-player-controls, .online-player-state, .online-player-corner')) return
  clearTimeout(surfaceTimer); void toggleFull()
}
function toggleMenu(panel) { activePanel.value = activePanel.value === panel ? '' : panel; showControls() }
function openVolume() { if (!activePanel.value || activePanel.value === 'volume') { activePanel.value = 'volume'; showControls() } }
function closeMenu(options = {}) {
  const previous = activePanel.value; activePanel.value = ''; showControls()
  if (options.restoreFocus !== false) { if (previous === 'volume') volumeControl.value?.focus(); else if (previous) quickControls.value?.focus(previous) }
}
function documentClick(event) { if (activePanel.value && !event.target.closest('.volume-control, .player-quick-controls')) closeMenu({ restoreFocus: false }) }
async function toggleFull() {
  closeMenu({ restoreFocus: false })
  try {
    if (document.fullscreenElement) { await document.exitFullscreen(); unlockOrientation() }
    else if (stageEl.value?.requestFullscreen) {
      await stageEl.value.requestFullscreen()
      if (landscapeLockWanted(orientationEnv())) await lockLandscape()
    } else notify('当前设备不支持网页全屏，可横屏观看')
  } catch { notify('暂时无法进入全屏，可横屏观看') }
}
function fullChange() { isFull.value = document.fullscreenElement === stageEl.value; if (!isFull.value) unlockOrientation(); showControls(); measure() }
function measure() { panelHeight.value = Math.max(100, Math.min(300, (stageEl.value?.clientHeight || 340) - 108)) }
function keydown(event) {
  if (!focus.isTop() || disposed || pageLifecycle.isAway() || event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return
  const typing = event.target.matches?.('input, select, textarea, [contenteditable="true"]')
  if (event.key === 'Escape') {
    if (activePanel.value) { event.preventDefault(); closeMenu(); return }
    if (!document.fullscreenElement) { event.preventDefault(); requestClose() }
    return
  }
  if (typing || activePanel.value) return
  if (event.code === 'Space' && !event.target.closest('button')) { event.preventDefault(); togglePlay() }
  else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); seekBy(event.key === 'ArrowLeft' ? -10 : 10) }
  else if (event.key.toLowerCase() === 'm') { event.preventDefault(); toggleMute() }
  else if (event.key.toLowerCase() === 'f') { event.preventDefault(); void toggleFull() }
}
const pageLifecycle = createPlaybackPageLifecycle({ capture, save: snapshot => save(snapshot, 'pagehide'), stop,
  restore: snapshot => {
    if (disposed || !identity) return
    const sameIdentity = snapshot?.identity && onlineIdentityKey(snapshot.identity) === onlineIdentityKey(identity)
    const remembered = sameIdentity ? snapshot : memory.get(identity)
    return load({ start: remembered?.ended ? 0 : remembered?.position ?? props.initialPosition,
      paused: remembered?.paused ?? false })
  } })
function requestClose() { void pageLifecycle.leave(); emit('close') }
const unregisterViewingCleanup = registerViewingPlaybackCleanup(async () => { await pageLifecycle.leave(); emit('close') })
watch(identityKey, () => {
  if (!mounted || disposed) return
  if (!pageLifecycle.isAway()) save(undefined, 'close')
  void stop(); activePanel.value = ''; errors.length = 0
  try { identity = validateOnlineIdentity(props.onlineIdentity) }
  catch { identity = null; error.value = '在线内容身份无效，请返回重新选择'; return }
  const previous = memory.get(identity)
  const initial = Number(props.initialPosition)
  const nextPosition = previous?.position ?? (Number.isFinite(initial) && initial >= 0 ? initial : 0)
  position.value = nextPosition
  duration.value = previous?.duration || 0
  void load({ start: nextPosition, paused: previous?.paused ?? false })
})
function snapshot() {
  return { identity, transport: transport.value, processing: 'passthrough', engine: engine.value,
    position: position.value, duration: duration.value, paused: paused.value, buffered: buffered.value.map(value => [...value]),
    sessionId: controller.current()?.session_id || null, activeHls: !!hls, errors: [...errors], gen: generation,
    booting: booting.value, closed: disposed || pageLifecycle.isAway(), firstFrame: firstFrame.value,
    startupGapRecoveries: startupGapRecoveries.value, presentedFrames }
}
const debug = { snapshot }
onMounted(() => {
  mounted = true; pageLifecycle.bind(window)
  document.addEventListener('fullscreenchange', fullChange); document.addEventListener('click', documentClick)
  window.addEventListener('keydown', keydown)
  if (typeof ResizeObserver !== 'undefined') { resizeObserver = new ResizeObserver(measure); resizeObserver.observe(stageEl.value) }
  measure()
  if (import.meta.env.DEV) window.__jzOnlinePlayer = debug
  try { identity = validateOnlineIdentity(props.onlineIdentity) }
  catch { booting.value = false; error.value = '在线内容身份无效，请返回重新选择'; return }
  void load({ start: props.initialPosition })
})
onBeforeUnmount(() => {
  if (!pageLifecycle.isAway()) save(undefined, 'close')
  disposed = true; unregisterViewingCleanup(); void pageLifecycle.leave(); pageLifecycle.unbind()
  void controller.close(); detachMedia(); resizeObserver?.disconnect()
  document.removeEventListener('fullscreenchange', fullChange); document.removeEventListener('click', documentClick)
  window.removeEventListener('keydown', keydown)
  for (const timer of [hideTimer, hintTimer, seekTimer, startupTimer, surfaceTimer]) clearTimeout(timer)
  unlockOrientation()
})
onUnmounted(() => { if (import.meta.env.DEV && window.__jzOnlinePlayer === debug) delete window.__jzOnlinePlayer })
defineExpose({ snapshot, saveFinal: () => { save(undefined, 'close'); return Promise.resolve() } })
</script>
