<template>
  <div v-show="budget.segments.length" class="budget-bar" role="group" aria-label="调整缓存预算分配"
    :aria-describedby="hintId" :data-scale-gb="scaleGb">
    <div ref="rail" class="budget-bar-rail">
      <div class="budget-bar-track" :class="{ 'is-over': budget.excessGb }">
        <span v-for="part in segments" :key="part.key" class="budget-segment" :class="'budget-segment--' + part.key"
          :data-budget-part="part.key" :style="{ width: part.percent + '%' }" :aria-hidden="!canEditSegment(part) || undefined"
          @pointerenter="showPointer($event, part.key)" @pointermove="hover($event, part.key)" @pointerleave="leavePointer">
          <JzButton v-if="canEditSegment(part)" variant="ghost" class="budget-segment-edit" :data-quota="part.key"
            :aria-label="part.key === 'transcode' ? '精确设置播放缓存额度' : '精确设置进度缩略图额度'"
            :disabled="disabled" @click="requestEdit(part.key, $event.currentTarget)">
            <span class="budget-segment-label"><span>{{ part.label }}</span><strong>{{ gb(part.gb) }}</strong></span>
            <AppIcon name="edit" :size="16" class="budget-segment-edit-icon" />
          </JzButton>
          <span v-else class="budget-segment-label"><span>{{ part.label }}</span><strong>{{ gb(part.gb) }}</strong></span>
        </span>
        <span v-if="limitPercent < 100" class="budget-overflow-zone" :style="{ left: limitPercent + '%' }" aria-hidden="true" />
        <span v-if="continues" class="budget-continuation" aria-hidden="true" />
      </div>
      <div v-if="budget.excessGb || limitPercent < 100" class="budget-limit-marker" :style="{ left: limitPercent + '%' }" aria-hidden="true">
        <span :class="{ 'align-end': limitPercent > 50 }">总预算 {{ gb(modelValue.data_warning_gb) }}</span>
      </div>
      <JzButton v-for="handle in handles" :key="handle.part" variant="ghost" role="slider"
        class="budget-handle" :class="'budget-handle--' + handle.part" :style="{ left: handle.position + '%' }"
        :data-budget-handle="handle.part"
        :aria-label="handle.label" aria-orientation="horizontal" :aria-valuemin="0.1" :aria-valuemax="102400"
        :aria-valuenow="handle.value" :aria-valuetext="handle.valueText" :aria-describedby="handleDescription(handle.part)"
        :disabled="disabled || !budget.editable"
        @focus="showFocus($event.currentTarget, handle.part)" @blur="blurHandle"
        @pointerenter="showPointer($event, handle.part)" @pointerleave="leavePointer"
        @keydown="keydown($event, handle.part)" @pointerdown="pointerdown($event, handle.part)"
        @pointermove="pointermove($event, handle.part)" @pointerup="pointerend($event, false)"
        @pointercancel="pointerend($event, true)" @lostpointercapture="pointerend($event, true)">
        <span class="budget-grip" aria-hidden="true" />
      </JzButton>
    </div>
  </div>
  <p v-show="budget.segments.length" :id="hintId" class="hint budget-drag-hint">拖动蓝、绿把手分别调整缓存额度，或点击缓存色块、下方额度精确输入。可先临时超配，配平后保存。</p>
  <Teleport to="body">
    <div v-if="tooltipInfo" :id="tooltipId" ref="tooltipElement" class="budget-tooltip" role="tooltip" :style="tooltipStyle">
      <strong>{{ tooltipInfo.label }} {{ gb(tooltipInfo.gb) }}</strong>
      <span :class="{ 'is-over': budget.excessGb }">{{ balanceText }}</span>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, useId, watch } from 'vue'
import { allocationRange, cacheBudget, moveAllocationBoundary } from '../cacheBudget.js'
import JzButton from './JzButton.vue'
import AppIcon from './AppIcon.vue'

