<template>
  <div ref="frame" class="assistant-launcher-frame">
    <div v-if="open && modal" class="assistant-chat-backdrop" aria-hidden="true" @click.self="!busy && emit('toggle')"></div>
    <JzButton ref="button" class="assistant-launcher" variant="ghost" icon-only
      :data-motion="phase" :data-paused="pageHidden || undefined" :style="style" aria-label="问设置助手" :aria-expanded="open"
      :aria-controls="open ? 'settings-assistant-panel' : undefined" aria-describedby="assistant-position-help"
      :title="open ? '收起设置助手' : '问设置助手'" :disabled="busy"
      @pointerdown="start" @pointermove="move" @pointerup="finish" @pointercancel="cancel" @lostpointercapture="cancel"
      @keydown="positionKey" @click="activate">
      <SettingsAssistantProjector :phase="phase" :paused="pageHidden" :style="{ '--projector-lean': lean + 'deg' }" />
    </JzButton>
    <div v-if="open && placement" class="assistant-chat-window" :data-edge="placement.edge" :style="windowStyle">
      <slot />
    </div>
    <span id="assistant-position-help" class="launcher-sr">可拖动调整位置，方向键移动，Home 恢复右上角。</span>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import JzButton from './JzButton.vue'
import SettingsAssistantProjector from './SettingsAssistantProjector.vue'
import { placeAssistant } from '../settingsAssistantPlacement.js'

const props = defineProps({ open: Boolean, modal: Boolean, motion: { type: String, default: 'idle' }, busy: Boolean, section: String })
const emit = defineEmits(['toggle', 'position'])
const key = 'jz.settings-assistant.position'
const defaults = { side: 'right', height: 0 }
function loadPosition() {
  try {
    const p = JSON.parse(localStorage.getItem(key))
    if (['left', 'right'].includes(p?.side) && Number.isFinite(p.height) && p.height >= 0 && p.height <= 1) return p
  } catch { /* The launcher is usable when storage is unavailable. */ }
  return defaults
}
const saved = ref(loadPosition()), frame = ref(null), button = ref(null)
const dragging = ref(false), point = ref(null)
const settling = ref(false), lean = ref(0), pageHidden = ref(false)
const phase = computed(() => dragging.value ? 'drag' : settling.value ? 'settle' : props.motion)
const style = computed(() => point.value ? { left: point.value.x + 'px', top: point.value.y + 'px' } : {})
const clamp = (value, min, max) => Math.max(min, Math.min(value, max))
let gesture, blockedClick = false, animation = 0, observer, resizeObserver, viewport
let settleTimer, ownerDocument
let bounds = null, obstacles = []
const placement = computed(() => props.open && point.value && placeAssistant(point.value, bounds))
const windowStyle = computed(() => {
  const p = placement.value
  return {
    left: p.x + 'px', top: p.y + 'px', width: p.width + 'px',
    transform: `translateY(${p.offset}%)`,
    '--assistant-pointer': p.pointer, '--assistant-max-height': p.maxHeight + 'px',
  }
})

function persist(side, height) {
  saved.value = { side, height: clamp(height, 0, 1) }
  try { localStorage.setItem(key, JSON.stringify(saved.value)) } catch { /* Keep this page's position. */ }
}
watch(saved, p => {
  const vertical = p.height === 0 ? 'top' : p.height === 1 ? 'bottom' : ''
  emit('position', vertical ? vertical + '-' + p.side : '')
}, { immediate: true })

