<template>
  <div v-if="hasAny" class="rating-row">
    <span v-if="hasScore(tmdb)" class="rate-chip tmdb"><span class="stars">{{ starRow(tmdb) }}</span> {{ fmtScore(tmdb) }} <span class="src">TMDB</span></span>
    <span v-if="hasScore(douban)" class="rate-chip douban">豆瓣 {{ fmtScore(douban) }}</span>
    <span v-if="hasScore(custom)" class="rate-chip custom">自评 {{ fmtScore(custom) }}</span>
  </div>
</template>
<script setup>
// 详情 hero 评分单源（P2）：电影 rate-chip 与剧集纯文本统一为同一形态。
// 口径唯一走 ratings.js（hasScore>0，fmtScore 去尾零）；缺失源自动隐藏。
import { computed } from 'vue'
import { hasScore, fmtScore, starRow } from '../ratings.js'

const props = defineProps({
  tmdb: { type: Number, default: null },
  douban: { type: Number, default: null },
  custom: { type: Number, default: null },
})
const hasAny = computed(() =>
  hasScore(props.tmdb) || hasScore(props.douban) || hasScore(props.custom))
</script>