const props = defineProps({ modelValue: { type: Object, required: true }, usage: { type: Object, default: null }, disabled: Boolean })
const emit = defineEmits(['update:modelValue', 'change', 'edit'])
const instanceId = useId(), hintId = 'budget-drag-' + instanceId, tooltipId = 'budget-tooltip-' + instanceId
const rail = ref(null), tooltipElement = ref(null), drag = shallowRef(null), tooltip = shallowRef(null)
const railWidth = ref(0)
const viewport = ref({ width: 320, height: 844 }), tooltipSize = ref({ width: 240, height: 64 })
const budget = computed(() => cacheBudget(props.modelValue, props.usage))
const scaleGb = computed(() => drag.value?.scale ?? budget.value.scaleGb ?? 1)
const segments = computed(() => {
  let offset = 0
  return budget.value.segments.map(part => {
    const percent = part.gb / scaleGb.value * 100
    const visiblePercent = Math.max(0, Math.min(percent, 100 - offset))
    offset += percent
    return { ...part, percent, visiblePercent }
  })
})
const limitPercent = computed(() => Math.min(100, props.modelValue.data_warning_gb / scaleGb.value * 100))
const continues = computed(() => budget.value.allocatedGb > scaleGb.value)
const balanceText = computed(() => budget.value.excessGb ? `超出预算 ${gb(budget.value.excessGb)}`
  : budget.value.freeGb ? `剩余 ${gb(budget.value.freeGb)}` : '预算刚好用满')
const handles = computed(() => {
  if (!segments.value.length) return []
  const [base, playback, previews] = segments.value
  return [
    { part: 'transcode', label: '调整播放缓存额度', position: Math.min(100, base.percent + playback.percent),
      value: playback.gb, valueText: `播放缓存 ${gb(playback.gb)}，${balanceText.value}；缩略图额度保持不变` },
    { part: 'previews', label: '调整进度缩略图额度', position: Math.min(100, base.percent + playback.percent + previews.percent),
      value: previews.gb, valueText: `进度缩略图 ${gb(previews.gb)}，${balanceText.value}；播放额度保持不变` },
  ]
})
const tooltipInfo = computed(() => !props.disabled && tooltip.value && budget.value.editable
  ? budget.value.segments.find(part => part.key === tooltip.value.part) : null)
