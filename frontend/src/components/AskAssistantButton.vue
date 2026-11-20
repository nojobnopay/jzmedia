<template>
  <JzButton v-if="assistant?.available.value" variant="ghost" icon-only type="button" class="ask-assistant"
    data-assistant-entry aria-label="问小映" title="问小映" @click.stop="assistant.open($event.currentTarget)">
    <SettingsAssistantProjector phase="still" />
  </JzButton>
</template>

<script setup>
import { inject } from 'vue'
import { assistantAccess } from '../assistantPage.js'
import JzButton from './JzButton.vue'
import SettingsAssistantProjector from './SettingsAssistantProjector.vue'
const assistant = inject(assistantAccess, null)
</script>

<style scoped>
.ask-assistant { flex: none; }
.ask-assistant :deep(.projector-character) { width: 24px; height: 24px; }
</style>
