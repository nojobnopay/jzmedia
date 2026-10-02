<template>
  <Teleport to="body">
    <div v-if="open" :class="['jz-dialog-mask', `jz-dialog-mask--${layer}`, maskClass]"
      @click.self="onBackdrop">
      <section ref="dialog" v-bind="$attrs" :class="['jz-dialog', `jz-dialog--${size}`]"
        role="dialog" aria-modal="true" :aria-labelledby="labelledby || $attrs['aria-labelledby'] || (title ? titleId : undefined)" tabindex="-1">
        <header class="jz-dialog-header">
          <div class="jz-dialog-heading"><slot name="header" :title-id="titleId"><h2 :id="titleId">{{ title }}</h2></slot></div>
          <JzButton variant="ghost" class="jz-dialog-close" :disabled="busy" :aria-label="closeLabel" @click="requestClose"><AppIcon name="close" /></JzButton>
        </header>
        <div class="jz-dialog-body"><slot /></div>
        <footer v-if="$slots.footer" class="jz-dialog-footer"><slot name="footer" /></footer>
      </section>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, getCurrentInstance, onMounted, onUnmounted, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import JzButton from './JzButton.vue'
import { useFocusTrap } from '../useFocusTrap.js'

defineOptions({ inheritAttrs: false })
const props = defineProps({
  open: { type: Boolean, default: true },
  title: { type: String, default: '' },
  labelledby: { type: String, default: '' },
  size: { type: String, default: 'medium', validator: value => ['small', 'medium', 'large'].includes(value) },
  layer: { type: String, default: 'dialog', validator: value => ['dialog', 'preview', 'auth'].includes(value) },
  busy: Boolean,
  closeOnBackdrop: { type: Boolean, default: true },
  closeOnEscape: { type: Boolean, default: true },
  closeLabel: { type: String, default: '关闭' },
  focusActive: { type: Boolean, default: true },
  maskClass: { type: String, default: '' },
})
const emit = defineEmits(['close'])
const dialog = ref(null)
const titleId = `jz-dialog-title-${getCurrentInstance().uid}`
const { isTop } = useFocusTrap(computed(() => props.open && props.focusActive), dialog)
function requestClose() { if (!props.busy) emit('close') }
function onBackdrop() { if (props.closeOnBackdrop && props.focusActive) requestClose() }
function onKeydown(event) {
  // Child controls can consume Escape (menus, comboboxes, fullscreen media).
  if (event.key !== 'Escape' || event.defaultPrevented || !props.closeOnEscape || !props.focusActive || !isTop()) return
  event.preventDefault()
  event.stopPropagation()
  requestClose()
}
// Bubble after child handlers; busy content may replace the focused button and leave focus on body.
onMounted(() => document.addEventListener('keydown', onKeydown))
onUnmounted(() => document.removeEventListener('keydown', onKeydown))
defineExpose({ element: dialog })
</script>

<style scoped>
.jz-dialog-mask { position: fixed; inset: 0; z-index: var(--jz-z-dialog); display: flex; align-items: center; justify-content: center; padding: 24px; box-sizing: border-box; background: var(--jz-overlay); }
.jz-dialog-mask--preview { z-index: var(--jz-z-preview); }
.jz-dialog-mask--auth { z-index: var(--jz-z-auth); }
.jz-dialog { display: flex; flex-direction: column; width: min(720px, 100%); min-width: 0; max-height: calc(100dvh - 48px); overflow: hidden; box-sizing: border-box; border: 1px solid var(--jz-border-strong); border-radius: var(--jz-radius-dialog); background: var(--jz-surface); color: var(--jz-text); box-shadow: var(--jz-shadow-dialog); }
.jz-dialog--small { width: min(560px, 100%); }
.jz-dialog--large { width: min(960px, 100%); }
.jz-dialog-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 20px 24px; border-bottom: 1px solid var(--jz-border); flex: none; }
.jz-dialog-heading { flex: 1; min-width: 0; }
.jz-dialog-heading :deep(h2) { margin: 0; font-size: var(--jz-font-xl); line-height: 1.5; overflow-wrap: anywhere; }
.jz-dialog-close { flex: none; width: var(--jz-control-current); min-width: var(--jz-control-current); padding: 8px; margin: -4px -8px -4px 0; }
.jz-dialog-body { min-height: 0; overflow-y: auto; overscroll-behavior: contain; padding: 24px; line-height: 1.6; overflow-wrap: anywhere; }
.jz-dialog-footer { display: flex; flex-wrap: wrap; justify-content: flex-end; align-items: center; gap: 10px; flex: none; border-top: 1px solid var(--jz-border); padding: 16px 24px; }
@media (max-width: 600px) {
  .jz-dialog-mask { padding: max(12px, env(safe-area-inset-top)) max(12px, env(safe-area-inset-right)) max(12px, env(safe-area-inset-bottom)) max(12px, env(safe-area-inset-left)); }
  .jz-dialog { max-height: calc(100dvh - 24px); }
  .jz-dialog-header, .jz-dialog-body { padding: 16px; }
  .jz-dialog-footer { padding: 14px 16px; }
  .jz-dialog-footer :deep(.jz-button--primary) { flex: 1; }
}
</style>
