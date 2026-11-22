<template>
  <img v-if="src && !failed" :key="src" ref="image" :src="src" alt=""
    loading="lazy" decoding="async" @error="onError" />
  <ArtworkPlaceholder v-else class="no-poster" kind="poster" :label="title" />
</template>

<script setup>
import { ref, watch } from 'vue'
import ArtworkPlaceholder from './ArtworkPlaceholder.vue'

const props = defineProps({ src: String, title: String })
const image = ref(null)
const failed = ref(false)
watch(() => props.src, () => { failed.value = false })
function onError(event) {
  if (event.currentTarget === image.value) failed.value = true
}
</script>
