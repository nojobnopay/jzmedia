<template>
  <div class="wall-head">
    <h2>{{ title }} <span v-if="count" class="wall-count">{{ count }}</span></h2>
    <div class="wall-sort">
      <span v-if="relevance" class="sort-note">按搜索相关度排序</span>
      <template v-else>
        <label><span class="sort-label">排序</span><select :value="sort.key" aria-label="排序方式" @change="$emit('sort', $event.target.value)">
          <option v-for="option in options" :key="option.key" :value="option.key">{{ option.key === 'rating' ? option.label + '（' + ratingLabel + '）' : option.label }}</option>
        </select></label>
        <JzButton class="sort-direction" :aria-label="sort.order === 'asc' ? '当前升序，切换为降序' : '当前降序，切换为升序'" @click="$emit('sort', sort.key)" type="button">
          <AppIcon :name="sort.order === 'asc' ? 'sort-asc' : 'sort-desc'" /><span class="sort-label">{{ sort.order === 'asc' ? '升序' : '降序' }}</span>
        </JzButton>
      </template>
    </div>
  </div>
</template>
<script setup>
import AppIcon from './AppIcon.vue'

import JzButton from './JzButton.vue'

import { computed } from 'vue'
const props = defineProps({
  title: { type: String, required: true },
  count: { type: String, default: '' },
  sort: { type: Object, required: true },
  options: { type: Array, required: true },
  ratingSource: { type: String, default: 'tmdb' },
  relevance: Boolean,
})
defineEmits(['sort'])
const ratingLabel = computed(() => ({ tmdb: 'TMDB', douban: '豆瓣', custom: '自评' }[props.ratingSource] || 'TMDB'))
</script>
<style scoped>
.wall-head { display: flex; gap: var(--jz-gap-l); flex-wrap: wrap; align-items: center; margin: var(--jz-gap-2xl) 0 var(--jz-gap-l); }
h2 { margin: 0; font-size: 1.125rem; line-height: 1.4; font-weight: 600; }
.wall-count { display: inline-block; margin-left: var(--jz-gap-s); font-size: var(--jz-font-s); font-weight: 400; color: var(--jz-text-dim); }
.wall-sort { margin-left: auto; display: flex; gap: var(--jz-gap-xs); align-items: center; min-width: 0; }
.wall-sort label { display: flex; gap: var(--jz-gap-s); align-items: center; font-size: var(--jz-font-s); color: var(--jz-text-dim); }
.wall-sort select { width: 142px; min-height: var(--jz-control-current); padding: var(--jz-gap-s); }
.sort-note { color: var(--jz-text-dim); font-size: var(--jz-font-s); }
@media (max-width: 700px) {
  .wall-head { margin: var(--jz-gap-m) 0 var(--jz-gap-s); gap: var(--jz-gap-xs); }
  h2 { font-size: 1rem; }
  .wall-count { margin-left: var(--jz-gap-xs); }
  .sort-label { display: none; }
  .wall-sort select { width: 106px; font-size: var(--jz-font-s); }
}
</style>
