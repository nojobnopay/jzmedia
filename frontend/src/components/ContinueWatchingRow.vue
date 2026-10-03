<template>
  <section v-if="items.length" class="cw">
    <div class="cw-head">
      <h3>
        继续观看 <span class="cw-count">{{ items.length }}</span>
      </h3>
      <JzButton class="cw-toggle" variant="ghost" size="compact" :aria-pressed="allMode" @click="toggleAll" type="button" icon="history">{{ allMode ? '只看未看完' : '全部最近播放' }}</JzButton>
    </div>
    <div class="cw-strip">
      <JzButton v-if="canLeft" class="cw-nav left" icon-only aria-label="向左滚动" @click="scrollByDir(-1)" type="button"><AppIcon name="chevron-left" /></JzButton>
      <div ref="rowRef" class="cw-row" @scroll="onScroll">
        <div v-for="m in items" :key="m.id" class="cw-card" role="link" tabindex="0"
          :title="m.added_at ? ('入库 ' + fmtDate(m.added_at)) : ''" @click="$emit('open', m.id)" @keydown.enter.self="$emit('open', m.id)">
          <div class="poster-wrap">
            <img v-if="m.poster_path" :src="posterUrl(m.poster_path)" loading="lazy"
              :alt="(m.title || '影片') + ' 海报'" />
            <ArtworkPlaceholder v-else class="cw-no-poster" kind="poster" :label="m.title" />
            <div v-if="!m.watched" class="cw-bar" aria-hidden="true">
              <div class="cw-bar-in" :style="{ width: progressWidth(m.progress) }"></div>
            </div>
            <button class="poster-play" :aria-label="'继续播放 ' + (m.title || '')" title="继续播放"
              @click.stop="$emit('resume', m)"><PlayerIcon name="play" :size="24" /></button>
          </div>
          <div class="cw-name" :title="m.title">{{ m.title }}<span v-if="m.year" class="cw-year">({{ m.year }})</span><span v-if="m.version_count > 1" class="cw-year">×{{ m.version_count }}</span></div>
          <div v-if="m.subtitle" class="cw-ep" :title="m.subtitle">{{ m.subtitle }}</div>
          <div class="cw-progress-text"><AppIcon v-if="m.watched" name="check" :size="14" />{{ m.watched ? '已看完' : fmtRemaining(m.progress && m.progress.remaining_sec) }}</div>
        </div>
      </div>
      <JzButton v-if="canRight" class="cw-nav right" icon-only aria-label="向右滚动" @click="scrollByDir(1)" type="button"><AppIcon name="chevron-right" /></JzButton>
      <span v-if="canLeft" class="cw-fade left" aria-hidden="true"></span>
      <span v-if="canRight" class="cw-fade right" aria-hidden="true"></span>
    </div>
  </section>
</template>
<script setup>
import ArtworkPlaceholder from './ArtworkPlaceholder.vue'

import AppIcon from './AppIcon.vue'

import JzButton from './JzButton.vue'

import PlayerIcon from './PlayerIcon.vue'

import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, posterUrl } from '../api.js'
import { fmtDate, fmtRemaining } from '../format.js'
import { canScroll, loadRecentAll, progressWidth, saveRecentAll, scrollStep } from '../recentPlayed.js'

const props = defineProps({
  mediaLibraryId: { type: Number, default: null },
  kind: { type: String, default: 'movie' },   // movie|tv（剧集行显示 SxxEyy 副标题）
})
defineEmits(['open', 'resume'])

const items = ref([])
const loading = ref(false)
const allMode = ref(loadRecentAll(localStorage))
const rowRef = ref(null)
const canLeft = ref(false)
const canRight = ref(false)
let seq = 0
let rowIO = null

let pending = Promise.resolve()
function reload() { pending = fetchRecent(); return pending }
async function fetchRecent() {
  const s = ++seq
  loading.value = true
  try {
    const p = new URLSearchParams()
    p.set('limit', '20')
    if (allMode.value) p.set('include_finished', 'true')
    if (props.mediaLibraryId != null) p.set('media_library', String(props.mediaLibraryId))
    const base = props.kind === 'tv' ? '/api/tv/recent-played' : '/api/movies/recent-played'
    const d = await api(base + '?' + p.toString())
    if (s !== seq) return
    items.value = (d && d.items) || []
    await nextTick()
    updateScroll()
  } catch (e) {
    if (s === seq) items.value = []   // 静默失败：不挡海报墙（后端不可用时整页会自报错）
  } finally {
    if (s === seq) loading.value = false
  }
}

function toggleAll() {
  allMode.value = !allMode.value
  saveRecentAll(localStorage, allMode.value)
  reload()
}

