<template>
  <section v-if="(items || []).length" class="card-block similar-block">
    <h3>{{ title }} <span v-if="subtitle" class="similar-sub">{{ subtitle }}</span></h3>
    <div class="similar-wrap">
      <button v-if="items.length > 4" class="similar-nav left" aria-label="向左滚动" @click="scroll(-1)">‹</button>
      <div ref="rowRef" class="similar-row" @scroll="onScroll">
        <div v-for="x in items" :key="x.id" class="similar-card" @click="$emit('open', x.id)">
          <div class="poster-wrap">
            <img v-if="x.poster_path" :src="posterUrl(x.poster_path)" loading="lazy" :alt="(x.title || '影片') + ' 海报'" />
            <div v-else class="similar-no-poster" aria-hidden="true">{{ (x.title || '?').slice(0, 1) }}</div>
            <ScoreBadge :score="x.tmdb_rating" source="tmdb" />
          </div>
          <div class="similar-name" :title="x.title">{{ x.title }}<span v-if="x.year" class="similar-year">({{ x.year }})</span><span v-if="x.version_count > 1" class="similar-year">×{{ x.version_count }}</span><span v-if="hasScore(x.custom_rating)" class="similar-custom">♥{{ fmtScore(x.custom_rating) }}</span></div>
          <div v-if="mediaText(x)" class="similar-library" :title="mediaText(x)">{{ mediaText(x) }}</div>
          <div v-if="x.reason" class="similar-reason" :title="x.reason">{{ x.reason }}</div>
        </div>
      </div>
      <div v-if="bar.show" class="similar-bar" aria-hidden="true">
        <div class="similar-bar-thumb" :style="{ left: bar.left + '%', width: bar.width + '%' }"></div>
      </div>
      <button v-if="items.length > 4" class="similar-nav right" aria-label="向右滚动" @click="scroll(1)">›</button>
    </div>
  </section>
</template>
<script setup>
// 库中类似/相关节目单源（P4）：电影 similar-block 与剧集 similar-sec 统一为
// 同一交互（横向滚动+箭头+细线进度+ScoreBadge+reason）。TV 条目无 version_count/
// custom_rating 时对应徽标自动隐藏（hasScore 守卫）。
import { ref, onMounted, onUnmounted } from 'vue'
import { posterUrl } from '../api.js'
import { hasScore, fmtScore } from '../ratings.js'
import ScoreBadge from './ScoreBadge.vue'

defineProps({
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
function updateBar() {
  const el = rowRef.value
  if (!el) return
  const max = el.scrollWidth - el.clientWidth
  if (max <= 0) { bar.value = { show: false, left: 0, width: 100 }; return }
  const w = Math.max(8, (el.clientWidth / el.scrollWidth) * 100)
  const l = (el.scrollLeft / max) * (100 - w)
  bar.value = { show: true, left: l, width: w }
}
function onScroll() { updateBar() }
function scroll(dir) {
  const el = rowRef.value
  if (el) el.scrollBy({ left: dir * el.clientWidth * 0.8, behavior: 'smooth' })
}
onMounted(() => {
  updateBar()
  window.addEventListener('resize', updateBar)
})
onUnmounted(() => window.removeEventListener('resize', updateBar))
</script>
<style scoped>
.similar-block { position: relative; }
.similar-sub { color: #777; font-size: 0.75rem; font-weight: normal; margin-left: 6px; }
.similar-wrap { position: relative; }
.similar-row { display: flex; gap: 12px; overflow-x: auto; padding: 2px 2px 10px; scroll-behavior: smooth; scrollbar-width: none; }
.similar-row::-webkit-scrollbar { display: none; }
.similar-bar { position: relative; height: 3px; margin: 0 2px; }
.similar-bar-thumb { position: absolute; top: 0; height: 100%; border-radius: 999px; background: rgba(255,255,255,.18); transition: background .15s; }
.similar-wrap:hover .similar-bar-thumb { background: rgba(255,255,255,.32); }
.similar-card { flex: 0 0 140px; width: 140px; cursor: pointer; min-width: 0; }
.similar-card .poster-wrap img { border-radius: 8px; transition: filter .15s; }
.similar-card:hover .poster-wrap img { filter: brightness(1.1); }
.similar-no-poster { width: 100%; aspect-ratio: 2/3; display: flex; align-items: center; justify-content: center; background: #242424; color: #555; font-size: 2rem; font-weight: bold; border-radius: 8px; user-select: none; }
.similar-name { font-size: 0.8125rem; margin-top: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.similar-year { color: #999; font-size: 0.75rem; margin-left: 4px; }
.similar-custom { color: #ff6b6b; font-size: 0.75rem; margin-left: 4px; }
.similar-library { font-size: 0.6875rem; color: #6ab0ff; margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.similar-reason { font-size: 0.75rem; color: #888; margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.similar-nav { position: absolute; top: 42%; transform: translateY(-50%); z-index: 2; width: 32px; height: 44px; border: none; border-radius: 8px; background: rgba(0,0,0,.62); color: #eee; font-size: 1.5rem; line-height: 1; cursor: pointer; opacity: 0; transition: opacity .15s; padding: 0; }
.similar-nav.left { left: 4px; }
.similar-nav.right { right: 4px; }
.similar-block:hover .similar-nav { opacity: 1; }
.similar-nav:hover { background: rgba(0,0,0,.85); }
@media (hover: none) { .similar-nav { display: none; } }
</style>
