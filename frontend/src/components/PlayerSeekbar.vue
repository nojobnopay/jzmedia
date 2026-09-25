<template>
  <div ref="wrap" class="seekbar" @pointermove="move" @pointerleave="leave">
    <div class="seek-rail" aria-hidden="true">
      <span v-for="(section, i) in bufferStyles" :key="i" class="seek-buffer" :style="section"></span>
      <span class="seek-played" :style="{ width: playedPercent + '%' }"></span>
    </div>
    <input type="range" min="0" :max="Math.floor(duration)" step="1" :value="value"
      :disabled="disabled" aria-label="播放进度" :aria-valuetext="fmtTime(value) + ' / ' + fmtTime(duration)" @input="input" @change="commit"
      @pointerdown="begin" @pointerup="end" @pointercancel="cancel" @lostpointercapture="lostCapture" @blur="leave" />
    <div v-if="visible && duration > 0" class="preview" :style="{ left: bubbleLeft + 'px' }">
      <div v-if="frame && !imageFailed" class="preview-image" :style="imageStyle"></div>
      <span>{{ fmtTime(hoverTime) }}</span>
      <small v-if="!frame || imageFailed">{{ manifest?.state === 'running' ? '预览生成中…' : '可在设置中生成预览' }}</small>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { fmtTime } from '../playerLabels.js'
import { previewFrame } from '../playbackControls.js'

const props = defineProps({
  value: { type: Number, default: 0 }, duration: { type: Number, default: 0 },
  buffered: { type: Array, default: () => [] },
  disabled: Boolean, manifest: { type: Object, default: null },
})
const percent = time => Math.max(0, Math.min(100, (Number(time) || 0) / Math.max(1, props.duration) * 100))
const playedPercent = computed(() => percent(props.value))
const bufferStyles = computed(() => props.buffered.map(([start, end]) => ({
  left: percent(start) + '%', width: Math.max(0, percent(end) - percent(start)) + '%',
})))
const emit = defineEmits(['input', 'commit', 'active', 'cancel'])
const wrap = ref(null)
const visible = ref(false)
const dragging = ref(false)
const hoverTime = ref(0)
const pointerX = ref(0)
const limits = ref({ left: 0, right: 164 })
const imageFailed = ref(false)
const frame = computed(() => previewFrame(props.manifest, hoverTime.value))
const bubbleLeft = computed(() => Math.max(limits.value.left, Math.min(limits.value.right - 164, pointerX.value - 82)))
const imageStyle = computed(() => frame.value ? {
  width: frame.value.width + 'px', height: frame.value.height + 'px',
  backgroundImage: `url("${frame.value.url}")`,
  backgroundPosition: `-${frame.value.x}px -${frame.value.y}px`,
  backgroundSize: `${frame.value.sheetWidth}px ${frame.value.sheetHeight}px`,
} : {})
watch(() => frame.value?.url, (url, _old, cleanup) => {
  imageFailed.value = false
  if (!url) return
  const img = new Image()
  img.onerror = () => { if (frame.value?.url === url) imageFailed.value = true }
  img.src = url
  cleanup(() => { img.onerror = null })
  const pages = props.manifest?.pages || []
  const index = pages.indexOf(url)
  for (const adjacent of [pages[index - 1], pages[index + 1]]) {
    if (adjacent) { const preload = new Image(); preload.src = adjacent }
  }
})
watch(() => props.disabled, disabled => { if (disabled) cancel() })
function measure() {
  const rect = wrap.value.getBoundingClientRect()
  const container = wrap.value.closest('.pv-ctl')?.getBoundingClientRect() || rect
  limits.value = { left: container.left - rect.left + 4, right: container.right - rect.left - 4 }
  return rect
}
function move(e) {
  if (props.disabled) return
  const rect = measure()
  // 原生 range 两端预留 thumb 半径，悬停预览与点击位置一致。
  pointerX.value = Math.max(0, Math.min(rect.width, e.clientX - rect.left))
  if (!dragging.value) hoverTime.value = Math.max(0, Math.min(1, (pointerX.value - 7) / Math.max(1, rect.width - 14))) * props.duration
  visible.value = true
  emit('active', true)
}
function begin(e) {
  if (props.disabled) return
  move(e)
  dragging.value = true
  e.currentTarget.setPointerCapture?.(e.pointerId)
}
function lostCapture() { if (dragging.value) cancel() }
function input(e) {
  hoverTime.value = Number(e.target.value)
  const rect = measure()
  pointerX.value = 7 + hoverTime.value / Math.max(1, props.duration) * (rect.width - 14)
  visible.value = true
  emit('active', true)
  emit('input', e)
}
function commit(e) { dragging.value = false; emit('commit', e); leave() }
function end(e) { dragging.value = false; if (e.pointerType !== 'mouse') leave() }
function cancel() { dragging.value = false; emit('cancel'); leave() }
function leave() { if (!dragging.value) { visible.value = false; emit('active', false) } }
</script>

<style scoped>
.seekbar { position: relative; width: 100%; height: 28px; flex: none; display: flex; align-items: center; }
.seek-rail { position: absolute; left: 7px; right: 7px; height: 3px; background: #ffffff30; border-radius: 4px; overflow: hidden; transition: height .15s; pointer-events: none; }
.seek-buffer, .seek-played { position: absolute; inset: 0 auto 0 0; background: #ffffff50; border-radius: inherit; }
.seek-played { background: var(--player-accent, #e50914); }
input { appearance: none; position: relative; width: 100%; height: 28px; padding: 0; margin: 0; border: 0; background: transparent;
  border-radius: 4px; touch-action: none; cursor: pointer; }
input:disabled { cursor: default; }
input::-webkit-slider-runnable-track { height: 3px; background: transparent; }
input::-webkit-slider-thumb { appearance: none; width: 14px; height: 14px; margin-top: -5.5px; border: 0; border-radius: 50%;
  background: var(--player-accent, #e50914); box-shadow: 0 1px 4px #0005; opacity: 0; transition: opacity .15s; }
input::-moz-range-track { height: 3px; background: transparent; }
input::-moz-range-thumb { width: 14px; height: 14px; border: 0; border-radius: 50%; background: var(--player-accent, #e50914); opacity: 0; }
.seekbar:hover .seek-rail, .seekbar:focus-within .seek-rail { height: 5px; }
.seekbar:hover input::-webkit-slider-thumb, input:focus-visible::-webkit-slider-thumb { opacity: 1; }
.seekbar:hover input::-moz-range-thumb, input:focus-visible::-moz-range-thumb { opacity: 1; }
.preview { position: absolute; bottom: 32px; width: 160px; padding: 2px; border: 1px solid #ffffff25;
  border-radius: 10px; background: #202024; box-shadow: 0 6px 24px #0008; color: #fff; pointer-events: none; text-align: center; overflow: hidden; font-size: .75rem; }
.preview-image { background-repeat: no-repeat; border-radius: 7px 7px 0 0; }
.preview span { display: block; padding: 6px 4px; font-weight: 500; font-variant-numeric: tabular-nums; }
.preview small { display: block; width: 160px; max-width: 100%; padding: 0 4px 6px; box-sizing: border-box; color: #aaa; font-size: .625rem; }
@media (hover: none) {
  input::-webkit-slider-thumb { opacity: 1; }
  input::-moz-range-thumb { opacity: 1; }
}
@media (prefers-reduced-motion: reduce) {
  .seek-rail, input::-webkit-slider-thumb { transition: none; }
}
</style>
