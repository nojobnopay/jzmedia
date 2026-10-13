<template>
  <div ref="control" class="volume-control" @pointerenter="hoverOpen" @pointerleave="hoverLeave"
    @focusin="clearHoverClose" @focusout="focusLeave" @click.stop @dblclick.stop @keydown="onKey">
    <JzButton ref="trigger" variant="ghost" icon-only class="player-icon-btn volume-trigger" :disabled="disabled"
      aria-label="音量调节" :title="muted || volume === 0 ? '音量调节（已静音）' : '音量调节'"
      :aria-expanded="open" :aria-controls="open ? panelId : undefined" @click="toggle" @focus="focusOpen">
      <PlayerIcon :name="muted || volume === 0 ? 'mute' : 'volume'" />
    </JzButton>
    <section v-if="open" :id="panelId" class="volume-panel" :class="{ 'system-volume': !volumeSupported }"
      :style="panelStyle" aria-label="音量调节">
      <template v-if="volumeSupported">
        <output class="volume-percent" :for="rangeId">{{ Math.round(displayVolume * 100) }}%</output>
        <div class="volume-range-wrap">
          <span class="volume-rail" aria-hidden="true"><span :style="{ height: displayVolume * 100 + '%' }"></span></span>
          <input :id="rangeId" class="volume-range" type="range" min="0" max="100" step="1"
            :value="displayVolume * 100" :disabled="disabled" aria-label="音量" aria-orientation="vertical"
            :aria-valuetext="Math.round(displayVolume * 100) + '%'" @input="setVolume" @keydown="rangeKey"
            @pointerdown="beginDrag" @pointerup="endDrag" @pointercancel="endDrag" @lostpointercapture="endDrag" />
        </div>
      </template>
      <p v-else class="volume-system-hint">请使用设备音量键</p>
      <JzButton variant="ghost" icon-only class="player-icon-btn volume-mute" :disabled="disabled"
        :aria-label="muted ? '取消静音' : '静音'" :aria-pressed="muted" :title="muted ? '取消静音' : '静音'" @click="$emit('toggle-mute')">
        <PlayerIcon :name="muted ? 'mute' : 'volume'" :size="20" />
      </JzButton>
    </section>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, useId, watch } from 'vue'
import JzButton from './JzButton.vue'
import PlayerIcon from './PlayerIcon.vue'
import { createVolumePopoverState, normalizeVolume, volumeForKey } from '../volumeControls.js'

const props = defineProps({
  volume: { type: Number, default: 1 }, muted: Boolean, open: Boolean, disabled: Boolean,
  panelHeight: { type: Number, default: 240 }, volumeSupported: { type: Boolean, default: true },
})
const emit = defineEmits(['toggle', 'open', 'close', 'volume-change', 'toggle-mute'])
const control = ref(null)
const trigger = ref(null)
const instanceId = useId()
const panelId = `player-volume-${instanceId}`
const rangeId = `${panelId}-range`
const displayVolume = computed(() => props.muted ? 0 : normalizeVolume(props.volume))
const panelStyle = computed(() => {
  const limit = Number.isFinite(props.panelHeight) ? Math.max(0, Math.min(240, props.panelHeight)) : 240
  return { height: props.volumeSupported ? limit + 'px' : undefined, maxHeight: limit + 'px' }
})
const openState = createVolumePopoverState()
let dragging = false
let restoringFocus = false
let pointerInside = false
let hoverCloseTimer = null

function focus(options) {
  restoringFocus = true
  trigger.value?.focus(options)
  restoringFocus = false
}
defineExpose({ focus })

