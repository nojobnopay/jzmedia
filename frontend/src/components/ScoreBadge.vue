<template>
  <span v-if="hasScore(score)" class="score-badge" :class="source" :title="tip"><AppIcon :name="source === 'custom' ? 'heart-filled' : 'star-filled'" :size="12" /> {{ fmtScore(score) }}</span>
</template>
<script setup>
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import { hasScore, fmtScore } from '../ratings.js'

const props = defineProps({
  score: { type: Number, default: null },
  source: { type: String, default: 'tmdb' }, // tmdb | douban | custom
})
const tip = computed(() => ({ tmdb: 'TMDB 评分', douban: '豆瓣评分', custom: '我的评分' }[props.source] || '评分'))
</script>

<style scoped>
.score-badge { display: inline-flex; align-items: center; gap: 3px; }
</style>
