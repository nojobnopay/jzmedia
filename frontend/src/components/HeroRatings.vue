<template>
  <div v-if="hasAny" class="rating-row">
    <span v-if="hasScore(tmdb)" class="rate-chip tmdb"><AppIcon name="star-filled" :size="16" /> <strong>{{ fmtScore(tmdb) }}</strong> <span class="src">TMDB</span></span>
    <span v-if="hasScore(douban)" class="rate-chip douban">豆瓣 {{ fmtScore(douban) }}</span>
    <span v-if="hasScore(custom)" class="rate-chip custom">自评 {{ fmtScore(custom) }}</span>
  </div>
</template>
<script setup>
import AppIcon from './AppIcon.vue'

// 详情 hero 评分单源（P2）：电影 rate-chip 与剧集纯文本统一为同一形态。
// 口径唯一走 ratings.js（hasScore>0，fmtScore 去尾零）；缺失源自动隐藏。
import { computed } from 'vue'
import { hasScore, fmtScore } from '../ratings.js'

const props = defineProps({
  tmdb: { type: Number, default: null },
  douban: { type: Number, default: null },
  custom: { type: Number, default: null },
})
const hasAny = computed(() =>
  hasScore(props.tmdb) || hasScore(props.douban) || hasScore(props.custom))
</script>

<style scoped>
.rating-row { display: flex; gap: var(--jz-gap-l); align-items: center; margin: var(--jz-gap-m) 0; }
.rate-chip { display: inline-flex; align-items: baseline; gap: 4px; padding: 0; border: 0; margin: 0; border-radius: 0; font-size: var(--jz-font-m); }
.rate-chip strong { font-size: 1.125rem; font-weight: 600; }
.src { color: var(--jz-text-dim); font-size: var(--jz-font-s); }
@media (max-width: 700px) { .rating-row { gap: var(--jz-gap-m); margin: var(--jz-gap-s) 0; }.rate-chip { font-size: var(--jz-font-s); }.rate-chip strong { font-size: 1rem; } }
</style>
