<template>
  <section v-if="items.length || allMode || error || (settled && hasCatalogue)" class="cw" aria-label="最近播放">
    <div class="cw-head">
      <h2 v-if="visibleItems.length || allMode || error">{{ allMode ? '最近播放' : '继续观看' }}</h2>
      <JzButton class="cw-toggle" variant="ghost" size="compact" :aria-pressed="allMode" @click="toggleAll" icon="history">
        {{ allMode ? '只看未看完' : '全部最近播放' }}
      </JzButton>
    </div>
    <p v-if="error" class="cw-message" role="status">最近播放读取失败
      <JzButton variant="ghost" size="compact" @click="reload">重试</JzButton>
    </p>
    <p v-else-if="allMode && !items.length && !loading" class="cw-message">暂无最近播放记录</p>
    <div v-if="visibleItems.length" class="cw-strip">
      <JzButton v-if="canLeft" class="cw-nav left" icon-only aria-label="向左滚动" @click="scrollByDir(-1)"><AppIcon name="chevron-left" /></JzButton>
      <div ref="rowRef" class="cw-row" @scroll="updateScroll">
        <article v-for="m in visibleItems" :key="m.progress.version_id" class="cw-card">
          <router-link class="cw-link" :data-browse-focus="'recent-' + m.progress.version_id"
            :to="(kind === 'tv' ? '/tv/' : '/m/') + (m.show_id || m.id)">
            <div class="cw-art">
              <img v-if="m.poster_path && !failedPosters.has(m.poster_path)" :src="posterUrl(m.poster_path)"
                alt="" loading="lazy" @error="failedPosters.add(m.poster_path)" />
              <ArtworkPlaceholder v-else kind="poster" :label="m.title" />
            </div>
            <div class="cw-info">
              <span class="cw-name">{{ m.title }}<span v-if="m.version_count > 1" class="cw-versions"> ×{{ m.version_count }}</span></span>
              <span v-if="m.subtitle" class="cw-ep">{{ m.subtitle }}</span>
              <span class="cw-progress-text">{{ recentProgressLabel(m) }}</span>
              <span v-if="canResumeRecent(m) && m.progress.duration > 0" class="cw-bar" aria-hidden="true">
                <span :style="{ width: progressWidth(m.progress) }"></span>
              </span>
            </div>
          </router-link>
          <JzButton v-if="!m.missing" class="cw-play" icon-only variant="ghost" :data-version="m.progress.version_id"
            :aria-label="(canResumeRecent(m) ? '继续播放 ' : '播放 ') + m.title + (m.subtitle ? ' ' + m.subtitle : '')"
            @click="$emit('resume', m)"><PlayerIcon name="play" :size="18" /></JzButton>
        </article>
      </div>
      <JzButton v-if="canRight" class="cw-nav right" icon-only aria-label="向右滚动" @click="scrollByDir(1)"><AppIcon name="chevron-right" /></JzButton>
    </div>
  </section>
</template>
<script setup>
import { computed, nextTick, onUnmounted, reactive, ref, watch } from 'vue'
import { api, posterUrl } from '../api.js'
import { canResumeRecent, canScroll, loadRecentAll, progressWidth, recentProgressLabel, saveRecentAll, scrollStep } from '../recentPlayed.js'
import ArtworkPlaceholder from './ArtworkPlaceholder.vue'
import AppIcon from './AppIcon.vue'
import JzButton from './JzButton.vue'
import PlayerIcon from './PlayerIcon.vue'

const props = defineProps({
  mediaLibraryId: { type: Number, default: null },
  kind: { type: String, default: 'movie' },
  featuredVersion: { type: Number, default: null },
  hasCatalogue: Boolean,
})
defineEmits(['resume'])

const items = ref([])
const loading = ref(false)
const settled = ref(false)
const error = ref(false)
const allMode = ref(loadRecentAll(localStorage))
const rowRef = ref(null)
const canLeft = ref(false)
const canRight = ref(false)
const failedPosters = reactive(new Set())
const featuredItem = computed(() => items.value.find(canResumeRecent) || null)
const visibleItems = computed(() => allMode.value ? items.value
  : items.value.filter(item => canResumeRecent(item) && item.progress.version_id !== props.featuredVersion))
