<template>
  <div class="online-poster">
    <img v-if="image && !failed" :src="image" :alt="title" loading="lazy" decoding="async" referrerpolicy="no-referrer" @error="failed = true" />
    <ArtworkPlaceholder v-else :label="title" />
  </div>
</template>
<script setup>
import { computed, ref, watch } from 'vue'
import ArtworkPlaceholder from './ArtworkPlaceholder.vue'
const props = defineProps({ path: { type: String, default: '' }, title: { type: String, default: '' } })
const failed = ref(false)
// Provider URLs never become browser requests, even if a malformed API or an
// older server accidentally sends one. Only the authenticated image gateway.
const image = computed(() => /^\/api\/online\/catalog\/artwork\/src_[0-9a-f]{32}\/[A-Za-z0-9_-]+$/.test(props.path) ? props.path : '')
watch(() => props.path, () => { failed.value = false })
</script>
