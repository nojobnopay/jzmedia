<template>
  <div ref="wrap" class="seekbar" @pointermove="move" @pointerleave="leave">
    <input type="range" min="0" :max="Math.floor(duration)" step="1" :value="value"
      :disabled="disabled" aria-label="播放进度" @input="input" @change="commit"
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
  disabled: Boolean, manifest: { type: Object, default: null },
})
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
.seekbar { position: relative; flex: 1; min-width: 80px; display: flex; align-items: center; }
input { width: 100%; margin: 0; touch-action: none; }
input::-webkit-slider-thumb { width: 14px; }
.preview { position: absolute; bottom: 26px; width: 160px; padding: 2px; border-radius: 5px; background: #202020; box-shadow: 0 2px 10px #0009; color: #fff; pointer-events: none; text-align: center; overflow: hidden; font-size: 12px; }
.preview-image { background-repeat: no-repeat; }
.preview span { display: block; padding: 4px; font-variant-numeric: tabular-nums; }
.preview small { display: block; width: 160px; max-width: 100%; padding: 0 4px 5px; box-sizing: border-box; color: #bbb; }
</style>
