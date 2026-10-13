<template>
  <div class="player-quick-controls" @click.stop @pointerdown.stop @dblclick.stop @keydown="onKey">
    <JzButton ref="speedTrigger" variant="ghost" icon-only class="player-icon-btn rate-btn player-menu-trigger"
      :aria-label="'播放速度 ' + playbackRate + ' 倍'" :aria-expanded="panel === 'speed'"
      :aria-controls="panel === 'speed' ? speedId : undefined" title="播放速度" @click="$emit('toggle', 'speed')">
      {{ playbackRate }}<span>×</span>
    </JzButton>
    <JzButton ref="tracksTrigger" variant="ghost" icon-only class="player-icon-btn player-menu-trigger"
      aria-label="字幕与音轨" title="字幕与音轨" :aria-expanded="panel === 'tracks'"
      :aria-controls="panel === 'tracks' ? tracksId : undefined" @click="$emit('toggle', 'tracks')">
      <PlayerIcon name="subtitles" />
    </JzButton>
    <section v-if="panel === 'speed'" ref="speedPanel" :id="speedId" class="quick-panel player-popup"
      :style="{ maxHeight: panelHeight + 'px' }" aria-label="播放速度">
      <header class="quick-header"><strong>播放速度</strong>
        <JzButton variant="ghost" icon-only aria-label="关闭播放速度" @click="$emit('close')"><PlayerIcon name="close" :size="18" /></JzButton>
      </header>
      <div class="speed-choices" role="group" aria-label="倍速选项">
        <JzButton v-for="rate in PLAYBACK_RATES" :key="rate" :aria-pressed="playbackRate === rate"
          @click="$emit('rate-change', rate)">{{ rate }}×</JzButton>
      </div>
    </section>
    <section v-if="panel === 'tracks'" ref="tracksPanel" :id="tracksId" class="quick-panel player-popup"
      :style="{ maxHeight: panelHeight + 'px' }" aria-label="字幕与音轨">
      <header class="quick-header"><strong>字幕与音轨</strong>
        <JzButton variant="ghost" icon-only aria-label="关闭字幕与音轨" @click="$emit('close')"><PlayerIcon name="close" :size="18" /></JzButton>
      </header>
      <div class="track-options">
        <label v-if="audios.length > 1" :for="audioId">音轨
          <select :id="audioId" aria-label="音轨" :value="audioIdx" @change="$emit('audio-change', Number($event.target.value))">
            <option v-for="(audio, index) in audios" :key="index" :value="index">{{ audioLabel(audio, index) }}</option>
          </select>
        </label>
        <label v-if="subs.length" :for="subId">字幕轨道
          <select :id="subId" aria-label="字幕轨道" :value="subIdx" @change="$emit('sub-change', Number($event.target.value))">
            <option :value="-1">关闭字幕</option>
            <option v-for="(sub, index) in subs" :key="index" :value="index">{{ subLabel(sub, index) }}{{ subBadge(sub) }}</option>
          </select>
        </label>
        <p v-else class="quick-hint">暂无字幕，可加载本地字幕文件。</p>
        <div class="track-file-actions">
          <JzButton icon="subtitles" @click="pickFile">加载字幕文件</JzButton>
          <JzButton v-if="hasLocalSub" @click="$emit('remove-local-subs')">移除本地字幕</JzButton>
          <input ref="fileInput" type="file" accept=".srt,.vtt,.ass,.ssa" class="sub-file" aria-label="字幕文件" @change="loadFile" />
        </div>
        <JzButton variant="ghost" class="subtitle-settings" @click="$emit('subtitle-settings')">字幕调整与兼容设置</JzButton>
      </div>
    </section>
  </div>
</template>

<script setup>
import { getCurrentInstance, nextTick, ref, watch } from 'vue'
import JzButton from './JzButton.vue'
import PlayerIcon from './PlayerIcon.vue'
import { PLAYBACK_RATES } from '../playbackControls.js'
import { audioLabel, subLabel, subBadge } from '../playerLabels.js'