function park() {
  if (!bounds) return
  const { left, right, top, bottom, size } = bounds
  const x = saved.value.side === 'right' ? right : left
  let y = top + saved.value.height * (bottom - top)
  // A form can have both a sticky save bar and a selected-file toolbar.
  // Pick the closest unobstructed height on the chosen edge.
  const nearby = obstacles.filter(rect => x < rect.right + 6 && x + size > rect.left - 6)
  // On a short, narrow visual viewport (often the on-screen keyboard), move
  // the open anchor just far enough to leave room for the composer and reply.
  // The user's normalized resting position is unchanged when the viewport grows.
  if (props.open && placeAssistant({ x, y }, bounds).maxHeight < 240) {
    const minSpace = Math.min(250, bottom - top)
    if (Math.max(y - top, bottom - y) < minSpace) {
      y = y - top > bottom - y ? top + minSpace : bottom - minSpace
    }
  }
  const candidates = [y, top, bottom, ...nearby.flatMap(rect => [rect.top - size - 6, rect.bottom + 6])]
  const available = candidates.filter(v => v >= top && v <= bottom &&
    nearby.every(rect => v + size <= rect.top - 6 || v >= rect.bottom + 6))
  const preferred = available.sort((a, b) => Math.abs(a - y) - Math.abs(b - y))[0] ?? y
  point.value = { x, y: preferred }
}
function measure() {
  animation = 0
  const root = frame.value, el = button.value?.el
  if (!el?.getBoundingClientRect || !root) return
  const area = root.getBoundingClientRect(), size = el.offsetWidth
  const vx = viewport?.offsetLeft || 0, vy = viewport?.offsetTop || 0
  const viewRight = vx + (viewport?.width || document.documentElement.clientWidth)
  const viewBottom = vy + (viewport?.height || window.innerHeight)
  const layout = root.parentElement
  const nav = document.querySelector('.app-nav')?.getBoundingClientRect()
  const header = layout.querySelector('.settings-mobile-header')?.getBoundingClientRect()
  const left = Math.max(area.left, vx + 8)
  const right = Math.max(left, Math.min(area.right, viewRight - 8) - size)
  const bottom = Math.max(area.top, Math.min(area.bottom, viewBottom - 8) - size)
  const top = Math.min(bottom, Math.max(area.top, vy + 8, (nav?.bottom || 0) + 8, (header?.bottom || 0) + 8))
  bounds = { left, right, top, bottom, size }
  obstacles = window.innerWidth <= 700 ? [...layout.querySelectorAll('.settings-form-actions, #sec-files .fs-selection-summary, #sec-files .fs-toolbar')]
    .map(node => node.getBoundingClientRect()).filter(box => box.width && box.height && box.bottom > top && box.top < bottom + size) : []
  if (dragging.value) point.value = { x: clamp(point.value.x, left, right), y: clamp(point.value.y, top, bottom) }
  else park()
}
function schedule() {
  if (!animation) animation = window.requestAnimationFrame(measure)
}
function start(event) {
  if (event.button !== 0 || !event.isPrimary || props.busy) return
  measure()
  if (!bounds) return
  stopSettling()
  lean.value = 0
  blockedClick = false
  gesture = { id: event.pointerId, x: event.clientX, y: event.clientY, origin: point.value }
  event.currentTarget.setPointerCapture(event.pointerId)
}
function move(event) {
  if (gesture?.id !== event.pointerId || !bounds) return
  const dx = event.clientX - gesture.x, dy = event.clientY - gesture.y
  if (!dragging.value && Math.hypot(dx, dy) <= 8) return
  dragging.value = true
  const x = clamp(gesture.origin.x + dx, bounds.left, bounds.right)
  const step = x - point.value.x
  if (Math.abs(step) >= 1) lean.value = clamp(step, -8, 8)
  point.value = { x,
    y: clamp(gesture.origin.y + dy, bounds.top, bounds.bottom) }
}
function finish(event) {
  if (gesture?.id !== event.pointerId) return
  move(event)
  if (dragging.value && bounds) {
    const { left, right, top, bottom } = bounds
    persist(point.value.x < (left + right) / 2 ? 'left' : 'right',
      bottom > top ? (point.value.y - top) / (bottom - top) : saved.value.height)
  }
  cancel(event)
}
function cancel(event) {
  if (gesture?.id !== event.pointerId) return
  if (dragging.value && event.type === 'pointerup') {
    settling.value = true
    settleTimer = setTimeout(stopSettling, 340)
  }
  releaseGesture(dragging.value || event.type === 'pointercancel')
}
function stopSettling() {
  clearTimeout(settleTimer)
  settling.value = false
}
function releaseGesture(suppressClick) {
  const id = gesture.id
  blockedClick = suppressClick
  gesture = null
  dragging.value = false
  const el = button.value?.el
  if (el?.hasPointerCapture?.(id)) el.releasePointerCapture(id)
  park()
}
function syncVisibility() {
  pageHidden.value = ownerDocument.hidden
  if (pageHidden.value) {
    stopSettling()
    if (gesture) releaseGesture(true)
  }
}
function activate(event) {
  if (!(blockedClick && event.detail > 0) && !props.busy) emit('toggle')
  blockedClick = false
}
function moveToCorner(corner) {
  if (!['top-left', 'top-right', 'bottom-left', 'bottom-right'].includes(corner)) return
  const [vertical, side] = corner.split('-')
  persist(side, vertical === 'top' ? 0 : 1)
  park()
}
function positionKey(event) {
  if (event.altKey || event.metaKey || event.ctrlKey || !bounds) return
  const { side, height } = saved.value
  const step = 40 / Math.max(40, bounds.bottom - bounds.top)
  switch (event.key) {
    case 'Home': moveToCorner('top-right'); break
    case 'ArrowLeft': persist('left', height); break
    case 'ArrowRight': persist('right', height); break
    case 'ArrowUp': persist(side, height - step); break
    case 'ArrowDown': persist(side, height + step); break
    default: return
  }
  event.preventDefault()
  park()
}
defineExpose({ moveToCorner, focus: () => button.value?.focus({ preventScroll: true }) })
onMounted(() => {
  if (!frame.value?.getBoundingClientRect) return
  const layout = frame.value.parentElement
  viewport = window.visualViewport
  ownerDocument = frame.value.ownerDocument
  syncVisibility()
  ownerDocument.addEventListener('visibilitychange', syncVisibility)
  window.addEventListener('resize', schedule)
  window.addEventListener('scroll', schedule, { passive: true, capture: true })
  viewport?.addEventListener('resize', schedule)
  viewport?.addEventListener('scroll', schedule)
  resizeObserver = new ResizeObserver(schedule)
  for (const node of [frame.value, layout.querySelector('.settings-mobile-header'), document.querySelector('.app-nav')]) {
    if (node) resizeObserver.observe(node)
  }
  // File selections and form drafts can change sticky actions without scrolling.
  observer = new window.MutationObserver(schedule)
  observer.observe(layout.querySelector('.settings-main'), { subtree: true, childList: true, attributes: true,
    attributeFilter: ['class', 'style', 'open'] })
  measure()
})
watch(() => [props.section, props.open, props.modal], () => { if (bounds) schedule() }, { flush: 'post' })
onBeforeUnmount(() => {
  stopSettling()
  ownerDocument?.removeEventListener('visibilitychange', syncVisibility)
  observer?.disconnect()
  resizeObserver?.disconnect()
  window.removeEventListener('resize', schedule)
  window.removeEventListener('scroll', schedule, true)
  viewport?.removeEventListener('resize', schedule)
  viewport?.removeEventListener('scroll', schedule)
  if (animation) window.cancelAnimationFrame(animation)
})
</script>

