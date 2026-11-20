<template>
  <Teleport to="body" :disabled="teleportDisabled || inline">
    <div v-if="open" :hidden="awaitingModal || undefined" :class="['jz-dialog-mask', `jz-dialog-mask--${layer}`, `jz-dialog-mask--${presentation}`, maskClass]"
      @click.self="onBackdrop">
      <section ref="dialog" v-bind="$attrs" :class="['jz-dialog', `jz-dialog--${size}`, `jz-dialog--${presentation}`]"
        :role="inline ? 'complementary' : 'dialog'" :aria-modal="inline ? undefined : 'true'" :aria-labelledby="labelledby || $attrs['aria-labelledby'] || (title ? titleId : undefined)" tabindex="-1">
        <header class="jz-dialog-header">
          <div class="jz-dialog-heading"><slot name="header" :title-id="titleId"><h2 :id="titleId">{{ title }}</h2></slot></div>
          <AskAssistantButton v-if="assistantEntry && !inline && layer !== 'auth'" />
          <JzButton variant="ghost" class="jz-dialog-close" :disabled="busy" :aria-label="closeLabel" @click="requestClose"><AppIcon name="close" /></JzButton>
        </header>
        <div class="jz-dialog-body"><slot /></div>
        <footer v-if="$slots.footer" class="jz-dialog-footer"><slot name="footer" /></footer>
      </section>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, getCurrentInstance, onMounted, onUnmounted, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import AskAssistantButton from './AskAssistantButton.vue'
import JzButton from './JzButton.vue'
import { activeFocusTraps, useFocusTrap } from '../useFocusTrap.js'

defineOptions({ inheritAttrs: false })
const props = defineProps({
  open: { type: Boolean, default: true },
  title: { type: String, default: '' },
  labelledby: { type: String, default: '' },
  size: { type: String, default: 'medium', validator: value => ['small', 'medium', 'large'].includes(value) },
  presentation: { type: String, default: 'default', validator: value => ['default', 'drawer', 'popover', 'inline'].includes(value) },
  layer: { type: String, default: 'dialog', validator: value => ['dialog', 'preview', 'auth'].includes(value) },
  busy: Boolean,
  assistantEntry: { type: Boolean, default: true },
  closeOnBackdrop: { type: Boolean, default: true },
  closeOnEscape: { type: Boolean, default: true },
  closeLabel: { type: String, default: '关闭' },
  focusActive: { type: Boolean, default: true },
  teleportDisabled: Boolean,
  maskClass: { type: String, default: '' },
})
const emit = defineEmits(['close'])
const dialog = ref(null)
const titleId = `jz-dialog-title-${getCurrentInstance().uid}`
const inline = computed(() => props.presentation === 'inline')
// Narrowing a docked panel under an existing dialog must leave that dialog
// visible and in control of the keyboard until the user dismisses it.
const awaitingModal = ref(false)
watch(inline, (docked, wasDocked) => {
  awaitingModal.value = !docked && wasDocked && activeFocusTraps.value > 0
}, { flush: 'sync' })
watch(() => awaitingModal.value && !activeFocusTraps.value, released => {
  if (released) awaitingModal.value = false
})
const { isTop } = useFocusTrap(computed(() => props.open && props.focusActive && !inline.value && !awaitingModal.value), dialog)
function canFocus() { return inline.value ? !activeFocusTraps.value : isTop() }
function requestClose() { if (!props.busy) emit('close') }
function onBackdrop() { if (!inline.value && props.closeOnBackdrop && props.focusActive) requestClose() }
function onKeydown(event) {
  // Child controls can consume Escape (menus, comboboxes, fullscreen media).
  if (event.key !== 'Escape' || event.defaultPrevented || !props.closeOnEscape || !props.focusActive) return
  if (inline.value ? activeFocusTraps.value || !dialog.value?.contains(document.activeElement) : !isTop()) return
  event.preventDefault()
  event.stopPropagation()
  requestClose()
}
// Bubble after child handlers; busy content may replace the focused button and leave focus on body.
onMounted(() => document.addEventListener('keydown', onKeydown))
onUnmounted(() => document.removeEventListener('keydown', onKeydown))
defineExpose({ element: dialog, canFocus })
</script>

