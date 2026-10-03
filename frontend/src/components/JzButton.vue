<template>
  <button ref="el" :type="type" :class="['jz-button', `jz-button--${variant}`, `jz-button--${size}`, { 'jz-button--icon': iconOnly }]"
    :disabled="disabled || loading" :aria-busy="loading || undefined">
    <Spinner v-if="loading" :size="20" />
    <AppIcon v-else-if="icon" :name="icon" :size="20" />
    <slot />
  </button>
</template>

<script setup>
import { ref } from 'vue'
import AppIcon from './AppIcon.vue'
import Spinner from './Spinner.vue'

defineProps({
  variant: { type: String, default: 'secondary', validator: value => ['primary', 'secondary', 'danger', 'ghost'].includes(value) },
  size: { type: String, default: 'default', validator: value => ['default', 'compact'].includes(value) },
  type: { type: String, default: 'button' },
  loading: Boolean,
  disabled: Boolean,
  icon: { type: String, default: '' },
  iconOnly: Boolean,
})
// Callers that position menus or restore focus must use the real button element.
const el = ref(null)
defineExpose({ el, focus: options => el.value?.focus(options) })
</script>

<style scoped>
.jz-button {
  display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  min-height: var(--jz-control-current); box-sizing: border-box; padding: 8px 16px;
  border: 1px solid var(--jz-border-strong); border-radius: var(--jz-radius-s);
  background: var(--jz-surface-2); color: var(--jz-text); font: inherit;
  font-size: var(--jz-font-m); font-weight: 500; line-height: 1.4; text-align: center;
  text-decoration: none; cursor: pointer; touch-action: manipulation;
  transition: background-color var(--jz-motion-fast), border-color var(--jz-motion-fast);
}
.jz-button:hover:not(:disabled) { background: var(--jz-surface-3); }
.jz-button--compact { min-height: var(--jz-control-compact); padding: 6px 12px; font-size: var(--jz-font-s); }
.jz-button--primary { background: var(--jz-accent); border-color: var(--jz-accent); color: var(--jz-on-accent); font-weight: 600; }
.jz-button--primary:hover:not(:disabled) { background: var(--jz-accent-hover); border-color: var(--jz-accent-hover); }
.jz-button--danger { color: var(--jz-danger); border-color: color-mix(in srgb, var(--jz-danger) 45%, var(--jz-border)); background: var(--jz-danger-soft); }
.jz-button--danger:hover:not(:disabled) { background: color-mix(in srgb, var(--jz-danger) 18%, var(--jz-surface)); }
.jz-button--ghost { border-color: transparent; background: transparent; color: var(--jz-text-dim); }
.jz-button--ghost:hover:not(:disabled) { color: var(--jz-text); }
.jz-button[aria-pressed="true"] { background: var(--jz-selected); border-color: var(--jz-accent); color: var(--jz-text); }
.jz-button:active:not(:disabled) { filter: brightness(.9); }
.jz-button--icon { width: var(--jz-control-current); min-width: var(--jz-control-current); padding: 0; aspect-ratio: 1; }
.jz-button:disabled { opacity: .5; cursor: not-allowed; }
.jz-button:focus-visible { outline: 2px solid var(--jz-link); outline-offset: 3px; }
@media (max-width: 700px), (pointer: coarse) {
  .jz-button { min-width: var(--jz-touch-target); min-height: var(--jz-touch-target); }
}
</style>
