<template>
  <JzDialog ref="dialog" :open="casting.open.value" title="投屏到电视" size="small" layer="preview" class="cast-dialog"
    :teleport-disabled="teleportDisabled" close-label="关闭投屏面板" :busy="casting.busy.value" @close="casting.close">
    <p v-if="castError" class="cast-error" role="alert">{{ castError }}</p>
    <JzButton v-if="casting.uncertain?.value" icon="refresh" :loading="casting.busy.value"
      @click="casting.reconcile">重新检查投屏状态</JzButton>
    <section v-if="casting.session.value" class="cast-session" aria-label="电视播放状态">
      <div class="cast-receiver"><AppIcon name="tv" :size="28" /><strong>{{ casting.session.value.device.name }}</strong></div>
      <p class="cast-state" role="status" aria-live="polite">{{ sessionStatus }}</p>
      <p v-if="casting.session.value.warning" class="cast-hint">{{ casting.session.value.warning }}</p>
      <div v-if="casting.active.value && awaitingPlayback" class="cast-awaiting">
        <template v-if="preparing">
          <p>{{ casting.session.value.prepare_message || '正在生成只含所选音轨的兼容文件，完成后电视会自动播放。' }}</p>
          <progress class="cast-prepare-progress" :value="prepareProgress ?? undefined" max="1" aria-label="音轨准备进度"></progress>
          <p class="cast-hint">{{ prepareProgress == null ? '准备中…' : `${Math.round(prepareProgress * 100)}%` }} · 首次准备需要等待；关闭手机页面后任务继续，也可取消。</p>
        </template>
        <template v-else>
          <p>播放请求已发给选定的 jzmedia-tv，请保持该 App 在电视前台。</p>
          <p class="cast-hint">将从 {{ fmt(casting.session.value.resume_target ?? 0) }} 开始；仅在电视确认后显示实际播放进度。</p>
        </template>
        <JzButton :disabled="casting.busy.value" icon="stop" @click="casting.control('stop')">{{ preparing ? '取消音轨准备' : '取消本次播放' }}</JzButton>
      </div>
      <template v-else-if="casting.active.value">
        <div class="cast-timeline">
          <PlayerSeekbar class="cast-seekbar" :duration="duration" :manifest="manifest" :value="seekDragging ? seekTarget : displayPosition"
            :value-text="seekValueText" :disabled="seekDisabled" :preview-container="dialog?.element" label="电视播放进度" touch-target
            @input="seekInput" @commit="seekCommit" @cancel="cancelSeek" />
          <div class="cast-times">
            <output v-if="seekDragging" aria-label="待跳转位置">{{ fmt(seekTarget) }}</output>
            <output v-else-if="seekPending" aria-label="等待电视确认的跳转位置">{{ fmt(displayPosition) }} · 待确认</output>
            <span v-else>{{ casting.session.value.position_known ? fmt(actualPosition) : '电视未上报进度' }}</span>
            <span>{{ duration > 0 ? fmt(duration) : '时长未知' }}</span>
          </div>
          <p v-if="seekPending" class="cast-hint cast-reported-position">电视上次上报：{{ casting.session.value.position_known || casting.session.value.last_position != null ? fmt(actualPosition) : '未知' }}</p>
        </div>
        <div class="cast-playback-actions">
          <JzButton icon-only aria-label="后退 10 秒" title="后退 10 秒" :disabled="skipDisabled" @click="skip(-10)"><PlayerIcon name="rewind" /></JzButton>
          <JzButton :disabled="casting.busy.value || resumePending" :icon="playing ? 'pause' : 'play'" @click="casting.control(playing ? 'pause' : 'play')">{{ playing ? '暂停' : '播放' }}</JzButton>
          <JzButton icon-only aria-label="前进 10 秒" title="前进 10 秒" :disabled="skipDisabled" @click="skip(10)"><PlayerIcon name="forward" /></JzButton>
        </div>
        <div v-if="casting.session.value.device.volume_control" class="cast-volume">
          <div class="cast-volume-heading"><label for="cast-volume"><AppIcon name="volume" />电视音量</label>
            <output for="cast-volume">{{ volumeEditing ? `设置为 ${desiredVolume}%` : reportedVolume == null ? '电视未上报音量' : `${reportedVolume}%` }}</output>
          </div>
          <input id="cast-volume" class="cast-volume-range" type="range" min="0" max="100" step="1" :value="volumeValue"
            :style="{ '--cast-volume-percent': volumeValue + '%' }"
            :class="{ 'cast-volume-unknown': reportedVolume == null && !volumeEditing }" :disabled="casting.busy.value"
            aria-label="设置电视音量" :aria-valuetext="reportedVolume == null && !volumeEditing ? '电视未上报音量，拖动可设置' : volumeValue + '%'"
            @input="volumeInput" @change="volumeCommit" @pointerdown="beginVolume" @keydown="beginVolume"
            @pointercancel="cancelVolumeGesture" @blur="cancelVolume" />
        </div>
        <JzButton class="cast-stop" icon="stop" :disabled="casting.busy.value" @click="casting.control('stop')">停止投屏</JzButton>
      </template>
      <p v-if="casting.active.value" class="cast-hint cast-continuity">{{ awaitingPlayback ? '关闭面板或手机页面不会取消本次任务。' : '关闭面板或手机页面，电视会继续播放。' }}</p>
      <details v-if="casting.active.value && currentReceiver === 'dlna'" class="cast-receiver-options cast-details">
        <summary>电视无画面、无声音或需要字幕？</summary>
        <p class="cast-hint">可改用已安装的 jzmedia-tv，停止当前投屏后接续播放。</p>
        <JzButton icon="tv" :disabled="casting.busy.value || casting.uncertain?.value" @click="chooseReceiver('jzmedia_tv')">改用 jzmedia-tv</JzButton>
      </details>
    </section>
    <template v-if="!casting.active.value && !casting.uncertain?.value">
      <div class="cast-methods" role="group" aria-label="投屏方式">
        <JzButton :aria-pressed="receiverType === 'dlna'" :disabled="casting.busy.value" @click="chooseReceiver('dlna')">电视内置投屏</JzButton>
        <JzButton :aria-pressed="receiverType === 'jzmedia_tv'" :disabled="casting.busy.value" @click="chooseReceiver('jzmedia_tv')">jzmedia-tv App</JzButton>
      </div>
      <template v-if="receiverType === 'dlna'">
        <div v-if="dlnaBlocked" class="cast-compatibility" role="note" aria-labelledby="cast-compatibility-title">
          <strong id="cast-compatibility-title">当前无法使用电视内置投屏</strong>
          <p>{{ subtitleRestriction }}</p>
          <JzButton icon="tv" :disabled="casting.busy.value" @click="chooseReceiver('jzmedia_tv')">使用 jzmedia-tv 播放</JzButton>
        </div>
        <div class="cast-search-heading">
          <strong>选择电视</strong>
          <JzButton icon="refresh" :loading="casting.searching.value" :disabled="casting.busy.value" @click="casting.discover">重新搜索</JzButton>
        </div>
        <p v-if="casting.searching.value" class="cast-hint" role="status">正在搜索服务器局域网内的电视…</p>
        <div v-else-if="casting.devices.value.length" class="cast-devices" role="radiogroup" aria-label="投屏设备">
          <label v-for="device in casting.devices.value" :key="device.id" class="cast-device">
            <input v-model="casting.selected.value" type="radio" name="cast-device" :value="device.id" :disabled="casting.busy.value" />
            <AppIcon name="tv" />
            <span><strong>{{ device.name }}</strong><small>{{ device.model }} · {{ device.address }}</small></span>
          </label>
        </div>
        <p v-else-if="casting.searched.value" class="cast-hint" role="status">未找到电视。请开启电视的 DLNA 接收功能，再重新搜索；也可在连接设置中手动添加。</p>
        <details class="cast-network-settings cast-details">
          <summary>连接设置与手动添加</summary>
          <JzField id="cast-server-url" label="电视可访问的服务器地址" hint="通常可沿用当前地址；连接失败时改为服务器的局域网 IP 和端口，不要使用 localhost 或容器内部地址。" v-slot="field">
            <input id="cast-server-url" v-model="casting.serverUrl.value" type="url" :aria-describedby="field.describedby" placeholder="http://192.168.1.10:8080" :disabled="casting.busy.value" />
          </JzField>
          <form class="cast-manual" @submit.prevent="casting.addManual">
            <JzField id="cast-device-location" label="手动添加电视" hint="填写完整的 DLNA 描述地址，只有 IP 地址还不够。例如：http://192.168.1.20:49152/description.xml" v-slot="field">
              <input id="cast-device-location" v-model="casting.manualLocation.value" type="url" required :aria-describedby="field.describedby"
                placeholder="http://192.168.1.20:49152/description.xml" :disabled="casting.busy.value" />
            </JzField>
            <JzButton type="submit" :loading="casting.busy.value" :disabled="casting.busy.value || !casting.manualLocation.value.trim()">添加电视</JzButton>
          </form>
          <p class="cast-hint">设备由服务器搜索。Linux／NAS 自动发现建议使用 host 网络；bridge、访客 Wi-Fi 或虚拟网络可能阻止组播发现。</p>
        </details>
        <details class="cast-track-settings cast-details">
          <summary>音轨与格式说明</summary>
          <p class="cast-hint">音轨：{{ audioLabel || '沿用默认音轨' }}。多音轨片源会先准备只含所选音轨的完整文件，首次需要等待。</p>
          <p class="cast-hint">内置接收器由电视解码，网页画质、倍速和字幕不会传递。格式不兼容或电视无画面、无声音时，可改用 jzmedia-tv。</p>
          <JzButton icon="tv" :disabled="casting.busy.value" @click="chooseReceiver('jzmedia_tv')">改用 jzmedia-tv</JzButton>
        </details>
        <JzButton v-if="castError && !dlnaBlocked" class="cast-error-recovery" icon="tv" :disabled="casting.busy.value" @click="chooseReceiver('jzmedia_tv')">改用 jzmedia-tv</JzButton>
      </template>
      <template v-else>
        <div class="cast-search-heading"><strong>选择电视 App</strong>
          <JzButton icon="refresh" :loading="casting.searchingApps?.value" :disabled="casting.busy.value" @click="casting.refreshApps">刷新</JzButton>
        </div>
        <p v-if="casting.appsError?.value" class="cast-error" role="alert">{{ casting.appsError.value }}</p>
        <p v-else-if="casting.searchingApps?.value && !casting.appDevices?.value?.length" class="cast-hint" role="status">正在检查已打开的电视 App…</p>
        <div v-else-if="casting.appDevices?.value?.length" class="cast-devices" role="radiogroup" aria-label="jzmedia-tv 设备">
          <label v-for="device in casting.appDevices.value" :key="device.id" class="cast-device">
            <input v-model="casting.selected.value" type="radio" name="cast-app-device" :value="device.id" :disabled="casting.busy.value" />
            <AppIcon name="tv" /><span><strong>{{ device.name }}</strong><small>jzmedia-tv · {{ device.address }}</small></span>
          </label>
        </div>
        <p v-else class="cast-hint" role="status">尚未找到可接收的 jzmedia-tv。请在目标电视打开 jzmedia-tv，并连接当前服务器；App 打开后会自动出现在这里。</p>
        <details class="cast-app-help cast-details">
          <summary>如何连接电视 App</summary>
          <p class="cast-hint">请在目标电视打开 jzmedia-tv，并连接到当前 jzmedia 服务器。选中电视后点击播放，App 会自动打开这部影片。</p>
          <p class="cast-hint">若已打开仍未出现，请升级电视 App 并核对服务器地址。接收期间保持 App 在电视前台。</p>
        </details>
        <dl class="cast-track-summary" aria-label="传递给电视的播放选项">
          <div><dt>音轨</dt><dd>{{ audioLabel || '沿用默认音轨' }}</dd></div>
          <div><dt>字幕</dt><dd>{{ localSubtitle ? '需在电视 App 另选' : subtitleLabel || '关闭字幕' }}</dd></div>
        </dl>
        <p v-if="localSubtitle" class="cast-hint">当前字幕只保存在手机，不能直接传给电视。请在 jzmedia-tv 选择电视可用的字幕。</p>
      </template>
    </template>
    <div class="cast-help"><HelpLink page="user-guide/playback-controls" label="投屏帮助" /></div>
    <template #footer>
      <div class="cast-footer">
        <template v-if="!casting.active.value && !casting.uncertain?.value">
          <p v-if="startHint" id="cast-start-hint" class="cast-start-hint" role="status">{{ startHint }}</p>
          <JzButton class="cast-start" variant="primary" icon="cast" :loading="casting.busy.value"
            :aria-describedby="startHint ? 'cast-start-hint' : undefined" :disabled="startDisabled" @click="casting.play">{{ receiverType === 'jzmedia_tv' ? '在电视 App 播放' : '开始投屏' }}</JzButton>
        </template>
        <JzButton v-if="held && !casting.uncertain?.value" class="cast-return-phone" :disabled="casting.busy.value" @click="$emit('return-phone')">{{ casting.active.value ? '结束投屏，手机播放' : '手机播放' }}</JzButton>
      </div>
    </template>
  </JzDialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import JzDialog from './JzDialog.vue'
