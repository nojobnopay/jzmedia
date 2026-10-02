<template>
  <section v-if="items.length" class="cw">
    <div class="cw-head">
      <h3>
        <span class="cw-ico" aria-hidden="true"><PlayerIcon name="play" :size="14" /></span>
        继续观看 <span class="cw-count">{{ items.length }}</span>
        <span class="cw-sub">{{ allMode ? '最近播放 · 含已看完' : '最近播放 · 未看完' }}</span>
      </h3>
      <button class="cw-toggle" @click="toggleAll">{{ allMode ? '只看未看完' : '全部最近播放' }}</button>
    </div>
    <div class="cw-strip">
      <button v-if="canLeft" class="cw-nav left" aria-label="向左滚动" @click="scrollByDir(-1)">‹</button>
      <div ref="rowRef" class="cw-row" @scroll="onScroll">
        <div v-for="m in items" :key="m.id" class="cw-card" role="link" tabindex="0"
          :title="m.added_at ? ('入库 ' + fmtDate(m.added_at)) : ''" @click="$emit('open', m.id)" @keydown.enter.self="$emit('open', m.id)">
          <div class="poster-wrap">
            <img v-if="m.poster_path" :src="posterUrl(m.poster_path)" loading="lazy"
              :alt="(m.title || '影片') + ' 海报'" />
            <div v-else class="cw-no-poster" aria-hidden="true">{{ (m.title || '?').slice(0, 1) }}</div>
            <span v-if="m.watched" class="cw-done">✓已看</span>
            <span v-else class="cw-left">{{ fmtRemaining(m.progress && m.progress.remaining_sec) }}</span>
            <div v-if="!m.watched" class="cw-bar" aria-hidden="true">
              <div class="cw-bar-in" :style="{ width: progressWidth(m.progress) }"></div>
            </div>
            <button class="poster-play" :aria-label="'继续播放 ' + (m.title || '')" title="继续播放"
              @click.stop="$emit('resume', m)"><PlayerIcon name="play" :size="24" /></button>
          </div>
          <div class="cw-name" :title="m.title">{{ m.title }}<span v-if="m.year" class="cw-year">({{ m.year }})</span><span v-if="m.version_count > 1" class="cw-year">×{{ m.version_count }}</span></div>
          <div v-if="m.subtitle" class="cw-ep" :title="m.subtitle">{{ m.subtitle }}</div>
          <div class="cw-progress-text">{{ m.watched ? '✓ 已看完' : fmtRemaining(m.progress && m.progress.remaining_sec) }}</div>
        </div>
      </div>
      <button v-if="canRight" class="cw-nav right" aria-label="向右滚动" @click="scrollByDir(1)">›</button>
      <span v-if="canLeft" class="cw-fade left" aria-hidden="true"></span>
      <span v-if="canRight" class="cw-fade right" aria-hidden="true"></span>
    </div>
  </section>
