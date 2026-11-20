<template>
  <div class="assistant-reply" :class="{ 'assistant-reply-arriving': arriving }" role="group" aria-label="小映回复">
    <SettingsAssistantProjector :phase="reaction || 'still'" class="assistant-avatar" />
    <div class="assistant-bubble"><slot /></div>
  </div>
</template>

<script setup>
import SettingsAssistantProjector from './SettingsAssistantProjector.vue'
defineProps({ arriving: Boolean, reaction: String })
</script>

<style scoped>
.assistant-reply { display: flex; align-items: flex-start; gap: 8px; min-width: 0; }
.assistant-avatar { flex: none; width: 27px; height: 27px; margin-top: 2px; }
.assistant-bubble { min-width: 0; max-width: 90%; box-sizing: border-box; padding: 10px 13px; border-radius: 5px 15px 15px 15px; background: var(--chat-incoming); color: var(--jz-text); }
.assistant-bubble :slotted(p) { margin: 0; }
@media (prefers-reduced-motion: no-preference) {
  .assistant-reply-arriving .assistant-bubble { animation: assistant-answer-in 180ms ease-out; }
  .assistant-reply-arriving .assistant-avatar[data-pose="still"] { animation: assistant-answer-nod 400ms ease-out; transform-origin: 50% 85%; }
  @keyframes assistant-answer-in { from { opacity: .5; transform: translateY(3px); } }
  @keyframes assistant-answer-nod { 35% { transform: rotate(-5deg); } 68% { transform: rotate(2deg); } }
}
</style>