<style scoped>
.jz-dialog-mask { position: fixed; inset: 0; z-index: var(--jz-z-dialog); display: flex; align-items: center; justify-content: center; padding: 24px; box-sizing: border-box; background: var(--jz-overlay); }
.jz-dialog-mask--preview { z-index: var(--jz-z-preview); }
.jz-dialog-mask--auth { z-index: var(--jz-z-auth); }
.jz-dialog-mask[hidden] { display: none; }
.jz-dialog { display: flex; flex-direction: column; width: min(720px, 100%); min-width: 0; max-height: calc(100dvh - 48px); overflow: hidden; box-sizing: border-box; border: 1px solid var(--jz-border-strong); border-radius: var(--jz-radius-dialog); background: var(--jz-surface); color: var(--jz-text); box-shadow: var(--jz-shadow-dialog); }
.jz-dialog--small { width: min(560px, 100%); }
.jz-dialog--large { width: min(960px, 100%); }
.jz-dialog-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 20px 24px; border-bottom: 1px solid var(--jz-border); flex: none; }
.jz-dialog-heading { flex: 1; min-width: 0; }
.jz-dialog-heading :deep(h2) { margin: 0; font-size: var(--jz-font-xl); line-height: 1.5; overflow-wrap: anywhere; }
.jz-dialog-close { flex: none; width: var(--jz-control-current); min-width: var(--jz-control-current); padding: 8px; margin: -4px -8px -4px 0; }
.jz-dialog-body { min-height: 0; overflow-y: auto; overscroll-behavior: contain; padding: 24px; line-height: 1.6; overflow-wrap: anywhere; }
.jz-dialog-footer { display: flex; flex-wrap: wrap; justify-content: flex-end; align-items: center; gap: 10px; flex: none; border-top: 1px solid var(--jz-border); padding: 16px 24px; }
.jz-dialog-mask--drawer { justify-content: flex-end; align-items: stretch; padding: 0; }
.jz-dialog--drawer { width: min(440px, 100%); height: 100dvh; max-height: 100dvh; border-radius: 0; border-width: 0 0 0 1px; }
.jz-dialog--drawer .jz-dialog-body { flex: 1; }
.jz-dialog-mask--inline { display: block; position: static; height: 100%; min-height: 0; padding: 0; background: none; }
.jz-dialog--inline { width: 100%; min-height: 0; height: 100%; max-height: 100%; box-shadow: none; border-radius: var(--jz-radius-m); }
.jz-dialog--inline .jz-dialog-body { flex: 1; }
.jz-dialog--inline .jz-dialog-header, .jz-dialog--inline .jz-dialog-body, .jz-dialog--inline .jz-dialog-footer { padding: 14px 16px; }
.jz-dialog-mask--popover { background: var(--jz-overlay-soft); }
.jz-dialog--popover { position: absolute; left: var(--jz-popover-left, 24px); top: var(--jz-popover-top, 24px); width: min(380px, calc(100vw - 24px)); max-height: var(--jz-popover-max-height, calc(100dvh - 48px)); }
@media (max-width: 700px) {
  .jz-dialog-mask--drawer { padding: 0; }
  .jz-dialog--drawer { width: 100%; max-height: 100dvh; border: 0; }
  .jz-dialog--drawer .jz-dialog-footer { padding-bottom: max(14px, env(safe-area-inset-bottom)); }
  .jz-dialog--popover { inset: auto 12px max(12px, env(safe-area-inset-bottom)); width: auto; max-height: calc(100dvh - 48px); }
}
@media (max-width: 600px) {
  .jz-dialog-mask--default, .jz-dialog-mask--popover { padding: max(12px, env(safe-area-inset-top)) max(12px, env(safe-area-inset-right)) max(12px, env(safe-area-inset-bottom)) max(12px, env(safe-area-inset-left)); }
  .jz-dialog--default { max-height: calc(100dvh - 24px); }
  .jz-dialog-header, .jz-dialog-body { padding: 16px; }
  .jz-dialog-footer { padding: 14px 16px; }
  .jz-dialog-footer :deep(.jz-button--primary) { flex: 1; }
}
</style>
