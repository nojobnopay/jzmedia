<template>
  <section v-if="(items || []).length" class="card-block similar-block">
    <h3>{{ title }} <span v-if="subtitle" class="similar-sub">{{ subtitle }}</span></h3>
    <div class="similar-wrap">
      <JzButton v-if="bar.show" class="similar-nav left" aria-label="向左滚动" :disabled="!canLeft" @click="scroll(-1)" type="button"><AppIcon name="chevron-left" /></JzButton>
      <div ref="rowRef" class="similar-row" @scroll="onScroll">
        <div v-for="x in items" :key="x.id" class="similar-card" role="link" tabindex="0" @keydown.enter="$emit('open', x.id)" @click="$emit('open', x.id)">
          <div class="poster-wrap">
            <img v-if="x.poster_path" :src="posterUrl(x.poster_path)" loading="lazy" :alt="(x.title || '影片') + ' 海报'" />
            <ArtworkPlaceholder v-else class="similar-no-poster" kind="poster" :label="x.title" />
            <ScoreBadge :score="x.tmdb_rating" source="tmdb" />
          </div>
          <div class="similar-name" :title="x.title">{{ x.title }}<span v-if="x.year" class="similar-year">({{ x.year }})</span><span v-if="x.version_count > 1" class="similar-year">×{{ x.version_count }}</span><span v-if="hasScore(x.custom_rating)" class="similar-custom"><AppIcon name="heart-filled" :size="12" />{{ fmtScore(x.custom_rating) }}</span></div>
          <div v-if="mediaText(x)" class="similar-library" :title="mediaText(x)">{{ mediaText(x) }}</div>
          <div v-if="x.reason" class="similar-reason" :title="x.reason">{{ x.reason }}</div>
        </div>
      </div>
      <div v-if="bar.show" class="similar-bar" aria-hidden="true">
        <div class="similar-bar-thumb" :style="{ left: bar.left + '%', width: bar.width + '%' }"></div>
      </div>
      <JzButton v-if="bar.show" class="similar-nav right" aria-label="向右滚动" :disabled="!canRight" @click="scroll(1)" type="button"><AppIcon name="chevron-right" /></JzButton>
    </div>
  </section>
</template>
<script setup>
import ArtworkPlaceholder from './ArtworkPlaceholder.vue'

import AppIcon from './AppIcon.vue'

import JzButton from './JzButton.vue'

// 库中类似/相关节目单源（P4）：电影 similar-block 与剧集 similar-sec 统一为
// 同一交互（横向滚动+箭头+细线进度+ScoreBadge+reason）。TV 条目无 version_count/
// custom_rating 时对应徽标自动隐藏（hasScore 守卫）。
import { nextTick, ref, onMounted, onUnmounted, watch } from 'vue'
import { posterUrl } from '../api.js'
import { hasScore, fmtScore } from '../ratings.js'
import { canScroll, scrollStep } from '../recentPlayed.js'
import ScoreBadge from './ScoreBadge.vue'

const props = defineProps({
  items: { type: Array, default: () => [] },
  title: { type: String, default: '库中类似' },
  subtitle: { type: String, default: '按系列 / 影人 / 类型 / 标签推荐' },
})
defineEmits(['open'])

function mediaText (item) {
  return [...new Set([item?.media_name, item?.library_name].filter(Boolean))].join(' · ')
}

