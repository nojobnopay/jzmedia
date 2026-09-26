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
.backdrop-shade { position: absolute; inset: 0; background: linear-gradient(0deg, var(--jz-bg), transparent 55%), linear-gradient(90deg, rgba(12,12,12,.96), rgba(12,12,12,.73) 52%, rgba(12,12,12,.35)); }
@media (max-width: 700px) { .backdrop-shade { background: linear-gradient(0deg, var(--jz-bg) 4%, rgba(12,12,12,.72) 75%, rgba(12,12,12,.48)); } }
</style>
