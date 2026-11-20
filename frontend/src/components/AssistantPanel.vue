<template>
  <JzDialog :id="id" ref="dialog" class="assistant-panel assistant-glass" :title="labelBySubtitle ? subtitle : '小映'"
    :close-label="closeLabel" :presentation="docked ? 'inline' : 'default'" :busy="busy"
    teleport-disabled :assistant-entry="false" mask-class="assistant-chat-mask" @close="emit('close')">
    <template #header="{ titleId }">
      <div class="assistant-heading">
        <div class="assistant-title">
          <h2 :id="labelBySubtitle ? undefined : titleId">小映</h2>
          <div class="assistant-context"><span :id="labelBySubtitle ? titleId : undefined">{{ subtitle }}</span><template v-if="section"> · {{ section }}</template></div>
        </div>
        <details v-if="info" ref="privacy" class="assistant-privacy" @keydown.esc="dismissPrivacy" @focusout="leavePrivacy">
          <summary :title="infoLabel" :aria-label="infoLabel"><AppIcon name="info" :size="17" /></summary>
          <p :id="id + '-info'">{{ info }}</p>
        </details>
        <label class="assistant-dock" title="停靠位置">
          <AppIcon name="grid" :size="17" />
          <select aria-label="停靠位置" :value="corner" @change="emit('update:corner', $event.target.value)">
            <option v-if="!corner" value="" disabled>自定义</option>
            <option value="top-left">左上角</option><option value="top-right">右上角</option>
            <option value="bottom-left">左下角</option><option value="bottom-right">右下角</option>
          </select>
        </label>
      </div>
    </template>
    <slot />
    <template v-if="$slots.footer" #footer><slot name="footer" /></template>
  </JzDialog>
</template>

<script setup>
import { ref } from 'vue'
import AppIcon from './AppIcon.vue'
import JzDialog from './JzDialog.vue'

defineProps({
  id: { type: String, required: true }, subtitle: { type: String, required: true }, section: String,
  closeLabel: { type: String, required: true }, docked: Boolean, busy: Boolean, labelBySubtitle: Boolean,
  corner: { type: String, required: true }, info: String, infoLabel: String,
})
const emit = defineEmits(['close', 'update:corner'])
const dialog = ref(null), privacy = ref(null)
function leavePrivacy(event) {
  if (privacy.value && !privacy.value.contains(event.relatedTarget)) privacy.value.open = false
}
function dismissPrivacy(event) {
  if (privacy.value?.open) {
    event.preventDefault()
    event.stopPropagation()
    privacy.value.open = false
    privacy.value.querySelector('summary')?.focus()
  }
}
defineExpose({
  canFocus: () => dialog.value?.canFocus(),
  get element() { return dialog.value?.element },
})
</script>

<style>
/* JzDialog places its section inside a Teleport. Scope the chrome to the assistant mask. */
.assistant-chat-mask .assistant-panel {
  --chat-incoming: rgb(62 62 69 / 94%);
  --chat-outgoing: #963544;
  --chat-line: rgb(255 255 255 / 12%);
  --chat-link: #bfdcff;
  width: 100%; height: auto; min-height: 0; max-height: var(--assistant-max-height, 480px);
  border-radius: 19px;
}
@media (prefers-reduced-motion: no-preference) {
  .assistant-chat-mask .assistant-panel { animation: assistant-panel-open 130ms ease-out; }
  @keyframes assistant-panel-open { from { opacity: .65; } }
}
.assistant-chat-mask .assistant-panel .jz-dialog-header { position: relative; align-items: center; gap: 0; padding: 8px 12px 8px 17px; border-color: var(--chat-line); }
.assistant-chat-mask .assistant-panel .jz-dialog-heading { min-width: 0; }
.assistant-chat-mask .assistant-panel .jz-dialog-heading h2 { margin: 0; color: var(--jz-text); font-size: 1rem; font-weight: 650; line-height: 1.3; }
.assistant-chat-mask .assistant-panel .jz-dialog-close { width: 34px; min-width: 34px; min-height: 34px; margin: 0; padding: 0; border-radius: 50%; }
.assistant-chat-mask .assistant-panel .jz-dialog-body { flex: 1 1 auto; padding: 16px 14px; font-size: var(--jz-font-m); scrollbar-width: thin; }
.assistant-chat-mask .assistant-panel .jz-dialog-footer { display: block; padding: 0 12px 12px; border-top: 0; }
@media (max-width: 700px), (pointer: coarse) {
  .assistant-chat-mask .assistant-panel .jz-dialog-header { padding: 6px 6px 6px 14px; }
  .assistant-chat-mask .assistant-panel .jz-dialog-close { width: 44px; min-width: 44px; min-height: 44px; }
}

