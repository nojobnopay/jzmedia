<template>
  <div class="assistant-reply" role="group" aria-label="助手回复">
    <img :src="projectorArtwork.idle" width="27" height="27" alt="" draggable="false" />
    <div class="assistant-bubble"><slot /></div>
  </div>
</template>

<script setup>
import { projectorArtwork } from '../assistantArtwork.js'
</script>

<style scoped>
.assistant-reply { display: flex; align-items: flex-start; gap: 8px; min-width: 0; }
.assistant-reply img { display: block; flex: none; object-fit: contain; margin-top: 2px; }
.assistant-bubble { min-width: 0; max-width: 90%; box-sizing: border-box; padding: 10px 13px; border-radius: 5px 15px 15px 15px; background: var(--chat-incoming); color: var(--jz-text); }
.assistant-bubble :slotted(p) { margin: 0; }
</style>
