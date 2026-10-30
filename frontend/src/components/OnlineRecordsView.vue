<template>
  <section ref="root" class="online-records" aria-label="我的在线记录" data-online-records>
    <JzButton variant="ghost" icon="arrow-left" data-online-records-back @click="$emit('back')">返回在线浏览</JzButton>
    <header class="online-records-heading">
      <div><h1 ref="title" tabindex="-1">我的记录</h1><p>当前设备 · {{ modeText }}；与本地媒体库记录分开。</p></div>
      <JzButton variant="ghost" icon="refresh" :loading="loading" @click="$emit('refresh')">刷新记录</JzButton>
    </header>
    <div class="online-records-tabs" role="group" aria-label="在线记录分类">
      <JzButton v-for="tab in tabs" :key="tab.key" variant="ghost" :data-online-library-section="tab.key" :aria-pressed="section === tab.key" @click="$emit('section', tab.key)">{{ tab.label }}</JzButton>
    </div>
    <p v-if="error && rows.length" class="online-records-feedback" role="status">{{ error }}<JzButton variant="ghost" icon="refresh" :loading="loading" @click="$emit('refresh')">重试</JzButton></p>
    <OnlineSkeleton v-if="loading && !rows.length" :variant="section === 'favorites' ? 'catalogue' : 'records'" :count="6" label="正在读取在线记录…" />
    <EmptyState v-else-if="error && !rows.length" state="error" title="在线记录加载失败" :text="error" retry @retry="$emit('refresh')" />
    <EmptyState v-else-if="!rows.length" state="empty" :title="emptyTitle" :text="section === 'favorites' ? '打开内容详情，即可收藏到这里。' : '开始观看后，可以在这里接着看。'"><JzButton icon="play" @click="$emit('back')">浏览在线内容</JzButton></EmptyState>
    <div v-else class="online-records-grid" :class="{ 'online-records-grid--posters': section === 'favorites' }" :aria-busy="loading">
      <OnlineRecordCard v-for="row in rows" :key="row.item.id" :row="row" :vertical="section === 'favorites'"
        :disabled="disabled" :starting="starting" :busy="mutationBusy.has(row.item.id)" :reason-label="reasonLabel"
        @detail="$emit('detail', $event)" @resume="$emit('resume', $event)" @favorite="(item, value) => $emit('favorite', item, value)" @clear="$emit('clear', $event)" />
    </div>
    <nav v-if="total > 20" class="online-records-pagination" aria-label="在线记录分页">
      <JzButton icon="arrow-left" :disabled="offset === 0 || loading" @click="$emit('page', Math.max(0, offset - 20))">上一页</JzButton>
      <span>第 {{ Math.floor(offset / 20) + 1 }} 页 · {{ total }} 项</span>
      <JzButton icon="forward" :disabled="loading || offset + 20 >= total || offset + 20 > 1000" @click="$emit('page', offset + 20)">下一页</JzButton>
    </nav>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import JzButton from './JzButton.vue'
import EmptyState from './EmptyState.vue'
import OnlineRecordCard from './OnlineRecordCard.vue'
import OnlineSkeleton from './OnlineSkeleton.vue'
const props = defineProps({
  rows: { type: Array, default: () => [] },
  section: { type: String, default: 'continue' },
  offset: { type: Number, default: 0 },
  total: { type: Number, default: 0 },
  loading: Boolean,
  error: { type: String, default: '' },
  modeText: { type: String, default: '普通模式' },
  disabled: Boolean,
  starting: Boolean,
  mutationBusy: { type: Set, default: () => new Set() },
  reasonLabel: { type: Function, default: () => '视频源暂不可用，记录已保留。' },
})
defineEmits(['section', 'page', 'refresh', 'back', 'detail', 'resume', 'favorite', 'clear'])
const tabs = [{ key: 'continue', label: '继续观看' }, { key: 'favorites', label: '我的收藏' }, { key: 'history', label: '观看历史' }]
const emptyTitle = computed(() => ({ favorites: '还没有收藏的在线内容', history: '还没有在线观看记录', continue: '暂时没有继续观看的内容' }[props.section]))
const root = ref(null), title = ref(null)
defineExpose({ el: root, focus: () => title.value?.focus({ preventScroll: true }) })
</script>

<style scoped>
.online-records { min-width: 0; }
.online-records > .jz-button { padding-left: 0; margin-bottom: var(--jz-gap-m); }
.online-records-heading { display: flex; align-items: center; justify-content: space-between; gap: var(--jz-gap-m); }
.online-records-heading h1 { margin: 0; font-size: var(--jz-font-heading); }
.online-records-heading h1:focus { outline: none; }
.online-records-heading p { margin: var(--jz-gap-xs) 0 0; color: var(--jz-text-dim); font-size: var(--jz-font-s); line-height: 1.6; }
.online-records-tabs { display: flex; gap: var(--jz-gap-s); margin-block: var(--jz-gap-xl); border-bottom: 1px solid var(--jz-border); }
.online-records-tabs .jz-button { position: relative; padding-inline: var(--jz-gap-m); border: 0; border-radius: var(--jz-radius-s) var(--jz-radius-s) 0 0; }
.online-records-tabs .jz-button[aria-pressed="true"] { background: transparent; color: var(--jz-text); }
.online-records-tabs .jz-button[aria-pressed="true"]::after { position: absolute; content: ''; inset: auto var(--jz-gap-m) -1px; height: 2px; background: var(--jz-accent); }
.online-records-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 300px), 1fr)); gap: var(--jz-gap-l); align-items: stretch; }
.online-records-grid--posters { grid-template-columns: repeat(auto-fill, minmax(var(--poster-min, 150px), 1fr)); }
.online-records-feedback { display: flex; align-items: center; flex-wrap: wrap; gap: var(--jz-gap-s); margin-block: var(--jz-gap-m); color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.online-records-pagination { display: flex; align-items: center; justify-content: center; flex-wrap: wrap; gap: var(--jz-gap-m); margin-top: var(--jz-gap-2xl); }
.online-records-pagination > span { color: var(--jz-text-dim); font-size: var(--jz-font-s); }
@media (max-width: 700px) {
  .online-records-heading { align-items: flex-start; flex-wrap: wrap; }
  .online-records-heading > .jz-button { margin-left: auto; }
  .online-records-grid--posters { grid-template-columns: repeat(auto-fill, minmax(min(var(--jz-mobile-poster-min), 100%), 1fr)); gap: var(--jz-gap-m); }
  .online-records-tabs { gap: 0; }
  .online-records-tabs .jz-button { flex: 1; padding-inline: var(--jz-gap-s); }
}
</style>