import JzButton from './JzButton.vue'
import JzField from './JzField.vue'
import AppIcon from './AppIcon.vue'
import HelpLink from './HelpLink.vue'
import PlayerIcon from './PlayerIcon.vue'
import PlayerSeekbar from './PlayerSeekbar.vue'
import { fmtTime as fmt } from '../playerLabels.js'

const props = defineProps({ casting: { type: Object, required: true }, held: Boolean, teleportDisabled: Boolean,
  manifest: { type: Object, default: null }, audioLabel: { type: String, default: '' },
  subtitleLabel: { type: String, default: '' }, hasSelectedSubtitle: Boolean, localSubtitle: Boolean })
defineEmits(['return-phone'])
const dialog = ref(null)
const seekTarget = ref(0), seekDragging = ref(false)
const desiredVolume = ref(null), volumeEditing = ref(false)
let volumeGestureCancelled = false
const duration = computed(() => Math.max(0, Number(props.casting.session.value?.duration) || 0))
const resumePending = computed(() => props.casting.active.value && !!props.casting.session.value?.resume_pending)
const receiverType = computed(() => props.casting.receiverType?.value || 'dlna')
const currentReceiver = computed(() => props.casting.session.value?.receiver_type || 'dlna')
const preparing = computed(() => props.casting.session.value?.state === 'PREPARING')
const awaitingPlayback = computed(() => ['PREPARING', 'WAITING_FOR_APP', 'LOADING'].includes(props.casting.session.value?.state))
const prepareProgress = computed(() => {
  const value = props.casting.session.value?.prepare_progress
  return typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : null
})
const castError = computed(() => props.casting.error.value || props.casting.session.value?.error || '')
const dlnaBlocked = computed(() => receiverType.value === 'dlna' && props.hasSelectedSubtitle)
const subtitleRestriction = computed(() => `当前选择了${props.subtitleLabel ? `“${props.subtitleLabel}”字幕` : '字幕'}，电视内置投屏不能传递该字幕。请改用 jzmedia-tv，或回到播放器关闭字幕后再投屏。`)
const appUnavailable = computed(() => receiverType.value === 'jzmedia_tv' && (
  !!props.casting.appsError?.value || !props.casting.appDevices?.value?.some(device => device.id === props.casting.selected.value)))