function hoverOpen(event) {
  if (event.pointerType !== 'mouse') return
  pointerInside = true
  clearHoverClose()
  if (!props.disabled && window.matchMedia('(hover: hover) and (pointer: fine)').matches) emitAction(openState.openTemporarily(props.open, 'hover'))
}
function clearHoverClose() {
  clearTimeout(hoverCloseTimer)
  hoverCloseTimer = null
}
function scheduleHoverClose() {
  clearHoverClose()
  // Allow crossing the gap between the trigger and its raised panel.
  hoverCloseTimer = setTimeout(() => {
    hoverCloseTimer = null
    if (!props.open || pointerInside) return
    const focused = control.value?.contains(document.activeElement) && document.activeElement.matches(':focus-visible')
    emitAction(openState.releaseTemporary({ focused, dragging }))
  }, 180)
}
function hoverLeave(event) {
  if (event.pointerType !== 'mouse') return
  pointerInside = false
  scheduleHoverClose()
}
function focusLeave(event) {
  if (!pointerInside && !control.value?.contains(event.relatedTarget)) scheduleHoverClose()
}
function focusOpen(event) {
  if (!props.disabled && !restoringFocus && event.target.matches(':focus-visible')) emitAction(openState.openTemporarily(props.open, 'focus'))
}
function emitAction(action) { if (action) emit(action.event, action.options ?? action.source) }
function toggle() { if (!props.disabled) { clearHoverClose(); emitAction(openState.click(props.open)) } }
function setVolume(event) { if (!props.disabled) emit('volume-change', normalizeVolume(Number(event.target.value) / 100)) }
function rangeKey(event) {
  if (props.disabled) return
  const value = volumeForKey(event.key, displayVolume.value)
  if (value === null) return
  event.preventDefault()
  event.stopPropagation()
  emit('volume-change', value)
}
function beginDrag(event) {
  clearHoverClose()
  dragging = true
  event.currentTarget.setPointerCapture?.(event.pointerId)
}
function endDrag() { dragging = false; if (!pointerInside) scheduleHoverClose() }
function onKey(event) {
  if (event.key !== 'Escape' || !props.open) return
  event.preventDefault()
  event.stopPropagation()
  if (dragging) return
  openState.close()
  emit('close')
  focus()
}
watch(() => props.open, open => { if (!open) { clearHoverClose(); dragging = false; openState.close() } })
onBeforeUnmount(clearHoverClose)
</script>

<style scoped>
.volume-control { position: relative; display: flex; align-items: center; flex: none; }
.volume-trigger, .volume-mute { width: var(--jz-touch-target); min-width: var(--jz-touch-target); height: var(--jz-touch-target); min-height: var(--jz-touch-target); }
.volume-panel { position: absolute; z-index: 11; bottom: calc(100% + 8px); left: 50%; transform: translateX(-50%);
  display: flex; flex-direction: column; align-items: center; gap: var(--jz-gap-xs); width: 64px; box-sizing: border-box;
  padding: var(--jz-gap-s) var(--jz-gap-xs); border: 1px solid color-mix(in srgb, var(--jz-text) 14%, transparent);
  border-radius: var(--jz-radius-l); background: color-mix(in srgb, var(--jz-surface) 90%, transparent);
  backdrop-filter: blur(12px); color: var(--jz-text); box-shadow: var(--jz-shadow-menu); }
.volume-panel::after { content: ''; position: absolute; bottom: -10px; left: 0; right: 0; height: 10px; }
.volume-percent { flex: none; font-size: var(--jz-font-s); font-variant-numeric: tabular-nums; line-height: 20px; }
.volume-range-wrap { position: relative; flex: 1; min-height: 36px; width: var(--jz-touch-target); }
.volume-rail { position: absolute; top: 7px; bottom: 7px; left: calc(50% - 2px); width: 4px;
  border-radius: var(--jz-radius-pill); background: color-mix(in srgb, var(--jz-text) 28%, transparent); overflow: hidden; pointer-events: none; }
.volume-rail span { position: absolute; bottom: 0; width: 100%; background: var(--jz-text); border-radius: inherit; }
.volume-range { position: relative; writing-mode: vertical-lr; direction: rtl; appearance: none; width: 44px; height: 100%;
  min-height: 36px; padding: 0; margin: 0; border: 0; border-radius: var(--jz-radius-s); background: transparent;
  touch-action: none; cursor: pointer; }
.volume-range::-webkit-slider-runnable-track { width: 4px; height: 100%; background: transparent; }
.volume-range::-webkit-slider-thumb { appearance: none; width: 14px; height: 14px; margin-left: -5px; border: 0;
  border-radius: 50%; background: var(--jz-text); box-shadow: 0 1px 4px #0005; }
.volume-range::-moz-range-track { width: 4px; height: 100%; background: transparent; }
.volume-range::-moz-range-thumb { width: 14px; height: 14px; border: 0; border-radius: 50%; background: var(--jz-text); }
.volume-range:disabled { cursor: default; opacity: .5; }
.volume-mute { flex: none; }
.system-volume { width: 132px; }
.volume-system-hint { margin: 0; font-size: var(--jz-font-s); line-height: 1.5; text-align: center; color: var(--jz-text-dim); }
</style>
