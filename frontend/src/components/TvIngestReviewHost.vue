<template>
  <TvIngestReviewDialog v-if="flow.review.value" :initial-summary="flow.review.value" @settings="settings" />
</template>
<script setup>
import { nextTick, watch } from 'vue'
import { useRouter } from 'vue-router'
import { activeFocusTraps } from '../useFocusTrap.js'
import { tvIngestFlow } from '../useTvIngestFlow.js'
import TvIngestReviewDialog from './TvIngestReviewDialog.vue'
const props = defineProps({ contextKey: { type: String, required: true }, allowed: Boolean, libraryIds: { type: Array, default: () => [] } })
const flow = tvIngestFlow
const router = useRouter()
watch(() => [props.contextKey, props.allowed, props.libraryIds.join(',')], ([key, allowed, libraries]) => flow.setContext(`${key}|${libraries}`, allowed, props.libraryIds), { immediate: true, flush: 'sync' })
watch([flow.queue, activeFocusTraps, flow.allowed, flow.blocked], async () => {
  if (activeFocusTraps.value || flow.blocked.value || !flow.allowed.value || flow.review.value) return
  await nextTick()
  if (!activeFocusTraps.value && !flow.blocked.value && flow.allowed.value) flow.takeNext()
}, { immediate: true })
function settings(library) {
  router.push({ path: '/settings', query: { sec: 'sec-tvorganize', ...(library ? { library } : {}) } })
}
</script>