</template>
<script setup>
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
  el.scrollBy({ left: dir * scrollStep(el.clientWidth), behavior: 'smooth' })
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
.cw {
  margin: 6px 12px 0; padding: 10px 12px 12px; border-radius: 10px;
  border: 1px solid #2f2f2f; border-left: 3px solid #e50914;
  background: linear-gradient(90deg, #241618 0%, #1b1b1b 45%, #181818 100%);
  box-shadow: 0 4px 16px rgba(0,0,0,.35);
}
.cw-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; }
.cw-head h3 { margin: 0; font-size: 1.0625rem; color: #eee; display: flex; align-items: center; gap: 6px; }
.cw-ico {
  width: 20px; height: 20px; border-radius: 50%; background: #e50914; color: #fff;
  font-size: 0.625rem; display: inline-flex; align-items: center; justify-content: center;
  box-sizing: border-box;
}
.cw-count { color: #e50914; font-size: 0.875rem; font-weight: bold; }
.cw-sub { color: #777; font-size: 0.75rem; font-weight: normal; }
.cw-toggle { margin-left: auto; border: 1px solid #444; background: transparent; color: #aaa; font-size: 0.75rem; padding: 3px 10px; border-radius: 999px; }
.cw-toggle:hover { color: #ff8a8a; border-color: #e50914; }
.cw-strip { position: relative; }
.cw-row { display: flex; gap: 12px; overflow-x: auto; scroll-behavior: smooth; scrollbar-width: none; padding: 4px 2px 6px; margin: -4px -2px -6px; }
.cw-row::-webkit-scrollbar { display: none; }
.cw-card {
  flex: 0 0 160px; width: 160px; cursor: pointer; padding: 5px;
  background: #1f1f1f; border: 1px solid #333; border-radius: 10px;
  box-shadow: 0 1px 4px rgba(0,0,0,.4);
  transition: transform .15s, border-color .15s;
}
.cw-card:hover { transform: translateY(-2px); border-color: #e50914; }
.cw-card .poster-wrap { border-radius: 7px; overflow: hidden; background: #222; }
.cw-no-poster { width: 100%; aspect-ratio: 2/3; display: flex; align-items: center; justify-content: center; background: #242424; color: #555; font-size: 2.5rem; font-weight: bold; user-select: none; }
.cw-bar { position: absolute; left: 0; right: 0; bottom: 0; height: 4px; background: rgba(0,0,0,.55); }
.cw-bar-in { height: 100%; background: #e50914; transition: width .2s; }
.cw-left, .cw-done { position: absolute; top: 6px; left: 6px; font-size: 0.75rem; padding: 2px 8px; border-radius: 999px; background: rgba(0,0,0,.72); }
.cw-left { color: #ddd; }
.cw-done { color: #7ed321; }
.cw-name { padding: 6px 2px 0; font-size: 0.8125rem; color: #ddd; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cw-year { color: #888; font-size: 0.75rem; margin-left: 4px; }
.cw-ep { padding: 1px 2px 0; font-size: 0.6875rem; color: #9ecfff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cw-progress-text { display: none; }
.cw-card:focus-visible { outline: 2px solid var(--jz-link); outline-offset: 2px; }
.cw-nav {
  position: absolute; top: 50%; transform: translateY(-50%); z-index: 3;
  width: 30px; height: 52px; border-radius: 8px; cursor: pointer;
  background: rgba(0,0,0,.62); border: 1px solid #444; color: #fff; font-size: 1.25rem; line-height: 1;
  display: flex; align-items: center; justify-content: center;
}
.cw-nav:hover { border-color: #e50914; color: #ff8a8a; }
.cw-nav.left { left: 4px; }
.cw-nav.right { right: 4px; }
.cw-fade { position: absolute; top: 0; bottom: 0; width: 26px; z-index: 2; pointer-events: none; }
.cw-fade.left { left: 0; background: linear-gradient(90deg, rgba(34,22,24,.95), rgba(34,22,24,0)); }
.cw-fade.right { right: 0; background: linear-gradient(270deg, rgba(24,24,24,.95), rgba(24,24,24,0)); }
@media (hover: none) { .cw-nav { display: none; } }
</style>

<style scoped>
@media (max-width: 700px) {
  .cw { margin: 0; padding: var(--jz-gap-s); border-radius: var(--jz-radius-m); box-shadow: none; }
  .cw-head { align-items: center; gap: var(--jz-gap-xs); margin-bottom: var(--jz-gap-xs); }
  .cw-head h3 { font-size: var(--jz-font-l); gap: var(--jz-gap-xs); white-space: nowrap; }
  .cw-ico, .cw-sub { display: none; }
  .cw-toggle { margin-left: auto; min-height: 40px; padding-inline: var(--jz-gap-s); white-space: nowrap; }
  .cw-row { gap: var(--jz-gap-s); }
  .cw-card { box-sizing: border-box; flex: 0 0 224px; width: 224px; display: grid; grid-template-columns: 64px minmax(0, 1fr); grid-template-rows: 1fr auto auto; gap: 0 var(--jz-gap-s); padding: var(--jz-gap-xs); box-shadow: none; }
  .cw-card .poster-wrap { grid-column: 1; grid-row: 1 / 4; }
  .cw-name { grid-column: 2; grid-row: 1; align-self: center; padding: 0; white-space: normal; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; font-size: var(--jz-font-m); line-height: 1.5; }
  .cw-ep { grid-column: 2; grid-row: 2; padding: 0; font-size: var(--jz-font-s); }
  .cw-progress-text { display: block; grid-column: 2; grid-row: 3; padding-top: var(--jz-gap-xs); font-size: var(--jz-font-s); color: var(--jz-text-dim); }
  .cw-left, .cw-done { display: none; }
}
</style>
