<template>
  <div v-if="text" class="media-overview">
    <p ref="paragraph" class="overview" :class="{ clamped: !expanded }">{{ text }}</p>
    <button v-if="collapsible" class="overview-toggle" :aria-expanded="expanded" @click="expanded = !expanded">{{ expanded ? '收起简介' : '展开简介' }}</button>
  </div>
</template>
<script setup>
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
const props = defineProps({ text: { type: String, default: '' } })
const paragraph = ref(null)
const expanded = ref(false)
const collapsible = ref(false)
let observer = null
function measure() {
  const el = paragraph.value
  if (!el) { collapsible.value = false; return }
  const lineHeight = Number.parseFloat(window.getComputedStyle(el).lineHeight)
  collapsible.value = el.scrollHeight > lineHeight * 3 + 2
}
function observe() {
  observer?.disconnect()
  if (paragraph.value) observer?.observe(paragraph.value)
  measure()
}
watch(() => props.text, async () => {
  expanded.value = false
  await nextTick()
  observe()
})
onMounted(() => {
  if (typeof ResizeObserver !== 'undefined') observer = new ResizeObserver(measure)
  observe()
})
onUnmounted(() => observer?.disconnect())
</script>
<style scoped>
.media-overview { max-width: 820px; margin: 18px 0; }
.overview { margin: 0; color: var(--jz-text-dim); font-size: var(--jz-font-l); line-height: 1.8; white-space: pre-wrap; overflow-wrap: anywhere; }
.clamped { display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.overview-toggle { margin-top: 6px; border: 0; background: transparent; padding: 4px 0; color: var(--jz-link); font-size: var(--jz-font-s); }
</style>
