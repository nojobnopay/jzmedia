<template>
  <details ref="menu" class="action-menu" @keydown.esc.stop="close(true)" @toggle="fit">
    <summary>{{ label }} <AppIcon name="chevron-down" :size="16" /></summary>
    <div ref="items" :style="{ transform: `translateX(${offset}px)` }" class="action-menu-items" @click.capture="onAction"><slot /></div>
  </details>
</template>
<script setup>
import AppIcon from './AppIcon.vue'

import { nextTick, onMounted, onUnmounted, ref } from 'vue'
defineProps({ label: { type: String, default: '更多操作' } })
const menu = ref(null)
const items = ref(null)
const offset = ref(0)
async function fit() {
  if (!menu.value?.open) return
  offset.value = 0
  await nextTick()
  if (!menu.value?.open || !items.value) return
  const rect = items.value.getBoundingClientRect()
  offset.value = Math.max(16 - rect.left, Math.min(0, window.innerWidth - 16 - rect.right))
}
function close(focus = false) {
  if (!menu.value) return
  menu.value.open = false
  if (focus) menu.value.querySelector('summary')?.focus()
}
function onAction(event) {
  // Restore the visible trigger before a child action opens a dialog. Its focus
  // trap can then remember this trigger without the menu stealing focus back.
  if (event.target.closest('button, a')) close(true)
}
function outside(event) {
  if (!menu.value?.contains(event.target)) close()
}
onMounted(() => {
  document.addEventListener('pointerdown', outside)
  window.addEventListener('resize', fit)
})
onUnmounted(() => {
  document.removeEventListener('pointerdown', outside)
  window.removeEventListener('resize', fit)
})
</script>
<style scoped>
.action-menu { position: relative; display: inline-block; }
summary { box-sizing: border-box; min-height: var(--jz-control-current); list-style: none; display: flex; gap: var(--jz-gap-s); align-items: center; cursor: pointer; border: 1px solid var(--jz-border-strong); border-radius: var(--jz-radius-s); padding: var(--jz-gap-s) var(--jz-gap-m); font-size: var(--jz-font-m); color: var(--jz-text); background: var(--jz-surface-2); }
summary::-webkit-details-marker { display: none; }
.action-menu-items { position: absolute; top: calc(100% + 8px); right: 0; z-index: 40; width: max-content; min-width: 180px; max-width: min(300px, calc(100vw - 32px)); background: var(--jz-surface-2); border: 1px solid var(--jz-border-strong); border-radius: var(--jz-radius-m); padding: 6px; box-shadow: 0 12px 32px #0008; }
.action-menu-items :deep(button), .action-menu-items :deep(a) { display: flex; justify-content: flex-start; width: 100%; text-align: left; text-decoration: none; box-sizing: border-box; }
.action-menu-items :deep(button:hover), .action-menu-items :deep(a:hover) { background: var(--jz-surface-3); }
summary:focus-visible { outline: 2px solid var(--jz-link); outline-offset: 3px; }
</style>