const hasUnfinished = computed(() => !!featuredItem.value)
let seq = 0
let controller
let pending
function reload() {
  pending = fetchRecent()
  return pending
}
async function fetchRecent() {
  const current = ++seq
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  error.value = false
  try {
    const p = new URLSearchParams({ limit: '20' })
    if (allMode.value) p.set('include_finished', 'true')
    if (props.mediaLibraryId != null) p.set('media_library', String(props.mediaLibraryId))
    const base = props.kind === 'tv' ? '/api/tv/recent-played' : '/api/movies/recent-played'
    const result = await api(base + '?' + p, { signal: controller.signal })
    if (current === seq) items.value = (result?.items || []).filter(item => item.progress?.version_id)
  } catch {
    if (current === seq) error.value = true
  } finally {
    if (current === seq) {
      loading.value = false
      settled.value = true
    }
  }
}
function toggleAll() {
  allMode.value = !allMode.value
  saveRecentAll(localStorage, allMode.value)
  reload()
}
function updateScroll() {
  const el = rowRef.value
  const bounds = canScroll(el || {})
  canLeft.value = bounds.left
  canRight.value = bounds.right
}
function scrollByDir(dir) {
  const el = rowRef.value
  if (el) el.scrollBy({ left: dir * scrollStep(el.clientWidth) })
}
watch([() => props.mediaLibraryId, () => props.kind], () => {
  items.value = []
  failedPosters.clear()
  settled.value = false
  reload()
}, { immediate: true, flush: 'sync' })
watch([rowRef, visibleItems], async ([el], _previous, onCleanup) => {
  const observer = el && typeof ResizeObserver !== 'undefined' ? new ResizeObserver(updateScroll) : null
  if (observer) observer.observe(el)
  window.addEventListener('resize', updateScroll)
  onCleanup(() => { observer?.disconnect(); window.removeEventListener('resize', updateScroll) })
  await nextTick()
  updateScroll()
}, { flush: 'post' })
onUnmounted(() => { seq++; controller?.abort() })
function focusResume() {
  const item = visibleItems.value.find(canResumeRecent)
  if (item) rowRef.value?.querySelector(`[data-version="${item.progress.version_id}"]`)?.focus()
}
defineExpose({ reload, ready: () => pending, settled, featuredItem, hasUnfinished, focusResume })
</script>
<style scoped>
.cw { margin: 0 0 clamp(18px, 2vw, 30px); }
.cw-head { display: flex; flex-wrap: wrap; align-items: center; gap: var(--jz-gap-s); margin-bottom: var(--jz-gap-s); }
.cw-head h2 { margin: 0; font-size: clamp(1.05rem, 1.6vw, 1.5rem); line-height: 1.3; }
.cw-toggle { margin-left: auto; }
.cw-strip { position: relative; }
.cw-row { display: flex; gap: clamp(16px, 2vw, 32px); overflow-x: auto; scrollbar-width: none; scroll-snap-type: x proximity; scroll-behavior: smooth; padding: 4px; margin: -4px; }
.cw-row::-webkit-scrollbar { display: none; }
.cw-card { position: relative; flex: 0 0 clamp(300px, 32vw, 500px); min-width: 0; scroll-snap-align: start; }
.cw-link { display: grid; grid-template-columns: 35% minmax(0, 1fr); align-items: center; gap: var(--jz-gap-l); min-height: 86px; border-radius: var(--jz-radius-s); color: var(--jz-text); text-decoration: none; }
.cw-art { overflow: hidden; border-radius: var(--jz-radius-s); aspect-ratio: 16/9; background: var(--jz-surface-2); }
.cw-art img, .cw-art .artwork-placeholder { display: block; width: 100%; height: 100%; object-fit: cover; object-position: 50% 15%; }
.cw-art .artwork-placeholder { display: flex; }
.cw-info { display: grid; gap: 4px; min-width: 0; padding-right: 44px; }
.cw-name, .cw-ep, .cw-progress-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cw-name { font-size: var(--jz-font-body); font-weight: 600; }
.cw-ep, .cw-versions, .cw-progress-text { font-size: var(--jz-font-s); font-weight: 400; color: var(--jz-text-dim); }
.cw-bar { display: block; margin-top: 4px; height: 3px; background: var(--jz-border-strong); border-radius: 2px; overflow: hidden; }
.cw-bar > span { display: block; height: 100%; background: var(--jz-accent); }
.cw-play { position: absolute; inset-inline-end: 0; top: 50%; transform: translateY(-50%); width: 44px; height: 44px; min-height: 44px; border-radius: 50%; color: var(--jz-text); }
.cw-link:hover .cw-name { text-decoration: underline; }
.cw-link:focus-visible { outline: 2px solid var(--jz-link); outline-offset: 2px; }
.cw-nav { position: absolute; z-index: 1; top: 50%; transform: translateY(-50%); height: 44px; }
.cw-nav.left { left: 0; } .cw-nav.right { right: 0; }
.cw-message { color: var(--jz-text-dim); font-size: var(--jz-font-m); }
@media (max-width: 1023px) {
  .cw-card { flex-basis: min(330px, 85%); }
  .cw-link { grid-template-columns: 56px minmax(0, 1fr); min-height: 78px; gap: 12px; }
  .cw-art { aspect-ratio: 2/3; }
}
@media (max-width: 700px) {
  .cw { margin-bottom: var(--jz-gap-l); }
  .cw-row { gap: 16px; }
  .cw-card { flex-basis: calc(100% - 52px); }
  .cw-card:only-child { flex-basis: min(345px, 100%); }
  .cw-art { aspect-ratio: 3/4; }
  .cw-name { font-size: var(--jz-font-m); }
  .cw-nav { display: none; }
}
@media (min-width: 550px) and (max-width: 700px) {
  .cw-row:has(> :nth-child(3)) .cw-card { flex-basis: calc((100% - 68px) / 2); }
}
@media (hover: none) { .cw-nav { display: none; } }
@media (prefers-reduced-motion: reduce) { .cw-row { scroll-behavior: auto; } }
</style>
