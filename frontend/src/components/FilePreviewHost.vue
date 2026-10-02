<template>
  <Teleport v-if="active && file && player" to="body">
    <PlayerModal :key="player.kind + ':' + player.id" :version-id="player.id" :kind="player.kind" :title="file.name" :preview="true" @close="emit('close')" />
  </Teleport>
  <FilePreviewDialog v-else-if="active && file" :key="file.url" :file="file" @close="emit('close')" />
</template>

<script setup>
import { computed, defineAsyncComponent } from 'vue'
import FilePreviewDialog from './FilePreviewDialog.vue'
import { filePreviewPlayer } from '../filePreview.js'
const PlayerModal = defineAsyncComponent(() => import('./PlayerModal.vue'))
const props = defineProps({ file: { type: Object, default: null }, active: { type: Boolean, default: true } })
const emit = defineEmits(['close'])
const player = computed(() => filePreviewPlayer(props.file))
</script>