const rowRef = ref(null)
const bar = ref({ show: false, left: 0, width: 100 })
const canLeft = ref(false)
const canRight = ref(false)
let rowIO = null
function updateScroll() {
  const el = rowRef.value
  if (!el) {
    canLeft.value = false
    canRight.value = false
    bar.value = { show: false, left: 0, width: 100 }
    return
  }
  const s = canScroll({ scrollLeft: el.scrollLeft, clientWidth: el.clientWidth, scrollWidth: el.scrollWidth })
  canLeft.value = s.left
  canRight.value = s.right
  const max = el.scrollWidth - el.clientWidth
  if (max <= 0) { bar.value = { show: false, left: 0, width: 100 }; return }
  const w = Math.max(8, (el.clientWidth / el.scrollWidth) * 100)
  const l = (el.scrollLeft / max) * (100 - w)
  bar.value = { show: true, left: l, width: w }
}
function onScroll() { updateScroll() }
function scroll(dir) {
  const el = rowRef.value
  if (!el) return
  const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  el.scrollBy({ left: dir * scrollStep(el.clientWidth), behavior: reduce ? 'instant' : 'smooth' })
}
watch(() => props.items, () => nextTick(updateScroll))
onMounted(() => {
  updateScroll()
  window.addEventListener('resize', updateScroll)
  try {
    if (window.ResizeObserver && rowRef.value) {
      rowIO = new ResizeObserver(updateScroll)
      rowIO.observe(rowRef.value)
    }
  } catch (e) { /* 不支持则只在滚动/重载时更新 */ }
})
onUnmounted(() => {
  window.removeEventListener('resize', updateScroll)
  if (rowIO) { try { rowIO.disconnect() } catch (e) { /* 忽略 */ } rowIO = null }
})
</script>
<style scoped>
.similar-block { position: relative; padding: 0; margin-top: var(--jz-gap-xl); background: transparent; }
.similar-block h3 { font-size: 1.125rem; margin-bottom: var(--jz-gap-l); }
.similar-card:focus-visible { outline: 2px solid var(--jz-link); outline-offset: 2px; border-radius: var(--jz-radius-s); }
.similar-sub { display: block; margin-top: var(--jz-gap-xs); color: var(--jz-text-dim); font-size: 0.75rem; font-weight: normal; }
.similar-wrap { position: relative; }
.similar-row { display: flex; gap: 12px; overflow-x: auto; padding: 2px 2px 10px; scroll-snap-type: x proximity; scroll-padding-inline: 2px; scroll-behavior: smooth; scrollbar-width: none; }
.similar-row::-webkit-scrollbar { display: none; }
.similar-bar { position: relative; height: 3px; margin: 0 2px; }
.similar-bar-thumb { position: absolute; top: 0; height: 100%; border-radius: 999px; background: rgba(255,255,255,.18); transition: background .15s; }
.similar-wrap:hover .similar-bar-thumb { background: rgba(255,255,255,.32); }
.similar-card { flex: 0 0 148px; width: 148px; cursor: pointer; min-width: 0; scroll-snap-align: start; }
.similar-card .poster-wrap img { border-radius: 8px; transition: filter .15s; }
.similar-card:hover .poster-wrap img { filter: brightness(1.1); }
.similar-no-poster { width: 100%; aspect-ratio: 2/3; display: flex; align-items: center; justify-content: center; background: var(--jz-surface-3); color: var(--jz-text-faint); font-size: 2rem; font-weight: bold; border-radius: 8px; user-select: none; }
.similar-name { font-size: 0.8125rem; margin-top: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.similar-year { color: var(--jz-text-dim); font-size: 0.75rem; margin-left: 4px; }
.similar-custom { color: var(--jz-danger); font-size: 0.75rem; margin-left: 4px; }
.similar-library { font-size: 0.6875rem; color: var(--jz-text-dim); margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.similar-reason { font-size: 0.75rem; color: var(--jz-text-dim); margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.similar-nav { position: absolute; top: 42%; transform: translateY(-50%); z-index: 2; width: 32px; height: 44px; opacity: 0; transition: opacity .15s; }
.similar-nav.left { left: 4px; }
.similar-nav.right { right: 4px; }
.similar-block:hover .similar-nav, .similar-nav:focus-visible { opacity: 1; }
/* 到头时保留占位并置灰（不用 v-if 移除，避免箭头消失导致误点下方卡片） */
.similar-block .similar-nav:disabled { opacity: 0; }
.similar-block:hover .similar-nav:disabled, .similar-nav:disabled:focus-visible { opacity: .35; }
@media (hover: none) { .similar-nav { display: none; } }
@media (max-width: 700px) { .similar-block { padding: 0; }.similar-card { flex-basis: 120px; width: 120px; } }
@media (prefers-reduced-motion: reduce) { .similar-row { scroll-behavior: auto; }.similar-card .poster-wrap img, .similar-bar-thumb, .similar-nav { transition: none; } }
</style>