const startDisabled = computed(() => props.casting.busy.value || (receiverType.value === 'dlna' && props.casting.searching.value)
  || !props.casting.selected.value || dlnaBlocked.value || appUnavailable.value)
const startHint = computed(() => {
  if (dlnaBlocked.value) return '已选字幕无法通过内置投屏传递，请改用电视 App 或关闭字幕。'
  if (props.casting.busy.value) return '正在发送播放请求…'
  if (receiverType.value === 'jzmedia_tv' && props.casting.appsError?.value) return '无法确认电视 App 是否可用，请刷新重试。'
  if (receiverType.value === 'dlna' && props.casting.searching.value) return '搜索完成后，请选择要播放的电视。'
  if (!props.casting.selected.value || appUnavailable.value) return receiverType.value === 'jzmedia_tv' ? '请先选择已打开的电视 App。' : '请先选择一台电视。'
  return ''
})
function chooseReceiver(type) { void props.casting.chooseReceiver?.(type) }
const lastPlaybackState = ref('')
watch(() => props.casting.session.value?.id, () => {
  const state = props.casting.session.value?.state
  lastPlaybackState.value = ['PLAYING', 'PAUSED_PLAYBACK'].includes(state) ? state : ''
}, { flush: 'sync' })
watch(() => props.casting.session.value?.state, state => {
  if (['PLAYING', 'PAUSED_PLAYBACK'].includes(state)) lastPlaybackState.value = state
}, { immediate: true, flush: 'sync' })
const playing = computed(() => props.casting.session.value?.state === 'PLAYING'
  || (seekPending.value && props.casting.session.value?.state === 'TRANSITIONING' && lastPlaybackState.value === 'PLAYING'))