<style scoped>
.assistant-launcher-frame { position: fixed; z-index: 12; inset: max(12px, env(safe-area-inset-top)) max(12px, env(safe-area-inset-right)) max(12px, env(safe-area-inset-bottom)) max(12px, env(safe-area-inset-left)); pointer-events: none; }
.assistant-chat-backdrop { position: fixed; inset: 0; pointer-events: auto; }
.assistant-chat-window { position: fixed; z-index: 1; }
.assistant-chat-window :deep(.assistant-chat-mask) { position: static; display: block; height: auto; padding: 0; background: transparent; pointer-events: auto; }
.assistant-chat-window :deep(.assistant-chat-mask[hidden]) { display: none; }
.assistant-chat-window:has(.assistant-chat-mask:not([hidden]))::before {
  content: ""; position: absolute; z-index: 1; width: 11px; height: 11px;
  background: var(--jz-surface-2); border: solid var(--jz-border-strong); border-width: 1px 0 0 1px;
  pointer-events: none;
}
.assistant-chat-window[data-edge="left"]::before { left: 0; top: var(--assistant-pointer); transform: translate(-50%, -50%) rotate(-45deg); }
.assistant-chat-window[data-edge="right"]::before { right: 0; top: var(--assistant-pointer); transform: translate(50%, -50%) rotate(135deg); }
.assistant-chat-window[data-edge="top"]::before { top: 0; left: var(--assistant-pointer); transform: translate(-50%, -50%) rotate(45deg); }
.assistant-chat-window[data-edge="bottom"]::before { bottom: 0; left: var(--assistant-pointer); transform: translate(-50%, 50%) rotate(225deg); }
.assistant-launcher.jz-button { position: fixed; z-index: 2; top: 80px; right: max(12px, env(safe-area-inset-right)); width: 56px; height: 56px; padding: 0; border: 0; border-radius: 50%; background: transparent; pointer-events: auto; touch-action: none; user-select: none; }
.assistant-launcher.jz-button:hover:not(:disabled) { background: var(--jz-surface-2); }
.assistant-launcher[data-motion="drag"] { cursor: grabbing; }
.assistant-launcher :deep(.projector-character) { width: 100%; height: 100%; filter: drop-shadow(0 2px 3px rgb(0 0 0 / 32%)); }
.launcher-sr { position: absolute; width: 1px; height: 1px; margin: -1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
@media (max-width: 700px) { .assistant-launcher.jz-button { width: 48px; height: 48px; } }
</style>
