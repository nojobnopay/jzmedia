<template>
  <span v-if="hasScore(score)" class="score-badge" :class="source" :title="tip">{{ star }} {{ fmtScore(score) }}</span>
</template>
<script setup>
import { computed } from 'vue'
import { hasScore, fmtScore } from '../ratings.js'

const props = defineProps({
  score: { type: Number, default: null },
  source: { type: String, default: 'tmdb' }, // tmdb | douban | custom
})
const star = computed(() => (props.source === 'custom' ? '♥' : '★'))
const tip = computed(() => ({ tmdb: 'TMDB 评分', douban: '豆瓣评分', custom: '我的评分' }[props.source] || '评分'))
</script>