const actualPosition = computed(() => {
  const session = props.casting.session.value
  const position = session?.position_known ? session.position : session?.last_position
  return Math.max(0, Math.min(duration.value || Infinity, Number(position) || 0))
})
const seekPending = computed(() => !!props.casting.seekPending?.value)
const displayPosition = computed(() => props.casting.displayPosition?.value ?? actualPosition.value)
const sessionStatus = computed(() => awaitingPlayback.value ? props.casting.stateLabel.value
  : seekPending.value ? `正在跳转到 ${fmt(displayPosition.value)}，等待电视确认`
  : resumePending.value ? `正在接续到 ${fmt(props.casting.session.value.resume_target)}` : props.casting.stateLabel.value)
const seekValueText = computed(() => seekDragging.value ? `待跳转 ${fmt(seekTarget.value)}`
  : seekPending.value ? `正在跳转到 ${fmt(displayPosition.value)}，等待电视确认`
  : props.casting.session.value?.position_known ? '' : '电视尚未上报进度，拖动可选择跳转位置')
const seekDisabled = computed(() => props.casting.busy.value || resumePending.value || !(duration.value > 0))
const skipDisabled = computed(() => seekDisabled.value || seekPending.value || !props.casting.session.value?.position_known)
const reportedVolume = computed(() => {
  const value = props.casting.session.value?.volume
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100 ? value : null
})
const volumeValue = computed(() => volumeEditing.value ? desiredVolume.value : reportedVolume.value ?? 50)
function cancelSeek() { seekDragging.value = false }
function seekInput(event) {
  if (seekDisabled.value) return
  seekDragging.value = true
  seekTarget.value = Math.max(0, Math.min(duration.value, Number(event.target.value) || 0))
}
function seekCommit(event) {
  if (seekDisabled.value) { cancelSeek(); return }
  const target = seekDragging.value ? seekTarget.value : Number(event.target.value)
  cancelSeek()
  if (Number.isFinite(target)) void props.casting.control('seek', Math.max(0, Math.min(duration.value, target)))
}
function skip(delta) {
  if (!skipDisabled.value) void props.casting.control('seek', Math.max(0, Math.min(Math.max(0, duration.value - 1), actualPosition.value + delta)))
}
function volumeInput(event) {
  if (props.casting.busy.value) return
  volumeEditing.value = true
  desiredVolume.value = Math.max(0, Math.min(100, Number(event.target.value) || 0))
}
function volumeCommit(event) {
  if (volumeGestureCancelled) { volumeGestureCancelled = false; cancelVolume(); return }
  if (props.casting.busy.value) { cancelVolume(); return }
  const value = volumeEditing.value ? desiredVolume.value : Number(event.target.value)
  cancelVolume()
  if (Number.isFinite(value)) void props.casting.control('volume', Math.max(0, Math.min(100, value)))
}
function cancelVolume() { volumeEditing.value = false; desiredVolume.value = null }
function beginVolume() { volumeGestureCancelled = false }
function cancelVolumeGesture() { volumeGestureCancelled = true; cancelVolume() }
watch(() => props.casting.session.value?.id, () => { cancelSeek(); cancelVolume() })
watch(() => props.casting.open.value, open => { if (!open) { cancelSeek(); cancelVolume() } })
watch(seekDisabled, disabled => { if (disabled) cancelSeek() })
watch(() => props.casting.busy.value, busy => { if (busy) cancelVolume() })
</script>