const tooltipStyle = computed(() => {
  if (!tooltip.value) return {}
  const { x, y, pointerType } = tooltip.value, { width, height } = tooltipSize.value
  const left = Math.max(8, Math.min(x - width / 2, viewport.value.width - width - 8))
  const above = y - height - (pointerType === 'touch' ? 32 : 12)
  const top = Math.max(8, Math.min(above >= 8 ? above : y + 20, viewport.value.height - height - 8))
  return { left: left + 'px', top: top + 'px' }
})
function gb(value) { return value.toLocaleString('zh-CN', { maximumFractionDigits: 1 }) + ' GB' }
function canEditSegment(part) {
  return ['transcode', 'previews'].includes(part.key) && part.visiblePercent * Math.max(0, railWidth.value - 2) / 100 >= 44
}
function cancelInteraction() { finish(true); tooltip.value = null }
function requestEdit(part, anchor) { cancelInteraction(); emit('edit', { part, anchor }) }
defineExpose({ cancelInteraction })
function handleDescription(part) { return [hintId, tooltipInfo.value?.key === part ? tooltipId : ''].filter(Boolean).join(' ') }
function showPointer(event, part) {
  if (drag.value || !budget.value.editable || props.disabled) return
  tooltip.value = { part, mode: 'pointer', x: event.clientX, y: event.clientY, pointerType: event.pointerType }
}
function hover(event, part) { if (!drag.value) showPointer(event, part) }
function leavePointer() {
  if (drag.value || tooltip.value?.mode !== 'pointer') return
  const focused = rail.value?.querySelector('.budget-handle:focus')
  if (focused) showFocus(focused, focused.dataset.budgetHandle)
  else tooltip.value = null
}
function showFocus(element, part) {
  if (!budget.value.editable || props.disabled) return
  const rect = element.getBoundingClientRect()
  tooltip.value = { part, mode: 'focus', element, x: rect.x + rect.width / 2, y: rect.y }
}
watch([() => tooltipInfo.value?.key, () => tooltipInfo.value?.gb, balanceText], async () => {
  await nextTick()
  if (disposed) return
  const rect = tooltipElement.value?.getBoundingClientRect()
  if (rect) tooltipSize.value = { width: rect.width, height: rect.height }
  if (tooltip.value?.mode === 'focus') showFocus(tooltip.value.element, tooltip.value.part)
})
function update(part, value) {
  const next = moveAllocationBoundary(props.modelValue, props.usage, part, value)
  if (next.transcode_cache_gb === props.modelValue.transcode_cache_gb && next.preview_cache_gb === props.modelValue.preview_cache_gb) return
  emit('update:modelValue', next)
  emit('change')
}
function pointerdown(event, part) {
  if (props.disabled || !budget.value.editable || !event.isPrimary || event.button !== 0 || drag.value) return
  const width = rail.value?.getBoundingClientRect().width
  if (!width) return
  event.preventDefault()
  event.currentTarget.focus({ preventScroll: true })
  event.currentTarget.setPointerCapture(event.pointerId)
  drag.value = { id: event.pointerId, element: event.currentTarget, part, x: event.clientX, width,
    scale: budget.value.scaleGb, value: allocationRange(props.modelValue, props.usage, part).value,
    playback: props.modelValue.transcode_cache_gb, previews: props.modelValue.preview_cache_gb }
  tooltip.value = { part, mode: 'pointer', x: event.clientX, y: event.clientY, pointerType: event.pointerType }
}
function pointermove(event, part) {
  if (!drag.value) { hover(event, part); return }
  if (drag.value.id !== event.pointerId) return
  if (props.disabled || !budget.value.editable) { finish(true); return }
  const gesture = drag.value
  tooltip.value = { part: gesture.part, mode: 'pointer', x: event.clientX, y: event.clientY, pointerType: event.pointerType }
  update(gesture.part, gesture.value + Math.round((event.clientX - gesture.x) / gesture.width * gesture.scale * 10) / 10)
}
function pointerend(event, undo) {
  if (drag.value?.id === event.pointerId) finish(undo)
}
function finish(undo) {
  if (!drag.value) return
  const previous = drag.value
  drag.value = null
  if (previous.element.hasPointerCapture(previous.id)) previous.element.releasePointerCapture(previous.id)
  if (undo) {
    emit('update:modelValue', { ...props.modelValue, transcode_cache_gb: previous.playback, preview_cache_gb: previous.previews })
    emit('change')
    tooltip.value = null
  } else if (previous.element === document.activeElement) {
    nextTick(() => { if (!disposed) showFocus(previous.element, previous.part) })
  }
}
function keydown(event, part) {
  if (event.key === 'Escape' && (drag.value || tooltip.value)) {
    event.preventDefault(); event.stopPropagation(); finish(true); tooltip.value = null; return
  }
  if (props.disabled || !budget.value.editable || drag.value) return
  const range = allocationRange(props.modelValue, props.usage, part)
  let value
  if (['ArrowRight', 'ArrowUp', 'ArrowLeft', 'ArrowDown'].includes(event.key)) {
    value = range.value + (['ArrowRight', 'ArrowUp'].includes(event.key) ? 1 : -1) * (event.shiftKey ? 1 : 0.1)
  } else if (event.key === 'PageUp' || event.key === 'PageDown') value = range.value + (event.key === 'PageUp' ? 10 : -10)
  else if (event.key === 'Home') value = range.min
  else if (event.key === 'End') value = range.max
  else return
  event.preventDefault()
  showFocus(event.currentTarget, part)
  update(part, value)
}
// Only changes to the gesture's inputs cancel it. Crossing the budget does not.
watch([() => props.disabled, () => props.modelValue.data_warning_gb, () => budget.value.reservedGb,
  () => budget.value.available, () => budget.value.editable], () => {
  finish(true)
  if (props.disabled || !budget.value.editable) tooltip.value = null
}, { flush: 'sync' })
function blurHandle() { finish(true); tooltip.value = null }
function escapeHover(event) {
  if (event.key === 'Escape' && !event.defaultPrevented) tooltip.value = null
}
function layoutChanged() {
  viewport.value = { width: window.innerWidth, height: window.innerHeight }
  railWidth.value = rail.value.getBoundingClientRect().width
  if (drag.value && Math.abs(railWidth.value - drag.value.width) > 0.5) finish(true)
  if (tooltip.value?.mode === 'focus') showFocus(tooltip.value.element, tooltip.value.part)
  else if (!drag.value) tooltip.value = null
}
function scroll() {
  finish(true)
  if (tooltip.value?.mode === 'focus') showFocus(tooltip.value.element, tooltip.value.part)
  else tooltip.value = null
}
let observer, disposed = false
onMounted(() => {
  layoutChanged()
  observer = new ResizeObserver(layoutChanged)
  observer.observe(rail.value)
  window.addEventListener('resize', layoutChanged)
  window.addEventListener('scroll', scroll, true)
  window.addEventListener('blur', blurHandle)
  window.addEventListener('keydown', escapeHover)
})
onBeforeUnmount(() => {
  disposed = true
  finish(false)
  observer?.disconnect()
  window.removeEventListener('resize', layoutChanged)
  window.removeEventListener('scroll', scroll, true)
  window.removeEventListener('blur', blurHandle)
  window.removeEventListener('keydown', escapeHover)
})
</script>

