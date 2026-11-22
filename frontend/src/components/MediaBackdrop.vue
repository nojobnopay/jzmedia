<template>
  <div class="media-backdrop" aria-hidden="true">
    <img v-if="src && failed !== src" :key="src" :src="src" alt="" decoding="async" @error="failed = $event.currentTarget.getAttribute('src')" />
    <div class="backdrop-shade"></div>
  </div>
</template>
<script setup>
import { ref } from 'vue'
defineProps({ src: { type: String, default: '' } })
const failed = ref('')
</script>
<style scoped>
.media-backdrop { position: absolute; inset: 0; overflow: hidden; z-index: -1; background: var(--jz-surface-2); pointer-events: none; }
.media-backdrop img { display: block; width: 100%; height: 100%; object-fit: cover; object-position: center 30%; }
.backdrop-shade {
  position: absolute; inset: 0;
  background: linear-gradient(to top, var(--jz-bg), transparent 28%),
    linear-gradient(90deg, var(--jz-bg), color-mix(in srgb, var(--jz-bg) 96%, transparent) 27%,
      color-mix(in srgb, var(--jz-bg) 89%, transparent) 55%, color-mix(in srgb, var(--jz-bg) 16%, transparent) 76%, transparent);
}
@media (max-width: 700px) {
  .media-backdrop img { height: min(100%, 270px); object-position: 70% 22%; }
  .backdrop-shade {
    background: linear-gradient(180deg, transparent, var(--jz-bg) 245px),
      linear-gradient(90deg, color-mix(in srgb, var(--jz-bg) 92%, transparent),
        color-mix(in srgb, var(--jz-bg) 77%, transparent) 66%, color-mix(in srgb, var(--jz-bg) 42%, transparent));
  }
}
</style>
