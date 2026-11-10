<template>
  <div ref="actions" class="settings-form-actions" :style="{ '--settings-keyboard-inset': keyboardInset + 'px' }" role="group" :aria-label="label" :aria-busy="busy || undefined">
    <p v-if="status || dirty || busy" class="settings-actions-status" role="status">{{ status || (busy ? '正在处理…' : '有未保存修改') }}</p>
    <div class="settings-actions-buttons"><slot /></div>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

defineProps({
  dirty: Boolean,
  busy: Boolean,
  label: { type: String, default: '配置操作' },
  status: { type: String, default: '' },
})
const actions = ref(null), keyboardInset = ref(0)
let frame = 0, ensureFocus = false, viewport, disposed = false, generation = 0
async function update() {
  frame = 0
  const revision = ++generation
  const checkFocus = ensureFocus
  ensureFocus = false
  keyboardInset.value = window.innerWidth <= 700 && viewport ? Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop) : 0
  // The inset changes the sticky position. Measure only after Vue applies it;
  // otherwise a keyboard resize checks the previous, lower action-bar bounds.
  await nextTick()
  if (disposed || revision !== generation) return
  if (!checkFocus || window.innerWidth > 700) return
  const bar = actions.value, target = document.activeElement
  if (!bar || !target || bar.contains(target)) return
  const bounds = bar.getBoundingClientRect()
  if (!bounds.width || !bounds.height) return
  const scope = bar.closest('form, fieldset') || bar.parentElement
  if (!scope?.contains(target)) return
  const field = target.getBoundingClientRect()
  const header = document.querySelector('.settings-mobile-header')?.getBoundingClientRect()
  const top = Math.max(viewport?.offsetTop || 0, header?.bottom || 0) + 12
  const bottom = Math.min(bounds.top, (viewport?.offsetTop || 0) + (viewport?.height || window.innerHeight)) - 12
  if (bottom <= top) return
  // scrollIntoView(center) uses the layout viewport, which may remain 844px
  // tall above a 400px visual viewport. Move by the actual visible gap instead.
  const delta = field.height > bottom - top || field.top < top ? field.top - top
    : field.bottom > bottom ? field.bottom - bottom : 0
  if (delta) window.scrollBy({ top: delta, behavior: 'auto' })
}
function schedule(checkFocus = false) {
  ensureFocus ||= checkFocus
  if (!frame) frame = requestAnimationFrame(update)
}
const focusChanged = () => schedule(true)
const viewportResized = () => schedule(true)
// Panning while the keyboard is open should adjust the inset without pulling
// the user back to an input they intentionally scrolled away from.
const viewportMoved = () => schedule(false)
onMounted(() => {
  if (typeof window === 'undefined' || typeof document === 'undefined') return
  viewport = window.visualViewport
  document.addEventListener('focusin', focusChanged)
  window.addEventListener('resize', viewportResized)
  viewport?.addEventListener('resize', viewportResized)
  viewport?.addEventListener('scroll', viewportMoved)
  schedule()
})
onBeforeUnmount(() => {
  disposed = true
  generation++
  if (typeof window === 'undefined' || typeof document === 'undefined') return
  document.removeEventListener('focusin', focusChanged)
  window.removeEventListener('resize', viewportResized)
  viewport?.removeEventListener('resize', viewportResized)
  viewport?.removeEventListener('scroll', viewportMoved)
  if (frame) cancelAnimationFrame(frame)
})
</script>

<style scoped>
.settings-form-actions { margin-top: 20px; padding-block: 12px; }
.settings-actions-status { margin: 0 0 8px; color: var(--jz-text-dim); font-size: var(--jz-font-s); line-height: 1.5; overflow-wrap: anywhere; }
.settings-actions-buttons { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
/* Keyboard space also makes a short form scrollable when innerHeight stays unchanged. */
@media (max-width: 700px) {
  .settings-form-actions { position: sticky; bottom: var(--settings-keyboard-inset, 0px); z-index: 2; margin-inline: -16px; margin-bottom: calc(var(--settings-keyboard-inset, 0px) - 4px); padding: 12px 16px max(12px, env(safe-area-inset-bottom)); border-top: 1px solid var(--jz-border-strong); background: var(--jz-bg); box-shadow: 0 -8px 20px rgb(0 0 0 / 12%); }
  .settings-actions-buttons :deep(.jz-button--primary) { flex: 1 1 auto; }
}
</style>