<style scoped>
.budget-bar { position: relative; height: 136px; margin-top: 4px; }
.budget-bar-rail { position: absolute; inset-inline: 22px; top: 44px; height: 48px; }
.budget-bar-track { position: relative; display: flex; box-sizing: border-box; height: 100%; overflow: hidden;
  border: 1px solid var(--jz-border-strong); border-radius: var(--jz-radius-s); }
.budget-bar-track.is-over { border-color: var(--jz-danger); }
.budget-segment { display: flex; align-items: center; justify-content: center; flex: 0 0 auto; overflow: hidden;
  container: budget-segment / inline-size; font-size: var(--jz-font-s); color: var(--jz-bg); }
.budget-segment--base { background: var(--jz-text-dim); }
.budget-segment--transcode { background: var(--jz-blue-chip); }
.budget-segment--previews { background: var(--jz-green); }
.budget-segment--free { background: var(--jz-surface-3); color: var(--jz-text); }
.budget-segment-label { display: none; flex-direction: column; gap: 1px; padding-inline: 8px; white-space: nowrap; line-height: 1.4; }
.budget-segment-label strong { font-weight: 600; }
.budget-segment-edit { width: 100%; height: 100%; min-height: 44px; border: 0; border-radius: 0; padding: 0;
  background: transparent; color: inherit; font-size: inherit; }
.budget-segment-edit:hover:not(:disabled), .budget-segment-edit:focus-visible { background: #ffffff18; color: var(--jz-bg); }
.budget-segment-edit:focus-visible { outline-offset: -3px; }
@container budget-segment (min-width: 8em) {
  .budget-segment-label { display: flex; }
  .budget-segment-edit-icon { display: none; }
}
.budget-overflow-zone { position: absolute; inset-block: 0; right: 0; pointer-events: none;
  background: repeating-linear-gradient(135deg, transparent 0 5px, color-mix(in srgb, var(--jz-danger) 35%, transparent) 5px 7px); }
.budget-continuation { position: absolute; right: 0; inset-block: 0; width: 8px; border-right: 3px solid var(--jz-danger);
  background: var(--jz-danger-soft); pointer-events: none; }
.budget-limit-marker { position: absolute; top: 0; height: 48px; width: 0; border-left: 2px dashed var(--jz-danger); pointer-events: none; }
.budget-limit-marker > span { position: absolute; bottom: calc(100% + 4px); left: 0; white-space: nowrap;
  padding: 1px 4px; border-radius: 3px; color: var(--jz-danger); background: var(--jz-bg); font-size: var(--jz-font-s); }
.budget-limit-marker > .align-end { left: auto; right: 0; }
.budget-handle { position: absolute; width: 44px; min-width: 44px; height: 44px; min-height: 44px; padding: 0;
  transform: translateX(-50%); border: 0; border-radius: var(--jz-radius-s); touch-action: none; cursor: ew-resize; }
.budget-handle--transcode { top: -44px; color: var(--jz-blue-chip); }
.budget-handle--previews { top: 48px; color: var(--jz-green); }
.budget-handle:hover:not(:disabled), .budget-handle:focus-visible { color: var(--jz-text); background: var(--jz-surface-3); }
.budget-grip { position: relative; width: 14px; height: 24px; box-sizing: border-box; border: 2px solid var(--jz-text);
  border-radius: 4px; background: var(--jz-blue-chip); box-shadow: 0 1px 4px #0008; }
.budget-handle--previews .budget-grip { background: var(--jz-green); }
.budget-grip::after { content: ''; position: absolute; top: 5px; bottom: 5px; left: 4px; width: 2px; background: var(--jz-text); }
.budget-handle--transcode::after, .budget-handle--previews::after { content: ''; position: absolute; left: 21px; width: 2px; height: 10px; background: currentColor; pointer-events: none; }
.budget-handle--transcode::after { bottom: 0; }
.budget-handle--previews::after { top: 0; }
.budget-drag-hint { margin: 0; }
.budget-tooltip { position: fixed; z-index: var(--jz-z-menu); box-sizing: border-box; display: flex; flex-direction: column;
  gap: 3px; width: max-content; max-width: min(280px, calc(100vw - 16px)); padding: 10px 12px; pointer-events: none;
  background: var(--jz-surface-2); border: 1px solid var(--jz-border-strong); border-radius: var(--jz-radius-s);
  box-shadow: var(--jz-shadow-menu); color: var(--jz-text); font-size: var(--jz-font-m); line-height: 1.5; overflow-wrap: anywhere; }
.budget-tooltip strong { font-weight: 600; }
.budget-tooltip > span { color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.budget-tooltip > .is-over { color: var(--jz-danger); }
</style>