<style scoped>
/* JzDialog teleports its section, so the preview anchor uses this owned class. */
:global(.cast-dialog) { position: relative; }
.cast-hint, .cast-start-hint { color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.cast-hint { margin: 8px 0 12px; }
.cast-error { margin: 0 0 16px; color: var(--jz-danger); overflow-wrap: anywhere; }
.cast-session { margin: 0; }
.cast-search-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 20px 0 12px; }
.cast-search-heading strong { min-width: 0; font-size: var(--jz-font-m); }
.cast-search-heading .jz-button { flex: none; }
.cast-receiver { display: flex; align-items: center; gap: 12px; }
.cast-receiver strong { min-width: 0; overflow-wrap: anywhere; }
.cast-receiver .app-icon { flex: none; }
.cast-state { color: var(--jz-text-dim); margin: 8px 0; font-size: var(--jz-font-s); }
.cast-methods { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 8px; }
.cast-methods .jz-button { min-height: 44px; padding: 8px 6px; font-size: var(--jz-font-s); }
.cast-compatibility, .cast-awaiting { margin-top: 16px; padding: 12px; border: 1px solid var(--jz-border); border-radius: var(--jz-radius-s); background: var(--jz-surface-2); }
.cast-compatibility strong { font-size: var(--jz-font-m); }
.cast-compatibility p, .cast-awaiting p { margin: 0 0 12px; font-size: var(--jz-font-s); }
.cast-compatibility p { margin-top: 8px; }
.cast-compatibility .jz-button { width: 100%; }
.cast-prepare-progress { display: block; width: 100%; height: 8px; margin: 12px 0; accent-color: var(--jz-accent); }
.cast-devices { display: grid; gap: 8px; margin-bottom: 16px; }
.cast-device { display: flex; align-items: center; gap: 12px; min-height: var(--jz-touch-target); padding: 12px; border: 1px solid var(--jz-border); border-radius: var(--jz-radius-s); cursor: pointer; }
.cast-device:has(input:checked) { border-color: var(--jz-accent); background: var(--jz-selected); }
.cast-device input, .cast-device .app-icon { flex: none; }
.cast-device input { accent-color: var(--jz-accent); }
.cast-device span { min-width: 0; overflow-wrap: anywhere; }
.cast-device strong { display: block; font-size: var(--jz-font-m); }
.cast-device small { display: block; color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.cast-details { margin-top: 4px; border-top: 1px solid var(--jz-border); }
.cast-details summary { box-sizing: border-box; min-height: 44px; padding: 12px 0; cursor: pointer; font-size: var(--jz-font-s); color: var(--jz-text-dim); }
.cast-details summary:focus-visible { outline: 2px solid var(--jz-link); outline-offset: 2px; border-radius: var(--jz-radius-s); }
.cast-details[open] summary { color: var(--jz-text); }
.cast-details .jz-field { margin: 4px 0 12px; }
.cast-manual { margin: 20px 0 16px; }
.cast-track-summary { margin: 16px 0 0; font-size: var(--jz-font-s); }
.cast-track-summary > div { display: grid; grid-template-columns: 2em minmax(0, 1fr); gap: 12px; margin: 6px 0; }
.cast-track-summary dt { color: var(--jz-text-dim); }
.cast-track-summary dd { margin: 0; overflow-wrap: anywhere; }
.cast-error-recovery { margin-top: 12px; }
.cast-timeline { position: relative; margin-top: 20px; }
.cast-times { display: flex; justify-content: space-between; gap: 12px; font-size: var(--jz-font-s); font-variant-numeric: tabular-nums; color: var(--jz-text-dim); }
.cast-times > :last-child { flex: none; }
.cast-reported-position { margin: 4px 0 0; }
.cast-playback-actions { display: grid; grid-template-columns: 1fr 1.6fr 1fr; gap: 12px; margin-top: 20px; }
.cast-playback-actions .jz-button { min-height: 44px; }
.cast-playback-actions .jz-button--icon { justify-self: center; width: 44px; min-width: 44px; }
.cast-volume { margin-top: 20px; }
.cast-volume-heading, .cast-volume-heading label { display: flex; align-items: center; gap: 8px; }
.cast-volume-heading { justify-content: space-between; flex-wrap: wrap; font-size: var(--jz-font-s); }
.cast-volume-heading output { color: var(--jz-text-dim); font-variant-numeric: tabular-nums; }
.cast-volume-range { appearance: none; display: block; width: 100%; min-height: 44px; padding: 0; border: 0; background: transparent;
  margin: 4px 0 0; touch-action: none; cursor: pointer; --cast-volume-color: var(--jz-accent); }
.cast-volume-range::-webkit-slider-runnable-track { height: 4px; border-radius: 4px;
  background: linear-gradient(to right, var(--cast-volume-color) var(--cast-volume-percent), var(--jz-border-strong) var(--cast-volume-percent)); }
.cast-volume-range::-webkit-slider-thumb { appearance: none; width: 14px; height: 14px; margin-top: -5px; border: 0; border-radius: 50%; background: var(--cast-volume-color); }
.cast-volume-range::-moz-range-track { height: 4px; border-radius: 4px; background: var(--jz-border-strong); }
.cast-volume-range::-moz-range-progress { height: 4px; border-radius: 4px; background: var(--cast-volume-color); }
.cast-volume-range::-moz-range-thumb { width: 14px; height: 14px; border: 0; border-radius: 50%; background: var(--cast-volume-color); }
.cast-volume-range:disabled { cursor: default; opacity: .5; }
.cast-volume-unknown { --cast-volume-color: var(--jz-text-dim); }
.cast-stop { width: 100%; margin-top: 16px; }
.cast-continuity { margin: 12px 0; }
.cast-help { margin-top: 12px; }
.cast-footer { display: flex; flex-direction: column; gap: 10px; width: 100%; min-width: 0; }
.cast-start-hint { margin: 0; }
.cast-start, .cast-return-phone { width: 100%; min-height: 44px; white-space: nowrap; }
</style>