.assistant-thread { display: grid; grid-template-columns: minmax(0, 1fr); align-content: start; gap: 16px; }
.assistant-answer { line-height: 1.62; white-space: pre-line; }
.assistant-welcome { display: grid; gap: 9px; }
.assistant-starters { display: flex; flex-wrap: wrap; align-items: flex-start; gap: 6px; margin-left: 35px; }
.assistant-starters .jz-button { min-height: 30px; max-width: 100%; padding: 5px 10px; border-color: var(--chat-line); border-radius: 14px; background: rgb(28 28 33 / 55%); color: var(--jz-text); font-weight: 400; text-align: left; }
.assistant-starters .jz-button:hover { border-color: var(--jz-text-faint); }
.assistant-starters svg { color: var(--jz-text-dim); }
.assistant-topics { display: grid; gap: 7px; margin: 9px 0 0; padding: 9px 0 0; border-top: 1px solid var(--chat-line); list-style: none; }
.assistant-topics li { display: flex; align-items: center; flex-wrap: wrap; gap: 2px 12px; }
.assistant-topics a { display: inline-flex; align-items: center; gap: 4px; color: var(--chat-link); font-size: var(--jz-font-s); text-decoration: none; }
.assistant-topics a:hover { text-decoration: underline; }
.assistant-topics .help-link > svg { width: 15px; height: 15px; }
.assistant-footer-control { display: flex; align-items: center; width: 100%; min-width: 0; box-sizing: border-box; background: rgb(18 18 21 / 77%); border: 1px solid var(--chat-line); border-radius: 22px; }
.assistant-footer-control:focus-within { border-color: var(--jz-text-faint); }
@media (max-width: 700px), (pointer: coarse) {
  .assistant-footer-control { border-radius: 28px; }
  .assistant-topics li { display: grid; grid-template-columns: minmax(0, 1fr) fit-content(45%); gap: 7px; }
  .assistant-topics a { min-width: 0; line-height: 1.45; }
  .assistant-topics a, .assistant-starters .jz-button { min-height: 44px; }
}
</style>

<style scoped>
.assistant-heading { display: flex; align-items: center; min-width: 0; }
.assistant-title { flex: 1; min-width: 0; }
.assistant-context { margin-top: 2px; color: var(--jz-text-dim); font-size: var(--jz-font-s); line-height: 1.4; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.assistant-privacy summary, .assistant-dock { display: grid; place-items: center; position: relative; width: 34px; height: 34px; border-radius: 50%; color: var(--jz-text-dim); cursor: pointer; }
.assistant-privacy summary::-webkit-details-marker { display: none; }
.assistant-privacy summary:hover, .assistant-privacy[open] summary, .assistant-dock:hover { color: var(--jz-text); background: var(--chat-line); }
.assistant-dock:focus-within, .assistant-privacy summary:focus-visible { outline: 2px solid var(--jz-link); outline-offset: -2px; }
.assistant-dock select { position: absolute; inset: 0; width: 100%; min-height: 100%; opacity: 0; padding: 0; cursor: pointer; }
.assistant-privacy p { position: absolute; z-index: 2; top: calc(100% - 3px); right: 12px; left: 12px; margin: 0; padding: 12px 14px; border: 1px solid var(--jz-border-strong); border-radius: 10px; background: var(--jz-surface-2); box-shadow: var(--jz-shadow-menu); font-size: var(--jz-font-m); line-height: 1.6; }
@media (max-width: 700px), (pointer: coarse) {
  .assistant-privacy summary, .assistant-dock { width: 44px; min-width: 44px; height: 44px; min-height: 44px; }
}
</style>
