<template>
  <JzDialog :open="casting.open.value" title="投屏到电视" size="small" layer="preview"
    :teleport-disabled="teleportDisabled" close-label="关闭投屏面板" :busy="casting.busy.value" @close="casting.close">
    <p v-if="!casting.active.value" class="cast-intro">选择电视内置投屏，或打开电视上的 jzmedia-tv 后由 App 播放。</p>
    <p v-if="casting.error.value || casting.session.value?.error" class="cast-error" role="alert">
      {{ casting.error.value || casting.session.value.error }}
    </p>
    <JzButton v-if="casting.uncertain?.value" icon="refresh" :loading="casting.busy.value" :disabled="casting.busy.value"
      @click="casting.reconcile">重新检查投屏状态</JzButton>
    <section v-if="casting.session.value" class="cast-session" aria-label="电视播放状态">
      <div class="cast-receiver"><AppIcon name="tv" :size="28" /><strong>{{ casting.session.value.device.name }}</strong></div>
      <p class="cast-state" role="status" aria-live="polite">{{ awaitingPlayback ? casting.stateLabel.value : resumePending ? `正在接续到 ${fmt(casting.session.value.resume_target)}` : casting.stateLabel.value }}</p>
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
          <span v-if="!previewActive" class="cast-preview-hint">{{ manifest?.pages?.length ? '拖动进度条预览画面' : '尚无进度缩略图，仍可拖动跳转' }}</span>
          <PlayerSeekbar class="cast-seekbar" :duration="duration" :manifest="manifest" :value="seekDragging ? seekTarget : actualPosition"
            :value-text="seekDragging ? `待跳转 ${fmt(seekTarget)}` : casting.session.value.position_known ? '' : '电视尚未上报进度，拖动可选择跳转位置'"
            :disabled="seekDisabled" label="电视播放进度" touch-target
            @input="seekInput" @commit="seekCommit" @cancel="cancelSeek" @active="previewActive = $event" />
          <div class="cast-times">
            <output v-if="seekDragging" aria-label="待跳转位置">{{ fmt(seekTarget) }}</output>
            <span v-else>{{ casting.session.value.position_known ? fmt(actualPosition) : '电视未上报进度' }}</span>
            <span>{{ duration > 0 ? fmt(duration) : '时长未知' }}</span>
          </div>
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
      <p v-if="casting.active.value" class="cast-hint">{{ awaitingPlayback ? '关闭面板或手机页面不会取消本次任务，电视确认后会自动播放。' : '关闭此面板或手机页面，电视会继续播放。进度以电视实际上报为准。' }}</p>
      <div v-if="casting.active.value && currentReceiver === 'dlna'" class="cast-app-recommendation">
        <p>电视无画面、无声音或需要字幕时，可改用已安装的 jzmedia-tv。</p>
        <JzButton icon="tv" :disabled="casting.busy.value || casting.uncertain?.value" @click="chooseReceiver('jzmedia_tv')">改用 jzmedia-tv</JzButton>
      </div>
    </section>
    <template v-if="!casting.active.value && !casting.uncertain?.value">
      <div class="cast-methods" role="group" aria-label="投屏方式">
        <JzButton :aria-pressed="receiverType === 'dlna'" :disabled="casting.busy.value" @click="chooseReceiver('dlna')">电视内置投屏</JzButton>
        <JzButton :aria-pressed="receiverType === 'jzmedia_tv'" :disabled="casting.busy.value" @click="chooseReceiver('jzmedia_tv')">jzmedia-tv App</JzButton>
      </div>
      <template v-if="receiverType === 'dlna'">
      <div class="cast-search-heading">
        <strong>选择电视</strong>
        <JzButton icon="refresh" :loading="casting.searching.value" :disabled="casting.busy.value" @click="casting.discover">重新搜索</JzButton>
      </div>
      <p v-if="casting.searching.value" role="status">正在搜索服务器局域网内的电视…</p>
      <div v-else-if="casting.devices.value.length" class="cast-devices" role="radiogroup" aria-label="投屏设备">
        <label v-for="device in casting.devices.value" :key="device.id" class="cast-device">
          <input v-model="casting.selected.value" type="radio" name="cast-device" :value="device.id" :disabled="casting.busy.value" />
          <AppIcon name="tv" />
          <span><strong>{{ device.name }}</strong><small>{{ device.model }} · {{ device.address }}</small></span>
        </label>
      </div>
      <p v-else-if="casting.searched.value" class="cast-hint">未找到设备。请开启电视的 DLNA 接收功能。Linux／NAS 需要自动发现时，建议使用 host 网络模式；bridge、访客 Wi-Fi 或虚拟网络可能阻止组播发现。也可尝试手动添加电视。</p>
      <form class="cast-manual" @submit.prevent="casting.addManual">
        <JzField id="cast-device-location" label="手动添加电视" hint="填写电视的 DLNA 描述地址，只有 IP 地址还不够。例：http://192.168.1.20:49152/description.xml" v-slot="field">
          <input id="cast-device-location" v-model="casting.manualLocation.value" type="url" required :aria-describedby="field.describedby"
            placeholder="http://192.168.1.20:49152/description.xml" :disabled="casting.busy.value" />
        </JzField>
        <JzButton type="submit" :loading="casting.busy.value" :disabled="casting.busy.value || !casting.manualLocation.value.trim()">添加电视</JzButton>
      </form>
      <JzField id="cast-server-url" label="电视可访问的服务器地址" hint="通常是服务器的局域网 IP 和端口。不要使用 localhost、模拟器专用地址或容器内部地址。" v-slot="field">
        <input id="cast-server-url" v-model="casting.serverUrl.value" type="url" :aria-describedby="field.describedby" placeholder="http://192.168.1.10:8080" :disabled="casting.busy.value" />
      </JzField>
      <div class="cast-app-recommendation">
        <p>{{ hasSelectedSubtitle ? '手机选择了字幕，电视内置投屏不能传递该字幕。请打开 jzmedia-tv 后播放。' : '格式不兼容或需要字幕时，推荐打开电视上已安装的 jzmedia-tv。' }}</p>
        <JzButton icon="tv" :disabled="casting.busy.value" @click="chooseReceiver('jzmedia_tv')">使用 jzmedia-tv 播放</JzButton>
      </div>
      <p class="cast-hint">内置接收器由电视解码；选择不同音轨时，NAS 会先准备单音轨文件。网页画质、倍速和自绘字幕不随此方式传递。</p>
      </template>
      <template v-else>
        <div class="cast-search-heading"><strong>选择已打开的电视 App</strong>
          <JzButton icon="refresh" :loading="casting.searchingApps?.value" :disabled="casting.busy.value" @click="casting.refreshApps">刷新</JzButton>
        </div>
        <p class="cast-hint">请在目标电视打开 jzmedia-tv，并连接到当前 jzmedia 服务器。选中下面的电视后点击播放，App 会自动打开这部影片。</p>
        <p v-if="casting.appsError?.value" class="cast-error" role="alert">{{ casting.appsError.value }}</p>
        <p v-else-if="casting.searchingApps?.value && !casting.appsSearched?.value" role="status">正在检查已打开的电视 App…</p>
        <div v-else-if="casting.appDevices?.value?.length" class="cast-devices" role="radiogroup" aria-label="jzmedia-tv 设备">
          <label v-for="device in casting.appDevices.value" :key="device.id" class="cast-device">
            <input v-model="casting.selected.value" type="radio" name="cast-app-device" :value="device.id" :disabled="casting.busy.value" />
            <AppIcon name="tv" /><span><strong>{{ device.name }}</strong><small>jzmedia-tv · {{ device.address }}</small></span>
          </label>
        </div>
        <p v-else class="cast-hint" role="status">尚未找到可接收的 jzmedia-tv。App 打开后会自动出现在这里；若已打开仍未出现，请升级电视 App 并核对服务器地址。</p>
        <p v-if="localSubtitle" class="cast-hint">当前字幕只保存在手机，不能直接传给电视。请在 jzmedia-tv 选择电视可用的字幕。</p>
        <p class="cast-track-summary">音轨：{{ audioLabel || '沿用默认音轨' }}<br>字幕：{{ localSubtitle ? '请在电视 App 另选可用字幕' : subtitleLabel || '关闭字幕' }}</p>
      </template>
    </template>
    <template #footer>
      <HelpLink page="user-guide/playback-controls" label="投屏帮助" />
      <JzButton v-if="held && !casting.uncertain?.value" :disabled="casting.busy.value" @click="$emit('return-phone')">{{ casting.active.value ? '结束投屏，手机播放' : '手机播放' }}</JzButton>
      <JzButton v-if="!casting.active.value && !casting.uncertain?.value" variant="primary" icon="cast" :loading="casting.busy.value"
        :disabled="casting.searching.value || !casting.selected.value || (receiverType === 'dlna' && hasSelectedSubtitle)" @click="casting.play">{{ receiverType === 'jzmedia_tv' ? '在电视 App 播放' : '开始投屏' }}</JzButton>
      <JzButton :disabled="casting.busy.value" @click="casting.close">关闭</JzButton>
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
const seekTarget = ref(0), seekDragging = ref(false), previewActive = ref(false)
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
function chooseReceiver(type) { void props.casting.chooseReceiver?.(type) }
const playing = computed(() => props.casting.session.value?.state === 'PLAYING')
const actualPosition = computed(() => {
  const session = props.casting.session.value
  const position = session?.position_known ? session.position : session?.last_position
  return Math.max(0, Math.min(duration.value || Infinity, Number(position) || 0))
})
const seekDisabled = computed(() => props.casting.busy.value || resumePending.value || !(duration.value > 0))
const skipDisabled = computed(() => seekDisabled.value || !props.casting.session.value?.position_known)
const reportedVolume = computed(() => {
  const value = props.casting.session.value?.volume
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100 ? value : null
})
const volumeValue = computed(() => volumeEditing.value ? desiredVolume.value : reportedVolume.value ?? 50)
function cancelSeek() { seekDragging.value = false; previewActive.value = false }
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
.cast-intro, .cast-hint { color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.cast-intro { margin-top: 0; }
.cast-error { color: var(--jz-danger); overflow-wrap: anywhere; }
.cast-session { margin: 0; }
.cast-search-heading { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.cast-search-heading { justify-content: space-between; margin: 16px 0 12px; }
.cast-receiver { display: flex; align-items: center; gap: 12px; }
.cast-receiver strong { min-width: 0; overflow-wrap: anywhere; }
.cast-state { color: var(--jz-text-dim); margin: 8px 0; }
.cast-methods { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 16px; }
.cast-methods .jz-button { min-height: 44px; padding: 8px; }
.cast-app-recommendation, .cast-awaiting { margin-top: 16px; padding: 12px; border: 1px solid var(--jz-border); border-radius: var(--jz-radius-s); background: var(--jz-surface-2); }
.cast-app-recommendation p, .cast-awaiting p { margin: 0 0 12px; font-size: var(--jz-font-s); }
.cast-prepare-progress { display: block; width: 100%; height: 8px; margin: 12px 0; accent-color: var(--jz-accent); }
.cast-track-summary { color: var(--jz-text-dim); font-size: var(--jz-font-s); overflow-wrap: anywhere; }
.cast-timeline { position: relative; padding-top: 112px; }
.cast-preview-hint { position: absolute; inset: 20px 0 auto; padding: 20px 12px; color: var(--jz-text-dim); font-size: var(--jz-font-s); text-align: center; }
.cast-times { display: flex; justify-content: space-between; gap: 12px; font-size: var(--jz-font-s); font-variant-numeric: tabular-nums; color: var(--jz-text-dim); }
.cast-playback-actions { display: grid; grid-template-columns: 1fr 1.6fr 1fr; gap: 12px; margin-top: 20px; }
.cast-playback-actions .jz-button { min-height: 44px; }
.cast-playback-actions .jz-button--icon { justify-self: center; width: 44px; min-width: 44px; }
.cast-volume { margin-top: 24px; }
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
.cast-devices { display: grid; gap: 8px; margin-bottom: 16px; }
.cast-manual { margin-bottom: 16px; }
.cast-manual .jz-field { margin-bottom: 8px; }
.cast-device { display: flex; align-items: center; gap: 12px; min-height: var(--jz-touch-target); padding: 12px; border: 1px solid var(--jz-border); border-radius: var(--jz-radius-s); cursor: pointer; }
.cast-device:has(input:checked) { border-color: var(--jz-accent); background: var(--jz-surface); }
.cast-device input { flex: none; accent-color: var(--jz-accent); }
.cast-device span { min-width: 0; overflow-wrap: anywhere; }
.cast-device small { display: block; color: var(--jz-text-dim); }
</style>
