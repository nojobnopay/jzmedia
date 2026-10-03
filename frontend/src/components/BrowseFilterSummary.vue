<template>
  <div v-if="chips.length || query" ref="root" class="browse-filter-summary" role="group" aria-label="已生效的搜索与筛选">
    <span class="summary-label">已选条件</span>
    <JzButton v-if="query" class="summary-chip" size="compact" aria-label="清除搜索" :title="'搜索：' + query" @click="remove('clear-query')">
      <AppIcon name="search" :size="14" /><span>{{ query }}</span><AppIcon name="close" :size="14" />
    </JzButton>
    <JzButton v-for="chip in chips" :key="chip.key + ':' + chip.value" class="summary-chip" size="compact"
      :aria-label="'移除筛选：' + chip.label" :title="chip.label" @click="remove('remove', chip)">
      <span>{{ chip.label }}</span><AppIcon name="close" :size="14" />
    </JzButton>
    <JzButton v-if="chips.length" size="compact" variant="ghost" @click="remove('clear')">清空筛选</JzButton>
  </div>
</template>

<script setup>
import { nextTick, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import JzButton from './JzButton.vue'

const props = defineProps({ chips: { type: Array, default: () => [] }, query: { type: String, default: '' }, fallbackId: String })
const emit = defineEmits(['remove', 'clear', 'clear-query'])
const root = ref(null)
let pendingFocus = null
function remove(event, chip) {
  pendingFocus = root.value?.contains(document.activeElement)
    ? Array.from(root.value.querySelectorAll('button')).indexOf(document.activeElement) : null
  emit(event, chip)
}
watch(() => [props.chips, props.query], async () => {
  if (pendingFocus == null) return
  const index = pendingFocus
  pendingFocus = null
  await nextTick()
  const remaining = root.value?.querySelectorAll('button') || []
  const target = remaining[Math.min(Math.max(index, 0), remaining.length - 1)] || document.getElementById(props.fallbackId)
  target?.focus({ preventScroll: true })
}, { flush: 'post' })
</script>

<style scoped>
.browse-filter-summary { display: flex; flex-wrap: wrap; align-items: center; gap: var(--jz-gap-s); margin: 0 0 var(--jz-gap-l); }
.summary-label { color: var(--jz-text-dim); font-size: var(--jz-font-s); margin-right: var(--jz-gap-xs); }
.summary-chip { max-width: 100%; border-color: var(--jz-border); background: var(--jz-surface); }
.summary-chip span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