const props = defineProps({ panel: { type: String, default: '' }, panelHeight: { type: Number, default: 300 },
  playbackRate: { type: Number, default: 1 }, audios: { type: Array, default: () => [] }, audioIdx: { type: Number, default: 0 },
  subs: { type: Array, default: () => [] }, subIdx: { type: Number, default: -1 }, hasLocalSub: Boolean })
const emit = defineEmits(['toggle', 'close', 'rate-change', 'audio-change', 'sub-change',
  'load-sub-file', 'remove-local-subs', 'subtitle-settings'])
const instanceId = getCurrentInstance().uid
const speedId = `player-speed-${instanceId}`, tracksId = `player-tracks-${instanceId}`
const audioId = `player-audio-${instanceId}`, subId = `player-sub-${instanceId}`
const speedTrigger = ref(null), tracksTrigger = ref(null), speedPanel = ref(null), tracksPanel = ref(null), fileInput = ref(null)
function focus(panel) { (panel === 'speed' ? speedTrigger : tracksTrigger).value?.focus({ preventScroll: true }) }
defineExpose({ focus })
function pickFile() { fileInput.value?.click() }
function loadFile(event) {
  const file = event.target.files?.[0]
  if (file) emit('load-sub-file', file)
  event.target.value = ''
}
function onKey(event) {
  if (event.key !== 'Escape' || !props.panel) return
  event.preventDefault()
  event.stopPropagation()
  emit('close')
}
watch(() => props.panel, async panel => {
  if (!['speed', 'tracks'].includes(panel)) return
  await nextTick()
  if (props.panel !== panel) return
  const node = panel === 'speed'
    ? speedPanel.value?.querySelector('.speed-choices [aria-pressed="true"]')
    : tracksPanel.value?.querySelector('select, .track-file-actions button')
  node?.focus({ preventScroll: true })
})
</script>

<style scoped>
.player-quick-controls { display: flex; align-items: center; flex: none; gap: 2px; }
.player-quick-controls > .player-icon-btn { width: var(--jz-touch-target); height: var(--jz-touch-target); }
.rate-btn { font-size: .8125rem; font-weight: 600; font-variant-numeric: tabular-nums; }
.rate-btn span { color: var(--jz-text-dim); font-weight: 400; margin-left: 2px; }
.quick-panel { position: absolute; right: 0; bottom: calc(100% + 8px); z-index: 12; width: min(340px, 100%);
  overflow: auto; overscroll-behavior: contain; box-sizing: border-box; border: 1px solid var(--jz-border-strong);
  border-radius: var(--jz-radius-dialog); background: var(--jz-surface); color: var(--jz-text); box-shadow: var(--jz-shadow-menu); }
.quick-header { position: sticky; top: 0; z-index: 1; display: flex; align-items: center; justify-content: space-between;
  min-height: 44px; padding: 0 8px 0 14px; background: var(--jz-surface); border-bottom: 1px solid var(--jz-border); }
.quick-header strong { font-size: var(--jz-font-m); }
.quick-header :deep(.jz-button) { width: 44px; height: 44px; }
.speed-choices { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; padding: 12px; }
.speed-choices :deep(.jz-button) { min-height: 44px; padding: 6px; }
.track-options { padding: 12px; }
.track-options label { display: grid; gap: 6px; margin-bottom: 12px; font-size: var(--jz-font-s); color: var(--jz-text-dim); }
.track-options select { width: 100%; min-width: 0; min-height: 44px; box-sizing: border-box; color: var(--jz-text); }
.track-file-actions { display: flex; flex-wrap: wrap; gap: 6px; }
.track-file-actions :deep(.jz-button) { font-size: var(--jz-font-s); min-height: 44px; padding: 6px 10px; }
.quick-hint { color: var(--jz-text-dim); font-size: var(--jz-font-s); line-height: 1.7; margin: 0 0 12px; }
.subtitle-settings { margin-top: 6px; font-size: var(--jz-font-s); min-height: 44px; }
.sub-file { display: none; }
</style>
