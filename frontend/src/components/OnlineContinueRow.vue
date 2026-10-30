<template>
  <section v-if="loading || error || rows.length" class="online-continue" aria-label="继续观看" data-online-continue-row>
    <header class="online-continue-heading"><h2>继续观看</h2><JzButton variant="ghost" icon="history" data-online-records-open @click="$emit('all')">查看全部</JzButton></header>
    <p v-if="error" class="online-continue-feedback" role="status">{{ error }}<JzButton variant="ghost" icon="refresh" :loading="loading" @click="$emit('retry')">重试</JzButton></p>
    <OnlineSkeleton v-if="loading && !rows.length" variant="continue" :count="4" label="正在读取观看记录…" />
    <div v-else-if="rows.length" class="online-continue-scroll" :aria-busy="loading" tabindex="0" aria-label="继续观看列表，可左右滚动">
      <OnlineRecordCard v-for="row in rows.slice(0, 12)" :key="row.item.id" :row="row" compact :disabled="disabled" :starting="starting" :reason-label="reasonLabel"
        @detail="$emit('detail', $event)" @resume="$emit('resume', $event)" />
    </div>
  </section>
</template>

<script setup>
import JzButton from './JzButton.vue'
import OnlineRecordCard from './OnlineRecordCard.vue'
import OnlineSkeleton from './OnlineSkeleton.vue'
defineProps({
  rows: { type: Array, default: () => [] },
  loading: Boolean,
  error: { type: String, default: '' },
  disabled: Boolean,
  starting: Boolean,
  reasonLabel: { type: Function, default: () => '视频源暂不可用，记录已保留。' },
})
defineEmits(['detail', 'resume', 'retry', 'all'])
</script>

<style scoped>
.online-continue { min-width: 0; margin-block: var(--jz-gap-l) var(--jz-gap-2xl); }
.online-continue-heading { display: flex; align-items: center; justify-content: space-between; gap: var(--jz-gap-m); }
.online-continue-heading h2 { margin: 0; font-size: var(--jz-font-body); font-weight: 600; }
.online-continue-heading > .jz-button { font-size: var(--jz-font-s); }
.online-continue-scroll { display: flex; gap: var(--jz-gap-m); overflow-x: auto; overscroll-behavior-x: contain; scroll-snap-type: x proximity; padding: 3px 3px var(--jz-gap-s); margin: 0 -3px; scrollbar-width: thin; }
.online-continue-scroll > * { flex: none; scroll-snap-align: start; }
.online-continue-scroll:focus-visible { outline: 2px solid var(--jz-link); outline-offset: 3px; border-radius: var(--jz-radius-m); }
.online-continue-feedback { display: flex; align-items: center; flex-wrap: wrap; gap: var(--jz-gap-s); margin: 0; color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.online-continue :deep(.online-skeleton) { margin-top: 0; }
@media (max-width: 700px) { .online-continue { margin-block: var(--jz-gap-m) var(--jz-gap-xl); } }
</style>