// 左右箭头/渐隐：按滚动量实测（不是按条数猜），滚动到头的方向隐藏
function updateScroll() {
  const el = rowRef.value
  if (!el) {
    canLeft.value = false
    canRight.value = false
    return
  }
  const s = canScroll({ scrollLeft: el.scrollLeft, clientWidth: el.clientWidth,
                        scrollWidth: el.scrollWidth })
  canLeft.value = s.left
  canRight.value = s.right
}
function onScroll() { updateScroll() }
function scrollByDir(dir) {
  const el = rowRef.value
  if (!el) return
  el.scrollBy({ left: dir * scrollStep(el.clientWidth), behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' })
}

watch(() => props.mediaLibraryId, reload)
watch(() => props.kind, reload)
onMounted(async () => {
  await reload()
  window.addEventListener('resize', updateScroll)
  try {
    if (window.ResizeObserver && rowRef.value) {
      rowIO = new ResizeObserver(updateScroll)
      rowIO.observe(rowRef.value)
    }
  } catch (e) { /* 不支持则只在滚动/重载时更新 */ }
})
onUnmounted(() => {
  seq++
  window.removeEventListener('resize', updateScroll)
  if (rowIO) { try { rowIO.disconnect() } catch (e) { /* 忽略 */ } rowIO = null }
})
defineExpose({ reload, ready: () => pending })
</script>
<style scoped>
.cw { margin: var(--jz-gap-xl) 0 0; }
.cw-head { display: flex; align-items: center; gap: var(--jz-gap-m); margin-bottom: var(--jz-gap-s); }
.cw-head h3 { margin: 0; font-size: 1rem; line-height: 1.4; font-weight: 600; }
.cw-count { margin-left: var(--jz-gap-xs); color: var(--jz-text-dim); font-size: var(--jz-font-s); font-weight: 400; }
.cw-toggle { margin-left: auto; }
.cw-strip { position: relative; }
.cw-row { display: flex; gap: var(--jz-gap-m); overflow-x: auto; scroll-behavior: smooth; scrollbar-width: none; padding: 3px; margin: -3px; scroll-snap-type: x proximity; }
.cw-row::-webkit-scrollbar { display: none; }
.cw-card { flex: 0 0 290px; box-sizing: border-box; min-width: 0; display: grid; grid-template-columns: 64px minmax(0, 1fr); grid-template-rows: 1fr auto auto; gap: 0 var(--jz-gap-m); padding: var(--jz-gap-s); background: var(--jz-surface); border: 1px solid var(--jz-border); border-radius: var(--jz-radius-m); cursor: pointer; scroll-snap-align: start; }
.cw-card:hover { border-color: var(--jz-border-strong); background: var(--jz-surface-2); }
.cw-card .poster-wrap { grid-column: 1; grid-row: 1 / 4; border-radius: var(--jz-radius-s); overflow: hidden; background: var(--jz-surface-2); }
.cw-card .poster-play { width: 44px; height: 44px; }
.cw-no-poster { width: 100%; aspect-ratio: 2/3; display: flex; align-items: center; justify-content: center; color: var(--jz-text-faint); font-size: 1.5rem; }
.cw-bar { position: absolute; inset-inline: 0; bottom: 0; height: 3px; background: var(--jz-overlay-soft); }
.cw-bar-in { height: 100%; background: var(--jz-accent); }
.cw-name { grid-column: 2; grid-row: 1; align-self: center; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; font-size: var(--jz-font-m); line-height: 1.5; font-weight: 500; }
.cw-year { color: var(--jz-text-dim); font-size: var(--jz-font-s); margin-left: 4px; font-weight: 400; }
.cw-ep { grid-column: 2; grid-row: 2; color: var(--jz-text-dim); font-size: var(--jz-font-s); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cw-progress-text { grid-column: 2; grid-row: 3; padding-top: var(--jz-gap-xs); font-size: var(--jz-font-s); color: var(--jz-text-dim); font-variant-numeric: tabular-nums; }
.cw-card:focus-visible { outline: 2px solid var(--jz-link); outline-offset: 2px; }
.cw-nav { position: absolute; top: 50%; transform: translateY(-50%); z-index: 3; width: 36px; height: 52px; }
.cw-nav.left { left: 0; }.cw-nav.right { right: 0; }
.cw-fade { display: none; }
@media (max-width: 700px) {
  .cw { margin-top: var(--jz-gap-m); }
  .cw-head { margin-bottom: var(--jz-gap-xs); }
  .cw-card { flex-basis: 246px; grid-template-columns: 48px minmax(0, 1fr); gap: 0 var(--jz-gap-s); }
  .cw-row { gap: var(--jz-gap-s); }
  .cw-nav { display: none; }
}
@media (hover: none) { .cw-nav { display: none; } }
@media (prefers-reduced-motion: reduce) { .cw-row { scroll-behavior: auto; } }
</style>
