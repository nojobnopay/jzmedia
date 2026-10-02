<template>
  <div class="media-backdrop" aria-hidden="true">
    <img v-if="src && failed !== src" :key="src" :src="src" alt="" @error="failed = src" />
    <div class="backdrop-shade"></div>
  </div>
</template>
<script setup>
import { ref } from 'vue'
defineProps({ src: { type: String, default: '' } })
const failed = ref('')
</script>
<style scoped>
.media-backdrop { position: absolute; inset: 0; overflow: hidden; z-index: -1; background: var(--jz-surface); pointer-events: none; }
.media-backdrop img { width: 100%; height: 100%; object-fit: cover; object-position: center 25%; }
.backdrop-shade { position: absolute; inset: 0; background: linear-gradient(0deg, var(--jz-bg), transparent 65%), linear-gradient(90deg, color-mix(in srgb, var(--jz-bg) 96%, transparent), color-mix(in srgb, var(--jz-bg) 86%, transparent) 48%, color-mix(in srgb, var(--jz-bg) 30%, transparent)); }
@media (max-width: 700px) { .backdrop-shade { background: linear-gradient(0deg, var(--jz-bg) 4%, color-mix(in srgb, var(--jz-bg) 82%, transparent) 70%, color-mix(in srgb, var(--jz-bg) 62%, transparent)); } }
</style>
